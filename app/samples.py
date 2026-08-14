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


SAMPLES: dict[str, tuple[str, str, callable]] = {
    "punchoutsetuprequest": (
        "PunchOutSetupRequest",
        "What your system sends to open a session. The one document here that "
        "you send and we receive.", _setup_request),
    "punchoutordermessage": (
        "PunchOutOrderMessage",
        "The cart coming back. Your extractor reads this — note that "
        "SupplierPartAuxiliaryID must survive unchanged into your order.",
        _order_message),
    "confirmationrequest": (
        "ConfirmationRequest",
        "Order confirmation. Note UnitOfMeasure appears TWICE, once in "
        "ConfirmationItem and again in ConfirmationStatus: both are mandatory.",
        _confirmation),
    "shipnoticerequest": (
        "ShipNoticeRequest",
        "Dispatch notification. ItemID is OPTIONAL here and UnitOfMeasure is "
        "not — the reverse of every other item block in cXML, and the usual "
        "reason an extractor written from the other documents fails on this "
        "one.", _ship_notice),
    "invoicedetailrequest": (
        "InvoiceDetailRequest",
        "Invoice. Both indicator elements are mandatory and ordered even when "
        "empty, and UnitOfMeasure and UnitPrice come BEFORE the item "
        "reference.", _invoice),
}


def build(key: str) -> Optional[bytes]:
    entry = SAMPLES.get(key.lower())
    return entry[2]() if entry else None
