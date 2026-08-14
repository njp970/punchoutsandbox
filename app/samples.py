"""Worked examples of every document this sandbox speaks.

=============================================================================
GENERATED, NEVER PASTED
=============================================================================
Each sample is produced by the same builder that produces the real thing, at
request time. A sample written by hand into a template is a sample that drifts:
it is correct on the day it is written and silently wrong after the next change
to the builder — and a wrong example in the documentation of a conformance tool
is worse than no example, because people trust it.

Everything here is therefore DTD-valid by construction, and the tests assert
exactly that.

=============================================================================
WHY THIS EXISTS
=============================================================================
Feedback from somebody integrating against it: the docs mention
`ShipNoticeRequest` once in passing and show none, and the shape of that
document is precisely what their extractor got wrong. A sample would have
saved the bug.

`ShipNoticeRequest` is the worst offender in cXML for exactly this reason —
`ItemID` is OPTIONAL and `UnitOfMeasure` is not, which is the reverse of every
other item block in the specification. You do not discover that from prose.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Optional

from .cxml.fulfilment import (Confirmation, ConfirmationLine, Shipment,
                              ShipmentLine, build_confirmation,
                              build_ship_notice)
from .cxml.invoice import Invoice, InvoiceLine, build_invoice
from .cxml.punchout import CartItem, build_punchout_order_message
from .orderflow import SUPPLIER_PARTY
from .cxml.invoice import Party
from .tax.engine import Rounding, TaxableLine, TaxTreatment, calculate

#: Fixed so that two fetches of the same sample differ only where they must
#: (payload IDs and timestamps are generated). A sample whose part numbers
#: change between reads is useless to diff against.
_SKU = "MSC-1001"
_AUX = "BX-30"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _pid() -> str:
    return f"{secrets.token_hex(8)}@punchoutsandbox.com"


_IDENTITIES = dict(from_identity="meridian-supply", to_identity="buyer",
                   sender_identity="meridian-supply",
                   shared_secret="your-shared-secret")

_BUYER = Party(role="billTo", name="Northgate Industries Ltd",
               street="8 Kingsway", city="Manchester", postal_code="M2 4WU",
               country_code="GB", country_name="United Kingdom")


def _setup_request() -> bytes:
    """Hand-built, unlike the rest — this is the one document the sandbox
    RECEIVES rather than sends, so there is no builder to generate it from.
    It is covered by the round-trip test instead."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">\n'
        f'<cXML payloadID="{_pid()}" timestamp="{_now().isoformat()}">\n'
        ' <Header>\n'
        '  <From><Credential domain="NetworkID">\n'
        '    <Identity>your-buyer-id</Identity></Credential></From>\n'
        '  <To><Credential domain="NetworkID">\n'
        '    <Identity>YOUR-SANDBOX-IDENTITY</Identity></Credential></To>\n'
        '  <Sender><Credential domain="NetworkID">\n'
        '    <Identity>your-buyer-id</Identity>\n'
        '    <SharedSecret>YOUR-SHARED-SECRET</SharedSecret></Credential>\n'
        '   <UserAgent>Your Procurement System 1.0</UserAgent></Sender>\n'
        ' </Header>\n'
        ' <Request deploymentMode="test">\n'
        '  <PunchOutSetupRequest operation="create">\n'
        '   <BuyerCookie>your-requisition-id</BuyerCookie>\n'
        '   <BrowserFormPost>\n'
        '     <URL>https://your-system.example.com/punchout/return</URL>\n'
        '   </BrowserFormPost>\n'
        '  </PunchOutSetupRequest>\n'
        ' </Request>\n'
        '</cXML>\n').encode("utf-8")


def _order_message() -> bytes:
    return build_punchout_order_message(
        [CartItem(supplier_part_id=_SKU, quantity=D("3"),
                  unit_price=D("9.99"), description="Nitrile gloves, box of 30",
                  short_name="Nitrile gloves", unit_of_measure="BX",
                  classification="14111507", currency="GBP",
                  supplier_part_auxiliary_id=_AUX,
                  manufacturer_part_id="KES-NG-30",
                  manufacturer_name="Kestrel", lead_time_days=2)],
        buyer_cookie="your-requisition-id", payload_id=_pid(),
        timestamp=_now(), operation_allowed="edit", **_IDENTITIES)


def _confirmation() -> bytes:
    return build_confirmation(
        Confirmation(confirm_id="CONF-PO-1001", notice_date=_now(),
                     order_id="PO-1001", order_payload_id="the-order-payload-id",
                     header_type="detail",
                     lines=[ConfirmationLine(
                         line_number=1, quantity=D("3"), unit_of_measure="BX",
                         status="detail", unit_price=D("10.49"), currency="GBP",
                         delivery_date=_now() + timedelta(days=5),
                         comment="Price increased since the catalogue was published")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _ship_notice() -> bytes:
    return build_ship_notice(
        Shipment(shipment_id="SHIP-PO-1001", notice_date=_now(),
                 order_id="PO-1001", order_payload_id="the-order-payload-id",
                 shipment_date=_now(),
                 delivery_date=_now() + timedelta(days=2),
                 carrier_code="UPSN", service_level="Ground",
                 tracking_number="1Z999AA10123456784",
                 tracking_url="https://www.ups.com/track?tracknum=1Z999AA10123456784",
                 lines=[ShipmentLine(line_number=1, quantity=D("3"),
                                     unit_of_measure="BX",
                                     supplier_part_id=_SKU,
                                     description="Nitrile gloves, box of 30")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _invoice() -> bytes:
    lines = [InvoiceLine(line_number=1, quantity=D("3"), unit_of_measure="BX",
                         unit_price=D("9.99"), supplier_part_id=_SKU,
                         description="Nitrile gloves, box of 30",
                         po_line_number=1, supplier_part_auxiliary_id=_AUX,
                         classification="14111507",
                         manufacturer_part_id="KES-NG-30",
                         manufacturer_name="Kestrel")]
    calculation = calculate(
        [TaxableLine(line_number=1, net_amount=lines[0].subtotal)],
        jurisdiction_code="GB", treatment=TaxTreatment.STANDARD,
        rounding=Rounding.PER_LINE)
    return build_invoice(
        Invoice(invoice_id="INV-PO-1001", invoice_date=_now(),
                order_id="PO-1001", order_payload_id="the-order-payload-id",
                currency="GBP", lines=lines, tax=calculation,
                parties=[SUPPLIER_PARTY, _BUYER]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


# --------------------------------------------------------------------------- #
# The adversarial builders
# --------------------------------------------------------------------------- #
def _ship_notice_unit_change() -> bytes:
    """Ordered 1 BX (a box of 30). Despatched as 30 EA.

    Both statements are true and a supplier who breaks a box open really does
    send this. The receiving system has to notice that the UNIT changed as
    well as the number, and an extractor with no `UnitOfMeasure` field reads
    30 and believes thirty boxes arrived."""
    return build_ship_notice(
        Shipment(shipment_id="SHIP-PO-1002-A", notice_date=_now(),
                 order_id="PO-1002", order_payload_id="the-order-payload-id",
                 shipment_date=_now(),
                 delivery_date=_now() + timedelta(days=2),
                 carrier_code="UPSN", service_level="Ground",
                 tracking_number="1Z999AA10123456785",
                 lines=[ShipmentLine(line_number=1, quantity=D("30"),
                                     unit_of_measure="EA",
                                     supplier_part_id=_SKU,
                                     description="Nitrile gloves — box opened, "
                                                 "despatched as singles")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _ship_notice_partial() -> bytes:
    """Three ordered, one despatched, and the header says so."""
    return build_ship_notice(
        Shipment(shipment_id="SHIP-PO-1001-A", notice_date=_now(),
                 order_id="PO-1001", order_payload_id="the-order-payload-id",
                 shipment_date=_now(),
                 delivery_date=_now() + timedelta(days=2),
                 carrier_code="UPSN", service_level="Ground",
                 tracking_number="1Z999AA10123456786",
                 fulfillment_type="partial",
                 lines=[ShipmentLine(line_number=1, quantity=D("1"),
                                     unit_of_measure="BX",
                                     supplier_part_id=_SKU,
                                     description="Nitrile gloves, box of 30 "
                                                 "— 1 of 3, balance to follow")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _confirmation_price_change() -> bytes:
    """Accepted, at a higher price and a later date. Routine, and easy to miss
    if the only question asked is "did a confirmation arrive"."""
    return build_confirmation(
        Confirmation(confirm_id="CONF-PO-1003", notice_date=_now(),
                     order_id="PO-1003", order_payload_id="the-order-payload-id",
                     header_type="detail",
                     lines=[ConfirmationLine(
                         line_number=1, quantity=D("3"), unit_of_measure="BX",
                         status="detail", unit_price=D("12.75"), currency="GBP",
                         delivery_date=_now() + timedelta(days=21),
                         comment="Price increased and lead time extended since "
                                 "the catalogue was published")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _confirmation_backordered() -> bytes:
    """A confirmation is not an acceptance."""
    return build_confirmation(
        Confirmation(confirm_id="CONF-PO-1004", notice_date=_now(),
                     order_id="PO-1004", order_payload_id="the-order-payload-id",
                     header_type="backordered",
                     lines=[ConfirmationLine(
                         line_number=1, quantity=D("3"), unit_of_measure="BX",
                         status="backordered",
                         delivery_date=_now() + timedelta(days=60),
                         comment="Out of stock; expected in 8 weeks")]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _invoice_split_line() -> bytes:
    """One PO line, two invoice lines — what happens whenever a shipment is
    split. Both reference `po_line_number=1`; only `invoiceLineNumber` differs.

    A receiver matching invoice lines to PO lines one-to-one either drops the
    second or double-counts the first."""
    lines = [
        InvoiceLine(line_number=1, quantity=D("1"), unit_of_measure="BX",
                    unit_price=D("9.99"), supplier_part_id=_SKU,
                    description="Nitrile gloves, box of 30 — first shipment",
                    po_line_number=1, supplier_part_auxiliary_id=_AUX,
                    classification="14111507"),
        InvoiceLine(line_number=2, quantity=D("2"), unit_of_measure="BX",
                    unit_price=D("9.99"), supplier_part_id=_SKU,
                    description="Nitrile gloves, box of 30 — balance",
                    po_line_number=1, supplier_part_auxiliary_id=_AUX,
                    classification="14111507"),
    ]
    calculation = calculate(
        [TaxableLine(line_number=line.line_number, net_amount=line.subtotal)
         for line in lines],
        jurisdiction_code="GB", treatment=TaxTreatment.STANDARD,
        rounding=Rounding.PER_LINE)
    return build_invoice(
        Invoice(invoice_id="INV-PO-1001-B", invoice_date=_now(),
                order_id="PO-1001", order_payload_id="the-order-payload-id",
                currency="GBP", lines=lines, tax=calculation,
                parties=[SUPPLIER_PARTY, _BUYER]),
        payload_id=_pid(), timestamp=_now(), **_IDENTITIES)


def _order_message_quirks() -> bytes:
    """A cart of every deliberately imperfect product in the catalogue.

    Built from the catalogue itself rather than hand-written, so it stays in
    step with `catalogue/models.py:Quirk` — a new quirk joins this document
    automatically instead of being forgotten."""
    from .catalogue.data import PRODUCTS
    quirked = [p for p in PRODUCTS if p.quirks]
    items = [
        CartItem(supplier_part_id=p.sku, quantity=D("1"),
                 unit_price=p.price_for(1), description=p.description,
                 short_name=p.name[:50],
                 # Deliberately NOT normalised: the whole point is to send
                 # what a sloppy supplier sends.
                 unit_of_measure=p.uom,
                 classification=p.unspsc, currency=p.currency,
                 supplier_part_auxiliary_id=p.aux_token,
                 manufacturer_part_id=p.manufacturer_part_id,
                 manufacturer_name=p.manufacturer,
                 lead_time_days=p.lead_time_days)
        for p in quirked
    ]
    return build_punchout_order_message(
        items, buyer_cookie="your-requisition-id", payload_id=_pid(),
        timestamp=_now(), operation_allowed="edit", **_IDENTITIES)


@dataclass(frozen=True)
class Sample:
    key: str
    name: str
    blurb: str
    build: callable
    #: What an extractor gets wrong if it is not paying attention. Empty for
    #: the canonical samples, which are designed to agree with everything.
    breaks: str = ""


CANONICAL: tuple[Sample, ...] = (
    Sample("punchoutsetuprequest", "PunchOutSetupRequest",
           "What your system sends to open a session. The one document here "
           "that you send and we receive.", _setup_request),
    Sample("punchoutordermessage", "PunchOutOrderMessage",
           "The cart coming back. Your extractor reads this — note that "
           "SupplierPartAuxiliaryID must survive unchanged into your order.",
           _order_message),
    Sample("confirmationrequest", "ConfirmationRequest",
           "Order confirmation. UnitOfMeasure appears TWICE, once in "
           "ConfirmationItem and again in ConfirmationStatus: both are "
           "mandatory.", _confirmation),
    Sample("shipnoticerequest", "ShipNoticeRequest",
           "Dispatch notification. ItemID is OPTIONAL here and UnitOfMeasure "
           "is not — the reverse of every other item block in cXML, and the "
           "usual reason an extractor written from the other documents fails "
           "on this one. ShipControl is a SIBLING of ShipNoticePortion, not "
           "inside it: one shipment can cover several orders.", _ship_notice),
    Sample("invoicedetailrequest", "InvoiceDetailRequest",
           "Invoice. Both indicator elements are mandatory and ordered even "
           "when empty, and UnitOfMeasure and UnitPrice come BEFORE the item "
           "reference.", _invoice),
)


# =============================================================================
# ADVERSARIAL SAMPLES — VALID DOCUMENTS DESIGNED TO BREAK YOUR PARSER
# =============================================================================
# The canonical samples above agree with themselves: the ship notice ships
# exactly what the order ordered, in the same unit, at the same price. An
# extractor that ignores UnitOfMeasure entirely passes every one of them.
#
# That is a real weakness, and it was found the honest way — an integrator read
# the prose warning about ShipNoticeItem, checked their code, and discovered
# their extractor had no UnitOfMeasure field at all. The warning did the work
# the sample should have done.
#
# So: documents that are conformant cXML, that a real supplier really sends,
# and that DISAGREE with the order in exactly one way each. Every one is
# DTD-valid — the point is not malformed input, which `/validate` already
# covers, but well-formed input carrying a fact your code may be assuming away.
ADVERSARIAL: tuple[Sample, ...] = (
    Sample("shipnoticerequest-unit-change", "Ship notice — despatched in a different unit",
           "Ordered as 1 BX (a box of 30). Despatched as 30 EA. Both are "
           "true, and a supplier splitting a box legitimately does this.",
           _ship_notice_unit_change,
           breaks="An extractor with no UnitOfMeasure field reads '30' and "
                  "believes 30 boxes arrived. This is the shape of the most "
                  "expensive documented punchout failure."),
    Sample("shipnoticerequest-partial", "Ship notice — partial shipment",
           'Three ordered, one despatched, fulfillmentType="partial" with the '
           "rest to follow.", _ship_notice_partial,
           breaks="Code that marks a line complete on the first ship notice "
                  "closes an order that is two thirds outstanding."),
    Sample("confirmationrequest-price-change", "Confirmation — accepted at a different price",
           'type="detail" carrying a UnitPrice above the one ordered, and a '
           "delivery date later than requested. Entirely routine.",
           _confirmation_price_change,
           breaks="Treating every confirmation as accept-as-ordered means the "
                  "price change is discovered by the invoice, after the goods "
                  "have shipped."),
    Sample("confirmationrequest-backordered", "Confirmation — backordered",
           'type="backordered": accepted, but nothing is coming yet.',
           _confirmation_backordered,
           breaks="A confirmation is not an acceptance. Code keyed only on "
                  "'did a ConfirmationRequest arrive' reports this as "
                  "confirmed and in stock."),
    Sample("invoicedetailrequest-split-line", "Invoice — one PO line, two invoice lines",
           "A single order line invoiced across two invoice lines, as happens "
           "whenever a shipment is split.", _invoice_split_line,
           breaks="Matching invoice lines to PO lines one-to-one either drops "
                  "the second line or double-counts the first."),
    Sample("punchoutordermessage-quirks", "Cart — every catalogue quirk at once",
           "A cart built entirely from the deliberately imperfect products: "
           "free-text unit, pack size smuggled into the unit, a description "
           "past 256 characters, delimiters in the part number, non-ASCII "
           "text, an over-length auxiliary id, a sub-penny price and a "
           "punctuated UNSPSC.", _order_message_quirks,
           breaks="Everything at once. If your extractor survives this one, "
                  "it survives real suppliers."),
)

SAMPLES: dict[str, Sample] = {s.key: s for s in CANONICAL + ADVERSARIAL}


def build(key: str) -> Optional[bytes]:
    sample = SAMPLES.get(key.lower())
    return sample.build() if sample else None
