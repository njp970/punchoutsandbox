"""Inbound `OrderRequest` — the purchase order a buyer sends after punchout.

*This closes the largest hole in the sandbox. Until now the round trip ended
at the cart: a buyer could punch out, shop, and return a
`PunchOutOrderMessage`, and then nothing. But the cart is a requisition, not
an order — in the real flow the buyer turns it into a PO and sends an
`OrderRequest`, and everything downstream (confirmation, ship notice, invoice)
references that PO. Without it there is nothing to confirm and nothing to
invoice against.*

=============================================================================
THE SAME TOLERANCE RULE AS THE PUNCHOUT DOOR
=============================================================================
`setup_request.py` explains at length why a sandbox that rejects
non-conformant documents is useless to the person who came here because their
document is non-conformant. The same applies with more force here: an
`OrderRequest` is a much bigger document with many more ways to be subtly
wrong, and "Ariba accepted it, you did not" is a bug report we would deserve.

So **every parseable order is accepted and stored**, the full validation
report is attached to it, and the observations below are reported back inside
the cXML `Status` text where a buyer's own logs will capture them.

=============================================================================
WHAT THIS PARSER DELIBERATELY DOES NOT DO
=============================================================================
It does not compute anything. `Total` is read as sent and never recalculated
to "correct" it, because a mismatch between the buyer's `Total` and the sum of
the lines is one of the most useful things this sandbox can show a person —
and silently repairing it would hide exactly the defect they came to find. The
mismatch is reported as an observation and the document is stored as it
arrived.

=============================================================================
lineNumber IS OPTIONAL, AND THAT IS A TRAP
=============================================================================
`ItemOut/@lineNumber` is `#IMPLIED`. Suppliers universally assume it is
present, because it is the only stable handle for referring to a line in a
confirmation, a ship notice or an invoice — all three reference lines *by
number*. A buyer that omits it produces a valid order that cannot be precisely
confirmed, and nobody finds out until the confirmation is rejected.

We fall back to document position, which is what every real supplier does, and
say so loudly in the observations.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from decimal import Decimal as D, InvalidOperation
from typing import Optional


@dataclass
class OrderLine:
    line_number: int
    quantity: D
    supplier_part_id: str
    description: str
    unit_price: Optional[D] = None
    unit_of_measure: str = ""
    currency: str = ""
    supplier_part_auxiliary_id: Optional[str] = None
    classification: Optional[str] = None
    manufacturer_part_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    #: True when the buyer omitted `lineNumber` and we inferred it from
    #: position. Carried through to everything that references this line.
    line_number_inferred: bool = False

    @property
    def subtotal(self) -> D:
        if self.unit_price is None:
            return D("0.00")
        return (self.quantity * self.unit_price).quantize(D("0.01"))


@dataclass
class Order:
    order_id: str
    order_date: str
    payload_id: str
    currency: str
    lines: list[OrderLine] = field(default_factory=list)
    total: Optional[D] = None
    order_type: str = "regular"
    #: new | update | delete
    request_type: str = "new"
    buyer_identity: str = ""
    ship_to_name: str = ""
    ship_to_country: str = ""
    bill_to_name: str = ""

    @property
    def line_subtotal(self) -> D:
        return sum((line.subtotal for line in self.lines), D("0.00"))


def _text(node, path: str) -> Optional[str]:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = "".join(found.itertext()).strip()
    return value or None


def _decimal(value: Optional[str]) -> Optional[D]:
    """Parse a cXML numeric field, or return None.

    Returns None rather than raising on rubbish. A buyer sending
    `quantity="two"` has a real defect, but it is a defect to REPORT — raising
    here would turn a document we could otherwise show them into a 500."""
    if value is None:
        return None
    try:
        return D(value.strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def parse_order(tree) -> Order:
    """Build an `Order` from a parsed cXML tree.

    `tree` is the `lxml` root from `xml_safe.parse` — this function never sees
    raw bytes, so there is exactly one entry point for untrusted XML in the
    whole repository."""
    root = tree.find(".//OrderRequest")
    header = tree.find(".//OrderRequestHeader")
    cxml = tree if tree.tag == "cXML" else tree.getroottree().getroot()

    total_node = header.find("Total/Money") if header is not None else None
    currency = (total_node.get("currency") if total_node is not None else "") or ""

    order = Order(
        order_id=(header.get("orderID") if header is not None else "") or "",
        order_date=(header.get("orderDate") if header is not None else "") or "",
        payload_id=cxml.get("payloadID") or "",
        currency=currency,
        total=_decimal(total_node.text if total_node is not None else None),
        order_type=(header.get("orderType") if header is not None else None) or "regular",
        request_type=(header.get("type") if header is not None else None) or "new",
        buyer_identity=_text(tree, ".//From/Credential/Identity") or "",
        ship_to_name=_text(header, "ShipTo/Address/Name") if header is not None else "",
        ship_to_country=(
            (header.find("ShipTo/Address/PostalAddress/Country").get("isoCountryCode")
             if header is not None
             and header.find("ShipTo/Address/PostalAddress/Country") is not None
             else "") or ""),
        bill_to_name=_text(header, "BillTo/Address/Name") if header is not None else "",
    )

    items = root.findall("ItemOut") if root is not None else []
    for position, item in enumerate(items, start=1):
        raw_line = item.get("lineNumber")
        detail = item.find("ItemDetail")
        price_node = detail.find("UnitPrice/Money") if detail is not None else None

        order.lines.append(OrderLine(
            line_number=int(raw_line) if (raw_line or "").isdigit() else position,
            line_number_inferred=not (raw_line or "").isdigit(),
            quantity=_decimal(item.get("quantity")) or D("0"),
            supplier_part_id=_text(item, "ItemID/SupplierPartID") or "",
            supplier_part_auxiliary_id=_text(item, "ItemID/SupplierPartAuxiliaryID"),
            description=_text(detail, "Description") if detail is not None else "",
            unit_price=_decimal(price_node.text if price_node is not None else None),
            unit_of_measure=(_text(detail, "UnitOfMeasure")
                             if detail is not None else "") or "",
            currency=(price_node.get("currency") if price_node is not None else "") or currency,
            classification=_text(detail, "Classification") if detail is not None else None,
            manufacturer_part_id=(_text(detail, "ManufacturerPartID")
                                  if detail is not None else None),
            manufacturer_name=(_text(detail, "ManufacturerName")
                               if detail is not None else None),
        ))

    return order


def observations(order: Order) -> list[str]:
    """Facts about this order worth telling the buyer.

    Distinct from `validation.py`'s errors and advisories: those judge the
    document against the DTD and against platform behaviour. These are about
    the ORDER — internal inconsistencies that are perfectly valid XML and will
    still cause an argument at reconciliation time."""
    notes: list[str] = []

    if not order.lines:
        notes.append(
            "No ItemOut lines. The DTD requires at least one (OrderRequest is "
            "(OrderRequestHeader, ItemOut+)), so this document should not have "
            "validated — check the report.")

    inferred = [line for line in order.lines if line.line_number_inferred]
    if inferred:
        notes.append(
            f"{len(inferred)} of {len(order.lines)} lines have no "
            "ItemOut/@lineNumber. It is optional in the DTD and effectively "
            "mandatory in practice: confirmations, ship notices and invoices "
            "all reference lines BY NUMBER. We have inferred it from document "
            "position, which is what real suppliers do — but if your system "
            "reorders lines between documents, that inference silently "
            "misattributes every subsequent reference.")

    if order.total is not None and order.lines:
        difference = order.total - order.line_subtotal
        if difference:
            notes.append(
                f"Header Total ({order.currency} {order.total}) does not equal "
                f"the sum of the lines ({order.currency} {order.line_subtotal}), "
                f"a difference of {difference}. That is legal — Total is "
                "supposed to include shipping and tax — but it is also how an "
                "under-billing goes unnoticed, so it is worth confirming the "
                "difference is deliberate. Nothing here has been recalculated.")

    currencies = {line.currency for line in order.lines if line.currency}
    if order.currency and currencies - {order.currency}:
        notes.append(
            f"Line currencies {sorted(currencies)} do not all match the header "
            f"currency ({order.currency}). cXML permits this and almost no "
            "downstream system handles it correctly.")

    missing_price = [line.line_number for line in order.lines
                     if line.unit_price is None]
    if missing_price:
        notes.append(
            f"Lines {missing_price} carry no ItemDetail/UnitPrice. Valid for a "
            "blanket order; a defect in a regular one, and an invoice against "
            "them will have nothing to price from.")

    if order.request_type in ("update", "delete"):
        notes.append(
            f'type="{order.request_type}" means this replaces or cancels a '
            "previous order with the same orderID. This sandbox stores it as a "
            "new document rather than applying it, so both versions stay "
            "visible for comparison.")

    if not order.ship_to_country:
        notes.append(
            "No ShipTo country. The invoice generator needs one to pick a tax "
            "jurisdiction, and will fall back to the supplier's own country.")

    return notes


# --------------------------------------------------------------------------- #
# The orderID and the bytes around it
# --------------------------------------------------------------------------- #
#: Where your orderID ends up: the supplier stores it as the customer
#: reference on its own sales order, and that is a short field. SAP's
#: (VBKD-BSTKD, "customer purchase order number") is 35 characters. cXML puts
#: no limit on orderID at all, so nothing will warn you before a supplier's
#: ERP truncates it — and a truncated PO number is one the invoice will not
#: match.
ORDER_ID_REFERENCE_LIMIT = 35

#: A JSON-style escape that reached the XML as literal text: backslash, "u",
#: four hex digits. XML has no such escape, so these are never decoded — the
#: supplier stores the six characters as they are.
_ESCAPE_TEXT = re.compile(r"\\u([0-9A-Fa-f]{4})")

#: `&amp;` followed by another entity: something escaped text that was
#: already escaped. The receiving side decodes once and keeps `&amp;` or
#: `&#233;` as literal text.
_DOUBLE_ENCODED = re.compile(r"&amp;(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);")


def identifier_observations(order: Order, raw: str) -> list[str]:
    """Things about the characters in this order that will hurt downstream.

    Written after a real integrator spent a week sending orderIDs made of
    accents, CJK, emoji and — for two days — half an emoji written out as the
    text `\\uD83D`, and then stopped doing that on the third day. Every one of
    those documents validated. The DTD has no opinion about any of this, which
    is exactly why it is worth saying out loud."""
    notes: list[str] = []
    oid = order.order_id or ""

    if len(oid) > ORDER_ID_REFERENCE_LIMIT:
        size = len(oid.encode("utf-8"))
        notes.append(
            f"orderID is {len(oid)} characters ({size} bytes as UTF-8). cXML "
            "sets no limit, but the supplier keeps your orderID as the customer "
            "reference on its own sales order, and those fields are short — "
            f"SAP's is {ORDER_ID_REFERENCE_LIMIT} characters. A truncated PO "
            "number is one the confirmation and invoice will quote back to you "
            "wrongly, so they will not match the order you sent.")

    if any(ord(c) > 127 for c in oid):
        examples = "".join(sorted({c for c in oid if ord(c) > 127}))[:12]
        notes.append(
            f"orderID contains non-ASCII characters ({examples}). Legal — cXML "
            "is UTF-8 — but the orderID gets copied into supplier ERPs, EDI "
            "translators, email subjects and PDF filenames, several of which "
            "will not survive it. An ASCII PO number is the one that comes back "
            "unchanged on the invoice.")

    escapes = _ESCAPE_TEXT.findall(raw or "")
    if escapes:
        halves = [h for h in escapes if 0xD800 <= int(h, 16) <= 0xDFFF]
        where = " (including in the orderID)" if _ESCAPE_TEXT.search(oid) else ""
        if halves:
            notes.append(
                f"The document contains {len(escapes)} JSON-style escape "
                f"sequence(s) as literal text{where}, {len(halves)} of them "
                f"half of a surrogate pair (\\u{halves[0].upper()}). That is "
                "an emoji or other character outside the Basic Multilingual "
                "Plane, escaped by a JSON serialiser and then written into XML, "
                "which has no \\u escape — so it arrives as six characters of "
                "text, and the other half of the pair is gone. Look for where "
                "your code builds this value from JSON.")
        else:
            notes.append(
                f"The document contains {len(escapes)} JSON-style escape "
                f"sequence(s) as literal text{where} (\\u{escapes[0].upper()}). "
                "XML has no \\u escape, so the supplier stores the backslash "
                "and the hex digits exactly as sent. Something serialised this "
                "value as JSON before it went into the XML.")

    doubled = _DOUBLE_ENCODED.findall(raw or "")
    if doubled:
        notes.append(
            f"The document contains {len(doubled)} doubly-encoded entit"
            f"{'y' if len(doubled) == 1 else 'ies'} (for example "
            f"{doubled[0]}). Something escaped text that was already escaped, "
            "so the supplier decodes it once and keeps a literal "
            "\"&amp;\" or \"&#233;\" in the name or description.")

    return notes
