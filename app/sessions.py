"""Punchout session storage.

=============================================================================
WHY THIS MODULE EXISTS — THE BUG IT FIXES
=============================================================================
`handler.py` originally kept sessions in a module-level dict. That is correct
for a single long-lived process and **wrong for Lambda**, where every cold
start gets a fresh dict and concurrent invocations do not share one. The
symptom is nasty precisely because it is intermittent: a session works while
you click through quickly against one warm container, then vanishes when
Lambda scales out or recycles. A URL that behaves that way is worse than one
that is plainly offline, because it looks finished.

So sessions live in DynamoDB, in the table `data_stack.py` already provisions.

=============================================================================
THE IN-MEMORY BACKEND IS NOT A FALLBACK, IT IS FOR LOCAL DEV
=============================================================================
`SANDBOX_TABLE` unset selects `MemoryStore`. That is deliberate and narrow:
it lets `python -m app.handler` run with no AWS credentials at all, which is
how the storefront gets developed.

It is NOT a resilience feature. If the table is configured but unreachable,
operations raise rather than silently degrading to memory — a sandbox that
quietly forgot every session while reporting success would be indistinguishable
from the bug this module was written to fix.

=============================================================================
TTL DOES THE EXPIRY, AND THAT IS LOAD-BEARING
=============================================================================
Every session carries `expires_at`, registered as the table's TTL attribute.
Punchout sessions are short-lived by nature — the spec expects the StartPage
URL to be valid "for only a limited amount of time" — so an hour is generous.

TTL deletes cost nothing. A sweeper doing `DeleteItem` would consume write
capacity to do the same job worse. It also means abandoned carts (the common
case: a user opens the storefront and closes the tab) cost us nothing to
store, which is half the answer to BRIEF.md §3's abuse worry.

Note that DynamoDB TTL deletion is *eventual* — items can linger for up to 48
hours past their timestamp. `get` therefore checks `expires_at` itself rather
than trusting the row's absence to mean expiry. A session that is past its
time but not yet swept must not be usable.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol

#: One hour. Long enough for a human to shop, short enough that an abandoned
#: cart is not our problem for long.
DEFAULT_TTL_SECONDS = 3600


@dataclass
class Session:
    """A punchout session.

    `buyer_cookie` is the capability that binds a returned cart to the
    requisition that opened it. It is minted by the BUYER and echoed back
    unchanged — we never generate it and never alter it."""

    session_id: str
    buyer_name: str = "your procurement system"
    protocol: str = "cXML"
    buyer_cookie: str = ""
    return_url: str = ""
    operation: str = "create"
    cart: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = self.started_at + DEFAULT_TTL_SECONDS

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def seconds_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def return_url_display(self) -> str:
        return self.return_url or "(not set)"


class SessionStore(Protocol):
    def get(self, session_id: str) -> Optional[Session]: ...
    def put(self, session: Session) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def recent(self, limit: int = 25) -> list[Session]: ...


class MemoryStore:
    """Local development only. See the module docstring."""

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}

    def get(self, session_id: str) -> Optional[Session]:
        found = self._items.get(session_id)
        if found is None or found.expired:
            return None
        return found

    def put(self, session: Session) -> None:
        self._items[session.session_id] = session

    def delete(self, session_id: str) -> None:
        self._items.pop(session_id, None)

    def recent(self, limit: int = 25) -> list[Session]:
        live = [s for s in self._items.values() if not s.expired]
        return sorted(live, key=lambda s: s.started_at, reverse=True)[:limit]


class DynamoStore:
    """DynamoDB-backed sessions.

    The client is created lazily and cached on the instance: constructing a
    boto3 client costs ~100ms, and doing it at import time would add that to
    every cold start including ones that never touch a session (a static asset
    request, say)."""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        self._table = None

    @property
    def table(self):
        if self._table is None:
            import boto3  # imported here, not at module scope — see docstring
            self._table = boto3.resource("dynamodb").Table(self.table_name)
        return self._table

    @staticmethod
    def _key(session_id: str) -> dict:
        return {"pk": f"SESSION#{session_id}", "sk": "SESSION"}

    def get(self, session_id: str) -> Optional[Session]:
        response = self.table.get_item(Key=self._key(session_id))
        item = response.get("Item")
        if item is None:
            return None
        session = Session(
            session_id=session_id,
            buyer_name=item.get("buyer_name", ""),
            protocol=item.get("protocol", "cXML"),
            buyer_cookie=item.get("buyer_cookie", ""),
            return_url=item.get("return_url", ""),
            operation=item.get("operation", "create"),
            # DynamoDB returns numbers as Decimal; quantities are integers and
            # the rest of the app treats them as such, so convert at the
            # boundary rather than letting Decimal leak into the templates.
            cart={k: int(v) for k, v in (item.get("cart") or {}).items()},
            started_at=float(item.get("started_at", 0)),
            expires_at=float(item.get("expires_at", 0)),
        )
        # TTL deletion is eventual — up to 48 hours late. A session past its
        # time but not yet swept must not be usable.
        if session.expired:
            return None
        return session

    def put(self, session: Session) -> None:
        self.table.put_item(Item={
            **self._key(session.session_id),
            # GSI1 powers the "recent sessions" console screen. A constant
            # partition key is fine at this volume and would be a hot-partition
            # problem at a thousand times the traffic — noted rather than
            # prematurely sharded.
            "gsi1pk": "SESSIONS",
            "gsi1sk": f"{session.started_at:.6f}",
            "buyer_name": session.buyer_name,
            "protocol": session.protocol,
            "buyer_cookie": session.buyer_cookie,
            "return_url": session.return_url,
            "operation": session.operation,
            "cart": session.cart,
            "started_at": int(session.started_at),
            # TTL wants an epoch-second integer. A float or an ISO string is
            # silently ignored by DynamoDB, and the row then never expires —
            # a failure mode with no error message at all.
            "expires_at": int(session.expires_at),
        })

    def delete(self, session_id: str) -> None:
        self.table.delete_item(Key=self._key(session_id))

    def recent(self, limit: int = 25) -> list[Session]:
        response = self.table.query(
            IndexName="gsi1",
            KeyConditionExpression="gsi1pk = :p",
            ExpressionAttributeValues={":p": "SESSIONS"},
            ScanIndexForward=False,
            Limit=limit,
        )
        out: list[Session] = []
        for item in response.get("Items", []):
            session = Session(
                session_id=item["pk"].split("#", 1)[1],
                buyer_name=item.get("buyer_name", ""),
                protocol=item.get("protocol", "cXML"),
                buyer_cookie=item.get("buyer_cookie", ""),
                return_url=item.get("return_url", ""),
                operation=item.get("operation", "create"),
                cart={k: int(v) for k, v in (item.get("cart") or {}).items()},
                started_at=float(item.get("started_at", 0)),
                expires_at=float(item.get("expires_at", 0)),
            )
            if not session.expired:
                out.append(session)
        return out


_store: Optional[SessionStore] = None


def store() -> SessionStore:
    """The process-wide session store, chosen once from the environment."""
    global _store
    if _store is None:
        table = os.environ.get("SANDBOX_TABLE")
        _store = DynamoStore(table) if table else MemoryStore()
    return _store


def reset_store(replacement: Optional[SessionStore] = None) -> None:
    """Testing seam. Not used in production code."""
    global _store
    _store = replacement
