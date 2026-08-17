"""Turning a stored purchase order into the documents that follow it.

*The glue between `orders.py` (what arrived), `cxml/fulfilment.py` and
`cxml/invoice.py` (what we can build), `tax/` (what it costs) and `delivery.py`
(getting it there). Nothing novel happens here — it is deliberately the boring
layer, so that each of those modules stays testable on its own.*

=============================================================================
WHAT GETS DECIDED HERE, AND WHAT DOES NOT
=============================================================================
Decided here: which lines go on a confirmation, what a plausible tracking
number looks like, which jurisdiction an invoice is taxed in.

NOT decided here: any question of cXML conformance. If a document this module
assembles is invalid, that is a bug in the builder it called, and the builders
prove themselves against the real DTD in the tests. Keeping the judgement out
of the glue is what stops "it validates when sent from the order screen but
not from the test" ever being a sentence anyone has to say.

=============================================================================
THE SUPPLIER IS FICTIONAL AND ITS TAX ID IS NOT REAL
=============================================================================
Meridian Supply Co. is invented, as is every other company name in this
sandbox (BRIEF.md §6). The VAT number below is in the correct FORMAT and is
not issued to anybody — which is the right trade for a tool whose documents
will be pasted into test systems that validate format but not registration.
Never make it a real one.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Optional

from . import delivery, orders, telemetry
from .cxml.fulfilment import (Confirmation, ConfirmationLine, Shipment,
                              ShipmentLine, build_confirmation,
                              build_ship_notice, check_confirmation,
                              check_shipment)
from .cxml.invoice import Invoice, InvoiceLine, Party, build_invoice
from .cxml.order import Order, parse_order
from .tax import rates
from .tax.engine import Rounding, TaxableLine, TaxTreatment, calculate, determine
from .orders import OrderRecord, SentDocument

#: The sandbox's own identity in the cXML envelope.
SUPPLIER_IDENTITY = "punchoutsandbox.com"
SUPPLIER_COUNTRY = "GB"

SUPPLIER_PARTY = Party(
    role="remitTo",
    name="Meridian Supply Co.",
    street="1 Trade Park",
    city="Leeds",
    postal_code="LS10 1AB",
    country_code="GB",
    country_name="United Kingdom",
    # Correctly formatted, issued to nobody. See the module docstring.
    tax_id="GB123456789",
    tax_id_domain="vatID",
)


#: The confirmation header types worth offering in a UI, with the plain
#: meaning of each. `requestToPay` and `allDetail` are omitted deliberately —
#: the first is a payments-network flow that has nothing to do with testing a
#: punchout integration, and the second is described in the DTD's own prose as
#: a legacy EDI bridge that causes reconciliation problems. Both are still
#: reachable through the API; neither belongs in the obvious path.
HEADER_TYPES_FOR_UI = [
    ("accept", "Accept everything as ordered"),
    ("detail", "Accept with changes (price, date)"),
    ("backordered", "Backorder every line"),
    ("reject", "Reject the whole order"),
    ("except", "Mixed — accept some, reject or backorder others"),
]


def _now() -> datetime:
    """Timezone-aware, with a numeric offset. cXML forbids `Z`, and a naive
    datetime reaching a builder raises there rather than silently emitting a
    document with no offset — but it is cheaper to never create one."""
    return datetime.now(timezone.utc).astimezone()


def _payload_id() -> str:
    return f"{secrets.token_hex(10)}@punchoutsandbox.com"


def _doc_id(kind: str) -> str:
    """Time-prefixed so a document list sorts chronologically by key alone,
    matching `orders.new_ref()`."""
    return f"{int(_now().timestamp())}-{kind}-{secrets.token_urlsafe(5)}"


def order_from_record(record: OrderRecord):
    """Re-parse the stored raw document back into an `Order`.

    Re-parsed rather than stored parsed, deliberately. The raw document is the
    evidence (see `orders.py`), and deriving everything from it means the
    order screen can never disagree with the bytes it is displaying."""
    from .xml_safe import parse
    return parse_order(parse(record.raw.encode("utf-8")).tree)


# =============================================================================
# Confirmation
# =============================================================================
def build_confirmation_document(
    record: OrderRecord, *, header_type: str = "accept",
    shared_secret: str, buyer_identity: str,
    line_statuses: Optional[dict[int, str]] = None,
    price_changes: Optional[dict[int, D]] = None,
    delivery_days: int = 5,
) -> tuple[SentDocument, list[str]]:
    """Build a `ConfirmationRequest`. Returns `(document, problems)`.

    When `problems` is non-empty nothing was built — the rules that cannot be
    expressed in the DTD were broken, and `check_confirmation` explains which.
    A sandbox that emitted a document it knew a buyer would reject would be
    teaching the wrong thing."""
    order = order_from_record(record)
    statuses = line_statuses or {}
    prices = price_changes or {}
    delivery_date = _now() + timedelta(days=delivery_days)

    lines = [
        ConfirmationLine(
            line_number=line.line_number,
            quantity=line.quantity,
            unit_of_measure=line.unit_of_measure or "EA",
            status=statuses.get(line.line_number, _default_status(header_type)),
            unit_price=prices.get(line.line_number),
            currency=line.currency or order.currency,
            delivery_date=delivery_date,
        )
        for line in order.lines
    ]

    confirmation = Confirmation(
        confirm_id=f"CONF-{record.order_id or record.ref}",
        notice_date=_now(),
        order_id=order.order_id,
        order_payload_id=order.payload_id,
        header_type=header_type,
        lines=lines,
    )

    problems = check_confirmation(confirmation)
    if problems:
        return None, problems

    payload_id = _payload_id()
    xml = build_confirmation(
        confirmation, payload_id=payload_id, timestamp=_now(),
        from_identity=SUPPLIER_IDENTITY, to_identity=buyer_identity,
        sender_identity=SUPPLIER_IDENTITY, shared_secret=shared_secret)

    return SentDocument(
        doc_id=_doc_id("confirmation"), kind="ConfirmationRequest",
        payload_id=payload_id, created_at=_now().timestamp(),
        xml=xml.decode("utf-8"),
    ), []


def _default_status(header_type: str) -> str:
    """The line status that matches a header type when the caller has not
    chosen one per line. `ALLOWED_STATUSES` is the authority on what is legal;
    this only picks a sensible default from it."""
    return {"accept": "accept", "reject": "reject",
            "backordered": "backordered", "detail": "detail",
            "except": "accept", "allDetail": "allDetail",
            "replace": "detail", "requestToPay": "requestToPay"}.get(
                header_type, "accept")


# =============================================================================
# Ship notice
# =============================================================================
def build_ship_notice_document(
    record: OrderRecord, *, shared_secret: str, buyer_identity: str,
    carrier_code: str = "UPSN", service_level: str = "Ground",
    tracking_number: str = "", partial_lines: Optional[list[int]] = None,
) -> tuple[Optional[SentDocument], list[str]]:
    order = order_from_record(record)
    wanted = set(partial_lines) if partial_lines else None

    lines = [
        ShipmentLine(
            line_number=line.line_number,
            quantity=line.quantity,
            unit_of_measure=line.unit_of_measure or "EA",
            supplier_part_id=line.supplier_part_id,
            description=line.description or "",
        )
        for line in order.lines
        if wanted is None or line.line_number in wanted
    ]

    shipment = Shipment(
        shipment_id=f"SHIP-{record.order_id or record.ref}",
        notice_date=_now(),
        order_id=order.order_id,
        order_payload_id=order.payload_id,
        lines=lines,
        shipment_date=_now(),
        delivery_date=_now() + timedelta(days=2),
        carrier_code=carrier_code,
        service_level=service_level,
        tracking_number=tracking_number or f"1Z{secrets.token_hex(8).upper()}",
        fulfillment_type="partial" if wanted else "complete",
    )

    problems = check_shipment(shipment)
    if problems:
        return None, problems

    payload_id = _payload_id()
    xml = build_ship_notice(
        shipment, payload_id=payload_id, timestamp=_now(),
        from_identity=SUPPLIER_IDENTITY, to_identity=buyer_identity,
        sender_identity=SUPPLIER_IDENTITY, shared_secret=shared_secret)

    return SentDocument(
        doc_id=_doc_id("shipnotice"), kind="ShipNoticeRequest",
        payload_id=payload_id, created_at=_now().timestamp(),
        xml=xml.decode("utf-8"),
    ), []


# =============================================================================
# Invoice
# =============================================================================
def build_invoice_document(
    record: OrderRecord, *, shared_secret: str, buyer_identity: str,
    buyer_country: Optional[str] = None, buyer_has_tax_id: bool = True,
    rounding: Rounding = Rounding.PER_LINE,
) -> tuple[Optional[SentDocument], list[str], object]:
    """Build an `InvoiceDetailRequest` against the order.

    Returns `(document, problems, calculation)`. The calculation is handed back
    so the screen can show WHY the tax came out as it did — `determine()`
    produces reasons precisely so they can be displayed, and an invoice screen
    that shows a number without them answers the least interesting half of the
    question."""
    order = order_from_record(record)

    if not order.lines:
        return None, ["This order has no lines to invoice."], None

    priced = [line for line in order.lines if line.unit_price is not None]
    if not priced:
        return None, [
            "No line on this order carries a UnitPrice, so there is nothing to "
            "invoice from. A blanket order is priced at release time; a regular "
            "one is missing data."], None

    country = (buyer_country or order.ship_to_country or SUPPLIER_COUNTRY).upper()
    try:
        rates.get(country)
    except KeyError:
        return None, [
            f"No tax rates are held for '{country}'. The sandbox covers "
            f"{len(rates.JURISDICTIONS)} jurisdictions; pick one, or fix the "
            "ShipTo country on the order."], None

    treatment, reasons = determine(
        supplier_country=SUPPLIER_COUNTRY, buyer_country=country,
        buyer_has_tax_id=buyer_has_tax_id, goods=True)

    currency = order.currency or priced[0].currency or "GBP"
    calculation = calculate(
        [TaxableLine(line_number=line.line_number, net_amount=line.subtotal)
         for line in priced],
        jurisdiction_code=country, treatment=treatment, rounding=rounding,
        # Without this the totals quantize to 2dp whatever the currency, and
        # a yen invoice reads JPY 1000.00.
        currency=currency)
    calculation.notes = list(reasons) + list(calculation.notes)

    invoice_lines = [
        InvoiceLine(
            line_number=index,
            quantity=line.quantity,
            unit_of_measure=line.unit_of_measure or "EA",
            unit_price=line.unit_price,
            supplier_part_id=line.supplier_part_id or "UNKNOWN",
            description=line.description or line.supplier_part_id or "Item",
            po_line_number=line.line_number,
            supplier_part_auxiliary_id=line.supplier_part_auxiliary_id,
            classification=line.classification,
            manufacturer_part_id=line.manufacturer_part_id,
            manufacturer_name=line.manufacturer_name,
        )
        for index, line in enumerate(priced, start=1)
    ]

    buyer_party = Party(
        role="billTo",
        name=order.bill_to_name or order.buyer_identity or "Buyer",
        street="(from your order)", city="(from your order)",
        postal_code="", country_code=country,
        country_name=rates.get(country).name,
    )

    invoice = Invoice(
        invoice_id=f"INV-{record.order_id or record.ref}",
        invoice_date=_now(),
        order_id=order.order_id,
        order_payload_id=order.payload_id,
        currency=currency,
        lines=invoice_lines,
        tax=calculation,
        parties=[SUPPLIER_PARTY, buyer_party],
    )

    payload_id = _payload_id()
    xml = build_invoice(
        invoice, payload_id=payload_id, timestamp=_now(),
        from_identity=SUPPLIER_IDENTITY, to_identity=buyer_identity,
        sender_identity=SUPPLIER_IDENTITY, shared_secret=shared_secret)

    return SentDocument(
        doc_id=_doc_id("invoice"), kind="InvoiceDetailRequest",
        payload_id=payload_id, created_at=_now().timestamp(),
        xml=xml.decode("utf-8"),
    ), [], calculation


# =============================================================================
# Delivery
# =============================================================================
def send(record: OrderRecord, document: SentDocument, endpoint: str) -> SentDocument:
    """POST a document and record what happened, on the document itself.

    A refusal (`DeliveryRefused` — we declined to send) and a failure (we sent
    and it went wrong) are both recorded as `delivered=False`, but the reason
    text distinguishes them, because they call for different actions from the
    user: fix the URL, versus fix the far end."""
    document.endpoint = endpoint
    try:
        result = delivery.deliver(endpoint, document.xml.encode("utf-8"))
    except delivery.DeliveryRefused as refusal:
        document.delivered = False
        document.failure_reason = str(refusal)
        document.observations = []
        telemetry.event("delivery_refused", kind=document.kind)
        orders.store().update_document(record, document)
        return document

    document.delivered = result.ok
    document.http_status = result.status
    document.cxml_status = result.cxml_status_code or ""
    document.response_excerpt = result.body[:4000]
    document.failure_reason = result.reason
    document.observations = result.observations
    document.duration_ms = result.duration_ms
    orders.store().update_document(record, document)
    return document
