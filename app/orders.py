"""Purchase orders and the documents sent against them.

=============================================================================
WHY ORDERS OUTLIVE SESSIONS BY A LOT
=============================================================================
A punchout session lasts an hour (`sessions.py`) because it represents someone
shopping, and an abandoned cart is not worth storing.

An order represents a *flow*: PO in, confirmation out, ship notice out, invoice
out — with a human reading the buyer's response to each one and changing their
configuration in between. That is a day's work, sometimes a week's, and losing
it halfway through would be worse than not offering it. Seven days.

=============================================================================
THREE ROW SHAPES, AND WHY THERE IS A DUPLICATE
=============================================================================
    ORDER#<tenant>#<ref>  META          the order, including the raw document
    ORDER#<tenant>#<ref>  DOC#<id>      one document sent against it
    ORDERS#<tenant>       ORDER#<ref>   a summary row, for the list screen

The third duplicates a few fields from the first, on purpose. Without it,
listing an account's orders means a Scan or a GSI; with it, both access
patterns are a single Query and the cost is writing ~200 bytes twice. This is
the same pointer-row trade `tenants.py` makes, for the same reason.

`<ref>` is minted as `<epoch>-<token>` so that lexical sort IS chronological
sort, and the list screen needs no sorting logic at all.

=============================================================================
THE RAW DOCUMENT IS KEPT VERBATIM
=============================================================================
Not reserialised, not pretty-printed, not normalised. The entire value of this
sandbox is telling someone what their system actually sent, and a stored copy
that has been through a round trip is evidence of what OUR parser produced.
`differ.py` exists because that distinction matters.

DynamoDB's 400KB item limit is the one constraint, so oversized documents are
truncated with a marker rather than rejected — a 500KB order is still worth
showing the first 300KB of.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Optional, Protocol

#: Seven days. See the module docstring.
ORDER_TTL_SECONDS = 7 * 24 * 3600

#: Leaves room inside DynamoDB's 400KB item ceiling for everything else on the
#: row. Anything larger is stored truncated with a visible marker.
MAX_STORED_DOCUMENT = 300 * 1024

TRUNCATION_MARKER = "\n<!-- TRUNCATED BY PUNCHOUT SANDBOX AT {n} BYTES -->"


def new_ref() -> str:
    """Time-sortable, unguessable. The time prefix makes the list screen free;
    the token stops one account enumerating another's order URLs by counting."""
    return f"{int(time.time())}-{secrets.token_urlsafe(8)}"


def clamp(document: str) -> str:
    if len(document) <= MAX_STORED_DOCUMENT:
        return document
    return document[:MAX_STORED_DOCUMENT] + TRUNCATION_MARKER.format(
        n=MAX_STORED_DOCUMENT)


@dataclass
class SentDocument:
    """One document generated against an order, and what happened to it."""
    doc_id: str
    #: ConfirmationRequest | ShipNoticeRequest | InvoiceDetailRequest
    kind: str
    payload_id: str
    created_at: float
    xml: str
    #: Absent until someone presses send. A generated-but-undelivered document
    #: is a normal state — you can build one to look at it.
    delivered: Optional[bool] = None
    endpoint: str = ""
    http_status: Optional[int] = None
    cxml_status: str = ""
    response_excerpt: str = ""
    failure_reason: str = ""
    observations: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def state(self) -> str:
        if self.delivered is None:
            return "generated"
        if not self.delivered:
            return "failed"
        if self.cxml_status and not self.cxml_status.startswith("2"):
            return "rejected"
        return "delivered"


@dataclass
class OrderRecord:
    ref: str
    tenant_id: str
    order_id: str
    payload_id: str
    buyer_identity: str
    currency: str
    total: str
    line_count: int
    received_at: float
    raw: str
    expires_at: float = 0.0
    conformant: bool = True
    error_count: int = 0
    advisory_count: int = 0
    observations: list[str] = field(default_factory=list)
    documents: list[SentDocument] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = self.received_at + ORDER_TTL_SECONDS

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def document(self, doc_id: str) -> Optional[SentDocument]:
        return next((d for d in self.documents if d.doc_id == doc_id), None)


class OrderStore(Protocol):
    def get(self, tenant_id: str, ref: str) -> Optional[OrderRecord]: ...
    def put(self, order: OrderRecord) -> None: ...
    def add_document(self, order: OrderRecord, doc: SentDocument) -> None: ...
    def update_document(self, order: OrderRecord, doc: SentDocument) -> None: ...
    def recent(self, tenant_id: str, limit: int = 25) -> list[OrderRecord]: ...


class MemoryOrders:
    """Local development only, matching `sessions.MemoryStore`'s posture: not
    a fallback, and never selected when a table is configured."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], OrderRecord] = {}

    def get(self, tenant_id, ref):
        found = self._items.get((tenant_id, ref))
        return None if found is None or found.expired else found

    def put(self, order):
        self._items[(order.tenant_id, order.ref)] = order

    def add_document(self, order, doc):
        order.documents.append(doc)
        self.put(order)

    def update_document(self, order, doc):
        self.put(order)

    def recent(self, tenant_id, limit=25):
        live = [o for k, o in self._items.items()
                if k[0] == tenant_id and not o.expired]
        return sorted(live, key=lambda o: o.received_at, reverse=True)[:limit]


def _no_floats(value):
    """Recursively replace floats with Decimal before a DynamoDB write.

    boto3 refuses floats outright — "Float types are not supported. Use Decimal
    types instead" — and it refuses them at call time, deep inside the
    serialiser, as a 500 rather than anything a caller can act on.

    This exists because that is exactly how it was found: `MemoryOrders`
    accepts a float happily, so every test passed and the first document
    generated against the real table 502ed. Converting centrally means a field
    added later cannot reintroduce the bug, which a fix at the one call site
    would not have prevented."""
    if isinstance(value, float):
        # str() first: Decimal(0.1) captures the binary representation, which
        # is not the number anyone wrote down.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _no_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_no_floats(v) for v in value]
    return value


class DynamoOrders:
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
    def _pk(tenant_id: str, ref: str) -> str:
        return f"ORDER#{tenant_id}#{ref}"

    def get(self, tenant_id, ref):
        from boto3.dynamodb.conditions import Key
        rows = self.table.query(
            KeyConditionExpression=Key("pk").eq(self._pk(tenant_id, ref)))["Items"]
        meta = next((r for r in rows if r["sk"] == "META"), None)
        if meta is None:
            return None
        order = self._hydrate(meta)
        if order is None:
            return None
        # Documents are separate rows so that sending one is a single small
        # write rather than a rewrite of the whole order — an order carrying
        # three invoices would otherwise be rewritten in full on every send.
        order.documents = sorted(
            (self._hydrate_doc(r) for r in rows if r["sk"].startswith("DOC#")),
            key=lambda d: d.created_at)
        return order

    @staticmethod
    def _hydrate(item: dict) -> Optional[OrderRecord]:
        order = OrderRecord(
            ref=item["ref"], tenant_id=item["tenant_id"],
            order_id=item.get("order_id", ""),
            payload_id=item.get("payload_id", ""),
            buyer_identity=item.get("buyer_identity", ""),
            currency=item.get("currency", ""),
            total=item.get("total", ""),
            line_count=int(item.get("line_count", 0)),
            received_at=float(item.get("received_at", 0)),
            raw=item.get("raw", ""),
            expires_at=float(item.get("expires_at", 0)),
            conformant=bool(item.get("conformant", True)),
            error_count=int(item.get("error_count", 0)),
            advisory_count=int(item.get("advisory_count", 0)),
            observations=list(item.get("observations", [])),
        )
        return None if order.expired else order

    @staticmethod
    def _hydrate_doc(item: dict) -> SentDocument:
        raw = dict(item.get("doc", {}))
        raw["created_at"] = float(raw.get("created_at", 0))
        raw["duration_ms"] = int(raw.get("duration_ms", 0))
        if raw.get("http_status") is not None:
            raw["http_status"] = int(raw["http_status"])
        return SentDocument(**raw)

    def _summary_row(self, order: OrderRecord) -> dict:
        return {
            "pk": f"ORDERS#{order.tenant_id}", "sk": f"ORDER#{order.ref}",
            "ref": order.ref, "tenant_id": order.tenant_id,
            "order_id": order.order_id, "payload_id": order.payload_id,
            "buyer_identity": order.buyer_identity, "currency": order.currency,
            "total": order.total, "line_count": order.line_count,
            "received_at": int(order.received_at),
            "expires_at": int(order.expires_at),
            "conformant": order.conformant,
            "error_count": order.error_count,
            "advisory_count": order.advisory_count,
            # The summary row deliberately carries no `raw` — the list screen
            # does not show documents, and duplicating 300KB per order to save
            # one lookup would be the wrong trade by three orders of magnitude.
            "raw": "",
        }

    def put(self, order):
        item = self._summary_row(order)
        self.table.put_item(Item=_no_floats({
            **item,
            "pk": self._pk(order.tenant_id, order.ref), "sk": "META",
            "raw": clamp(order.raw),
            "observations": order.observations,
        }))
        self.table.put_item(Item=_no_floats(item))

    def add_document(self, order, doc):
        self.update_document(order, doc)
        order.documents.append(doc)

    def update_document(self, order, doc):
        payload = asdict(doc)
        payload["xml"] = clamp(payload["xml"])
        payload["response_excerpt"] = payload["response_excerpt"][:4000]
        self.table.put_item(Item=_no_floats({
            "pk": self._pk(order.tenant_id, order.ref),
            "sk": f"DOC#{doc.doc_id}",
            "doc": payload,
            "expires_at": int(order.expires_at),
        }))

    def recent(self, tenant_id, limit=25):
        from boto3.dynamodb.conditions import Key
        rows = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"ORDERS#{tenant_id}"),
            # Refs are time-prefixed, so descending sk order IS newest-first.
            ScanIndexForward=False, Limit=limit)["Items"]
        found = [self._hydrate(r) for r in rows]
        return [o for o in found if o is not None]


_store: Optional[OrderStore] = None


def store() -> OrderStore:
    global _store
    if _store is None:
        table = os.environ.get("SANDBOX_TABLE")
        _store = DynamoOrders(table) if table else MemoryOrders()
    return _store


def reset_store(replacement: Optional[OrderStore] = None) -> None:
    global _store
    _store = replacement
