"""Sandbox accounts — the signup gate's storage.

=============================================================================
WHY A GATE AT ALL, ON A FREE TOOL
=============================================================================
BRIEF.md §8 decided this: free, no billing, no SLA, but **signup-gated to
capture leads and cap abuse**. Both halves matter and they pull in different
directions, so it is worth being explicit about the trade.

The lead capture is the entire commercial case. RESEARCH.md §D found the
market is single-digit posts per year — there is no traffic to monetise, so
the value of this service is knowing WHO turned up. A procurement integrator
debugging a punchout at 11pm is precisely the audience that buys the product
this exists to promote.

The abuse cap is the other half. Validation is CPU-bound (`lxml` against a
400KB DTD) and the storefront is public, which BRIEF.md §3 flagged as "an open
invitation to burn compute". An account per user makes rate limiting possible
at all.

=============================================================================
WHAT THE GATE MUST NOT DO
=============================================================================
**It must not break the machine endpoints.** A buyer system POSTing a
`PunchOutSetupRequest` cannot fill in a web form, so gating `/punchout/setup`
behind a browser session would stop the product working entirely.

The resolution is that signup ISSUES CREDENTIALS, and the machine endpoints
authenticate with those. That is not a workaround — it is exactly how real
punchout works: a supplier and a buyer exchange a shared secret out of band
before anything connects. Requiring it makes the sandbox MORE faithful, not
less.

=============================================================================
WHAT WE STORE, AND WHAT WE DELIBERATELY DO NOT
=============================================================================
An email address, an optional company name, and counters. That is all, and the
signup form says so in as many words.

There is no password, because there is nothing to protect: every account sees
the same synthetic catalogue of invented companies. The cookie is the session
and the shared secret is the API credential; neither guards anything private.
Adding a password would imply a security boundary that does not exist and
would oblige us to store a hash we have no use for.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

#: Accounts are kept for a year. Long enough to be useful to someone who comes
#: back next quarter, short enough that we are not holding an email address
#: forever for a tool they used once.
TENANT_TTL_SECONDS = 365 * 24 * 3600

#: Per-account daily ceiling on the expensive operations (DTD validation,
#: punchout setup). Generous for a human debugging an integration; useless to
#: anyone trying to burn our compute.
DAILY_QUOTA = 500

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def valid_email(value: str) -> bool:
    """Deliberately loose. Email validation by regex is famously unwinnable,
    and the only question here is "did they type something shaped like an
    address" — we are not sending mail, so a false accept costs nothing and a
    false reject costs a lead."""
    return bool(_EMAIL.match((value or "").strip()))


@dataclass
class Tenant:
    tenant_id: str                  # opaque; the browser session key
    email: str
    company: str = ""
    #: The identity a buyer system presents. Shaped like an Ariba ANID because
    #: that is what the audience expects to paste into a config field.
    sandbox_id: str = ""
    shared_secret: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    #: Rolling daily counter, reset when `quota_day` changes.
    used_today: int = 0
    quota_day: str = ""

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = self.created_at + TENANT_TTL_SECONDS
        if not self.sandbox_id:
            self.sandbox_id = f"PSB{secrets.randbelow(10**9):09d}"
        if not self.shared_secret:
            self.shared_secret = secrets.token_urlsafe(24)

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def check_quota(self, *, today: str) -> tuple[bool, int]:
        """Returns `(allowed, remaining)` and MUTATES the counter.

        The caller must persist afterwards. Kept as a mutation rather than a
        pure function because the alternative — read, decide, write — invites
        a caller to skip the write and get a free quota."""
        if self.quota_day != today:
            self.quota_day = today
            self.used_today = 0
        if self.used_today >= DAILY_QUOTA:
            return False, 0
        self.used_today += 1
        return True, DAILY_QUOTA - self.used_today


class TenantStore(Protocol):
    def get(self, tenant_id: str) -> Optional[Tenant]: ...
    def by_sandbox_id(self, sandbox_id: str) -> Optional[Tenant]: ...
    def put(self, tenant: Tenant) -> None: ...
    def count(self) -> int: ...


class MemoryTenants:
    """Local development only — see sessions.py for the same argument."""

    def __init__(self) -> None:
        self._items: dict[str, Tenant] = {}

    def get(self, tenant_id):
        found = self._items.get(tenant_id)
        return None if found is None or found.expired else found

    def by_sandbox_id(self, sandbox_id):
        for t in self._items.values():
            if t.sandbox_id == sandbox_id and not t.expired:
                return t
        return None

    def put(self, tenant):
        self._items[tenant.tenant_id] = tenant

    def count(self):
        return len(self._items)


class DynamoTenants:
    """DynamoDB-backed accounts, in the table `data_stack.py` provisions.

    Two access patterns, so two keys: `TENANT#<id>` for the browser session,
    and a `SANDBOXID#<id>` pointer row for the machine endpoints. A GSI would
    also work; a pointer row is cheaper to reason about at this volume and
    needs no index projection decisions."""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        self._table = None

    @property
    def table(self):
        if self._table is None:
            import boto3
            self._table = boto3.resource("dynamodb").Table(self.table_name)
        return self._table

    @staticmethod
    def _key(tenant_id: str) -> dict:
        return {"pk": f"TENANT#{tenant_id}", "sk": "TENANT"}

    def _hydrate(self, item: dict) -> Optional[Tenant]:
        t = Tenant(
            tenant_id=item["pk"].split("#", 1)[1],
            email=item.get("email", ""),
            company=item.get("company", ""),
            sandbox_id=item.get("sandbox_id", ""),
            shared_secret=item.get("shared_secret", ""),
            created_at=float(item.get("created_at", 0)),
            expires_at=float(item.get("expires_at", 0)),
            used_today=int(item.get("used_today", 0)),
            quota_day=item.get("quota_day", ""),
        )
        return None if t.expired else t

    def get(self, tenant_id):
        item = self.table.get_item(Key=self._key(tenant_id)).get("Item")
        return self._hydrate(item) if item else None

    def by_sandbox_id(self, sandbox_id):
        pointer = self.table.get_item(
            Key={"pk": f"SANDBOXID#{sandbox_id}", "sk": "POINTER"}).get("Item")
        return self.get(pointer["tenant_id"]) if pointer else None

    def put(self, tenant):
        self.table.put_item(Item={
            **self._key(tenant.tenant_id),
            "gsi1pk": "TENANTS",
            "gsi1sk": f"{tenant.created_at:.6f}",
            "email": tenant.email,
            "company": tenant.company,
            "sandbox_id": tenant.sandbox_id,
            "shared_secret": tenant.shared_secret,
            "created_at": int(tenant.created_at),
            "expires_at": int(tenant.expires_at),
            "used_today": tenant.used_today,
            "quota_day": tenant.quota_day,
        })
        self.table.put_item(Item={
            "pk": f"SANDBOXID#{tenant.sandbox_id}", "sk": "POINTER",
            "tenant_id": tenant.tenant_id,
            "expires_at": int(tenant.expires_at),
        })

    def count(self):
        # Deliberately not implemented as a Scan. The console shows recent
        # signups from the GSI; a live total would cost a full table read
        # every page view to display a number nobody acts on.
        return -1


_store: Optional[TenantStore] = None


def store() -> TenantStore:
    global _store
    if _store is None:
        table = os.environ.get("SANDBOX_TABLE")
        _store = DynamoTenants(table) if table else MemoryTenants()
    return _store


def reset_store(replacement: Optional[TenantStore] = None) -> None:
    """Testing seam."""
    global _store
    _store = replacement


def verify_secret(presented: Optional[str], expected: str) -> bool:
    """Constant-time. A secret compared with `==` leaks its prefix through
    timing, and the cost of getting it right is one import."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)
