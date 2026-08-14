"""`ConfirmationRequest` and `ShipNoticeRequest` — the documents between a
purchase order and an invoice.

*Spec: Fulfill.dtd, which is a SEPARATE DTD from cXML.dtd. Getting the DOCTYPE
wrong here is the first thing that goes wrong, and it fails in a confusing way:
the document is well-formed, the elements all exist in the reader's mental
model of cXML, and validation reports an undefined element.*

=============================================================================
WHY A SANDBOX NEEDS THESE AT ALL
=============================================================================
Because they are where buyer platforms diverge most, and where nobody can test.
A supplier can usually get a punchout working by trial and error against a live
buyer, and an invoice is important enough that someone will sit on a call to
debug it. Confirmations and ship notices are neither: they are fire-and-forget
documents that a buyer either ingests silently or drops silently, and a
supplier typically discovers a year later that none of theirs ever landed.

=============================================================================
TWO ORDERING TRAPS, BOTH OF WHICH READ AS BACKWARDS
=============================================================================
1. **`ConfirmationStatus` is `(UnitOfMeasure, (ItemIn | (UnitPrice?, Tax?,
   Shipping?)), ...)`** — and its parent `ConfirmationItem` opens with
   `UnitOfMeasure` too. So the unit appears TWICE, nested, and both are
   mandatory. Emitting it once is the single most common confirmation
   validation failure.

2. **`ShipNoticeItem` is `(ItemID?, ShipNoticeItemDetail?, UnitOfMeasure, ...)`**
   — `ItemID` is OPTIONAL and `UnitOfMeasure` is not. The reverse of every
   other item block in cXML, where the identifier is the mandatory part.

=============================================================================
THE type ATTRIBUTE IS NOT FREE TEXT, AND THE TWO LEVELS DIFFER
=============================================================================
`ConfirmationHeader/@type` and `ConfirmationStatus/@type` are both enumerated
and their value sets are NOT the same:

  header : accept allDetail detail backordered except reject requestToPay replace
  status : accept allDetail detail backordered reject unknown requestToPay

`except` and `replace` are header-only; `unknown` is status-only. Worse, the
DTD cannot express which combinations are legal — the prose says, for example,
that a status of `accept` is allowed only under a header type of `accept`,
`detail` or `except`. `check_confirmation()` enforces those rules because the
validator cannot, and a document that passes the DTD while breaking them will
be rejected by the buyer with no useful explanation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal as D
from typing import Optional

_DOCTYPE = ('<!DOCTYPE cXML SYSTEM '
            '"http://xml.cxml.org/schemas/cXML/1.2.071/Fulfill.dtd">')

#: The two enumerations, kept apart on purpose — see the module docstring.
HEADER_TYPES = ("accept", "allDetail", "detail", "backordered", "except",
                "reject", "requestToPay", "replace")
STATUS_TYPES = ("accept", "allDetail", "detail", "backordered", "reject",
                "unknown", "requestToPay")

#: Which line statuses each header type permits. From the DTD's prose, which
#: is the only place these rules exist — no schema can express them.
ALLOWED_STATUSES: dict[str, tuple[str, ...]] = {
    "accept": ("accept",),
    "allDetail": ("allDetail", "backordered", "reject"),
    "detail": ("accept", "detail", "backordered", "reject"),
    "except": ("accept", "detail", "backordered", "reject"),
    "backordered": ("backordered",),
    "reject": ("reject",),
    "replace": ("detail",),
    "requestToPay": ("requestToPay",),
}


def _esc(value: str) -> str:
    """`&` first — see `invoice.py` for why the order is not negotiable."""
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _attr(value: str) -> str:
    return _esc(value).replace('"', "&quot;").replace("'", "&apos;")


def _stamp(value: datetime, *, who: str) -> str:
    """cXML forbids the `Z` designator and requires a numeric offset."""
    if value.tzinfo is None:
        raise ValueError(f"{who} must be timezone-aware — cXML forbids 'Z'")
    text = value.isoformat()
    if text.endswith("Z"):
        raise ValueError(f"{who} serialised with 'Z', which cXML forbids")
    return text


def _envelope(*, payload_id: str, timestamp: datetime, from_identity: str,
              to_identity: str, sender_identity: str, shared_secret: str,
              body: str) -> bytes:
    header = (
        "<Header>"
        f'<From><Credential domain="NetworkID"><Identity>{_esc(from_identity)}'
        "</Identity></Credential></From>"
        f'<To><Credential domain="NetworkID"><Identity>{_esc(to_identity)}'
        "</Identity></Credential></To>"
        f'<Sender><Credential domain="NetworkID"><Identity>{_esc(sender_identity)}'
        f"</Identity><SharedSecret>{_esc(shared_secret)}</SharedSecret></Credential>"
        "<UserAgent>PunchOut Sandbox</UserAgent></Sender>"
        "</Header>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + _DOCTYPE
        + f'<cXML payloadID="{_attr(payload_id)}" '
        f'timestamp="{_attr(_stamp(timestamp, who="timestamp"))}">'
        + header
        + '<Request deploymentMode="test">' + body + "</Request>"
        + "</cXML>"
    )
    return document.encode("utf-8")


def _order_reference(order_id: str, order_payload_id: str) -> str:
    """`OrderReference` is `(DocumentReference)` — the child is MANDATORY.

    An `OrderReference` carrying only its `orderID` attribute is a valid-
    looking, invalid document, and it is what you write if you have not read
    the element model."""
    return (f'<OrderReference orderID="{_attr(order_id)}">'
            f'<DocumentReference payloadID="{_attr(order_payload_id)}"/>'
            "</OrderReference>")


# =============================================================================
# ConfirmationRequest
# =============================================================================
@dataclass
class ConfirmationLine:
    line_number: int
    quantity: D
    unit_of_measure: str
    status: str = "accept"
    #: Only meaningful for `detail` and `allDetail`; a price change is the
    #: usual reason a supplier sends one at all.
    unit_price: Optional[D] = None
    currency: str = ""
    shipment_date: Optional[datetime] = None
    delivery_date: Optional[datetime] = None
    comment: str = ""


@dataclass
class Confirmation:
    confirm_id: str
    notice_date: datetime
    order_id: str
    order_payload_id: str
    header_type: str = "accept"
    lines: list[ConfirmationLine] = field(default_factory=list)
    #: Required when operation is "update" — identifies the previous document.
    previous_payload_id: str = ""
    operation: str = "new"
    comment: str = ""


def check_confirmation(confirmation: Confirmation) -> list[str]:
    """Rules the DTD cannot express. Returns problems, empty when clean.

    Separate from `build_confirmation` so a caller can show them to a human
    before sending. The builder raises on the same conditions — a sandbox that
    generates a document it knows a buyer will reject is not a useful
    sandbox."""
    problems: list[str] = []

    if confirmation.header_type not in HEADER_TYPES:
        problems.append(
            f'ConfirmationHeader/@type="{confirmation.header_type}" is not one '
            f"of {', '.join(HEADER_TYPES)}.")
        return problems

    if confirmation.operation == "update" and not confirmation.previous_payload_id:
        problems.append(
            'operation="update" requires a DocumentReference in the '
            "ConfirmationHeader pointing at the previous ConfirmationRequest. "
            "The DTD marks it optional; the prose makes it mandatory here.")

    allowed = ALLOWED_STATUSES.get(confirmation.header_type, ())
    for line in confirmation.lines:
        if line.status not in STATUS_TYPES:
            problems.append(
                f'Line {line.line_number}: status "{line.status}" is not a '
                f"ConfirmationStatus type. Note that `except` and `replace` "
                "are header-only values.")
        elif line.status not in allowed:
            problems.append(
                f'Line {line.line_number}: status "{line.status}" is not '
                f'permitted under header type "{confirmation.header_type}" '
                f"(allowed: {', '.join(allowed)}). Valid against the DTD, "
                "rejected by the buyer.")
        if line.status == "detail" and line.unit_price is None \
                and line.delivery_date is None and line.shipment_date is None:
            problems.append(
                f'Line {line.line_number}: status "detail" means "accepted '
                "with changes\", so at least one of UnitPrice, Shipping, Tax, "
                "ItemIn or deliveryDate must say what changed. This one "
                "changes nothing.")

    if not confirmation.lines and confirmation.header_type != "accept":
        problems.append(
            f'A header type of "{confirmation.header_type}" describes '
            "line-level outcomes but no ConfirmationItem elements are present. "
            'Only "accept" is meaningful as a header-only confirmation.')

    return problems


def _confirmation_item(line: ConfirmationLine) -> str:
    uom = f"<UnitOfMeasure>{_esc(line.unit_of_measure)}</UnitOfMeasure>"

    detail = ""
    if line.unit_price is not None:
        detail = (f'<UnitPrice><Money currency="{_attr(line.currency)}">'
                  f"{line.unit_price}</Money></UnitPrice>")

    dates = ""
    if line.shipment_date is not None:
        dates += f' shipmentDate="{_attr(_stamp(line.shipment_date, who="shipment_date"))}"'
    if line.delivery_date is not None:
        dates += f' deliveryDate="{_attr(_stamp(line.delivery_date, who="delivery_date"))}"'

    comment = (f'<Comments xml:lang="en">{_esc(line.comment)}</Comments>'
               if line.comment else "")

    return (
        f'<ConfirmationItem quantity="{line.quantity}" '
        f'lineNumber="{line.line_number}">'
        # First of the two mandatory UnitOfMeasure elements.
        + uom
        + f'<ConfirmationStatus quantity="{line.quantity}" '
        f'type="{_attr(line.status)}"{dates}>'
        # Second one. Nested, mandatory, and the usual reason a confirmation
        # fails to validate.
        + uom
        + detail
        + comment
        + "</ConfirmationStatus>"
        "</ConfirmationItem>"
    )


def build_confirmation(confirmation: Confirmation, *, payload_id: str,
                       timestamp: datetime, from_identity: str,
                       to_identity: str, sender_identity: str,
                       shared_secret: str) -> bytes:
    problems = check_confirmation(confirmation)
    if problems:
        raise ValueError("; ".join(problems))

    reference = ""
    if confirmation.operation == "update":
        reference = (f'<DocumentReference '
                     f'payloadID="{_attr(confirmation.previous_payload_id)}"/>')

    comment = (f'<Comments xml:lang="en">{_esc(confirmation.comment)}</Comments>'
               if confirmation.comment else "")

    body = (
        "<ConfirmationRequest>"
        f'<ConfirmationHeader confirmID="{_attr(confirmation.confirm_id)}" '
        f'operation="{_attr(confirmation.operation)}" '
        f'type="{_attr(confirmation.header_type)}" '
        f'noticeDate="{_attr(_stamp(confirmation.notice_date, who="notice_date"))}">'
        # DocumentReference is FIRST in ConfirmationHeader, before Total.
        + reference + comment
        + "</ConfirmationHeader>"
        + _order_reference(confirmation.order_id, confirmation.order_payload_id)
        + "".join(_confirmation_item(line) for line in confirmation.lines)
        + "</ConfirmationRequest>"
    )
    return _envelope(payload_id=payload_id, timestamp=timestamp,
                     from_identity=from_identity, to_identity=to_identity,
                     sender_identity=sender_identity,
                     shared_secret=shared_secret, body=body)


# =============================================================================
# ShipNoticeRequest
# =============================================================================
@dataclass
class ShipmentLine:
    line_number: int
    quantity: D
    unit_of_measure: str
    supplier_part_id: str = ""
    description: str = ""


@dataclass
class Shipment:
    shipment_id: str
    notice_date: datetime
    order_id: str
    order_payload_id: str
    lines: list[ShipmentLine] = field(default_factory=list)
    shipment_date: Optional[datetime] = None
    delivery_date: Optional[datetime] = None
    carrier: str = "SCAC"
    carrier_code: str = ""
    tracking_number: str = ""
    tracking_url: str = ""
    service_level: str = "Ground"
    #: actual | planned
    shipment_type: str = "actual"
    #: partial | complete
    fulfillment_type: str = "complete"
    operation: str = "new"
    previous_payload_id: str = ""


def check_shipment(shipment: Shipment) -> list[str]:
    problems: list[str] = []

    if shipment.operation != "delete" and not shipment.service_level:
        problems.append(
            "ServiceLevel is `*` in the DTD but the prose requires at least "
            'one on every ShipNoticeRequest except operation="delete". A '
            "buyer validating against the prose will reject this.")

    if shipment.operation in ("update", "delete") and not shipment.previous_payload_id:
        problems.append(
            f'operation="{shipment.operation}" requires a DocumentReference in '
            "the ShipNoticeHeader identifying the previous ShipNoticeRequest.")

    seen: set[int] = set()
    for line in shipment.lines:
        if line.line_number in seen:
            problems.append(
                f"Line {line.line_number} appears more than once. The spec is "
                "explicit that a single ship notice must describe each order "
                "line exactly once, even when the quantity is split across "
                "packages.")
        seen.add(line.line_number)

    if shipment.tracking_number and not shipment.carrier_code:
        problems.append(
            "A tracking number without a CarrierIdentifier is not actionable — "
            "ShipControl requires at least one CarrierIdentifier anyway, and a "
            "buyer cannot resolve a tracking number without knowing the "
            "carrier.")

    return problems


def _ship_notice_item(line: ShipmentLine) -> str:
    item_id = ""
    if line.supplier_part_id:
        item_id = ("<ItemID>"
                   f"<SupplierPartID>{_esc(line.supplier_part_id)}</SupplierPartID>"
                   "</ItemID>")
    detail = ""
    if line.description:
        detail = ("<ShipNoticeItemDetail>"
                  f'<Description xml:lang="en">{_esc(line.description)}</Description>'
                  "</ShipNoticeItemDetail>")
    return (
        f'<ShipNoticeItem quantity="{line.quantity}" '
        f'lineNumber="{line.line_number}">'
        # ItemID is optional here and UnitOfMeasure is not — the reverse of
        # every other item block in cXML.
        + item_id + detail
        + f"<UnitOfMeasure>{_esc(line.unit_of_measure)}</UnitOfMeasure>"
        + "</ShipNoticeItem>"
    )


def build_ship_notice(shipment: Shipment, *, payload_id: str,
                      timestamp: datetime, from_identity: str,
                      to_identity: str, sender_identity: str,
                      shared_secret: str) -> bytes:
    problems = check_shipment(shipment)
    if problems:
        raise ValueError("; ".join(problems))

    attrs = (f'shipmentID="{_attr(shipment.shipment_id)}" '
             f'operation="{_attr(shipment.operation)}" '
             f'noticeDate="{_attr(_stamp(shipment.notice_date, who="notice_date"))}" '
             f'shipmentType="{_attr(shipment.shipment_type)}" '
             f'fulfillmentType="{_attr(shipment.fulfillment_type)}"')
    if shipment.shipment_date is not None:
        attrs += f' shipmentDate="{_attr(_stamp(shipment.shipment_date, who="shipment_date"))}"'
    if shipment.delivery_date is not None:
        attrs += f' deliveryDate="{_attr(_stamp(shipment.delivery_date, who="delivery_date"))}"'

    reference = ""
    if shipment.operation in ("update", "delete"):
        reference = (f'<DocumentReference '
                     f'payloadID="{_attr(shipment.previous_payload_id)}"/>')

    service = (f'<ServiceLevel xml:lang="en">{_esc(shipment.service_level)}</ServiceLevel>'
               if shipment.service_level else "")

    control = ""
    if shipment.carrier_code or shipment.tracking_number:
        tracking_attrs = ""
        if shipment.tracking_url:
            tracking_attrs = f' trackingURL="{_attr(shipment.tracking_url)}"'
        control = (
            "<ShipControl>"
            f'<CarrierIdentifier domain="{_attr(shipment.carrier)}">'
            f"{_esc(shipment.carrier_code)}</CarrierIdentifier>"
            f'<ShipmentIdentifier domain="trackingNumber"{tracking_attrs}>'
            f"{_esc(shipment.tracking_number)}</ShipmentIdentifier>"
            "</ShipControl>"
        )

    portion = (
        "<ShipNoticePortion>"
        + _order_reference(shipment.order_id, shipment.order_payload_id)
        + "".join(_ship_notice_item(line) for line in shipment.lines)
        + "</ShipNoticePortion>"
    )

    body = (
        "<ShipNoticeRequest>"
        f"<ShipNoticeHeader {attrs}>"
        # ServiceLevel comes FIRST, before DocumentReference.
        + service + reference
        + "</ShipNoticeHeader>"
        + control
        + portion
        + "</ShipNoticeRequest>"
    )
    return _envelope(payload_id=payload_id, timestamp=timestamp,
                     from_identity=from_identity, to_identity=to_identity,
                     sender_identity=sender_identity,
                     shared_secret=shared_secret, body=body)
