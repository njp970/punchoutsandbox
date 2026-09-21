"""The purchase-order inbox — `OrderRequest` in, cXML `Response` out.

*The second machine endpoint, and the one that turns this from a punchout demo
into a full supplier. See `cxml/order.py` for the parsing and `orderflow.py`
for what can be done with an order once it is here.*

=============================================================================
WHY THE RESPONSE CARRIES SO MUCH TEXT
=============================================================================
A cXML `Status` has a `text` attribute and free character content, and almost
every implementation wastes both — "OK" or "Failure" and nothing else.

This endpoint puts everything it noticed in there: line count, whether
lineNumber was present, whether the header Total reconciles with the lines,
how many conformance errors the document has. That text lands in the BUYER's
own transaction log, which is the one place their integration team will
actually look when something is wrong. A verdict they have to come to our
website to read is a verdict most of them will never see.

=============================================================================
ACCEPTED, THEN JUDGED — NEVER THE OTHER WAY ROUND
=============================================================================
The same rule as `setup_request.py`: a document that parses is stored and
answered with a 200, however non-conformant it is, because the person sending
it is here to find out what is wrong with it. Only `xml_safe` refusals and
documents that are not `OrderRequest` at all are turned away.

The one thing that IS refused is an order with no recognisable `orderID`,
because everything downstream — confirmation, ship notice, invoice — must
reference it, and generating those against an empty string produces documents
that no buyer can reconcile.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from . import orders, telemetry
from .cxml.order import identifier_observations, observations, parse_order
from .http import Request, Response
from .orders import OrderRecord, new_ref
from .validation import describe_findings, validate
from .xml_safe import XmlRejected, parse


def _status_response(code: int, text: str, detail: str = "") -> Response:
    """HTTP 200 carrying a cXML Status — see `setup_request._status_response`
    for why a business-level refusal must never be an HTTP error code."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
        f'<cXML payloadID="{secrets.token_hex(8)}@punchoutsandbox.com" '
        f'timestamp="{datetime.now(timezone.utc).astimezone().isoformat()}">'
        f'<Response><Status code="{code}" text="{text}">'
        f"{detail}</Status></Response></cXML>"
    )
    return Response(status=200, body=body, content_type="text/xml; charset=utf-8")


def _escape(value: str) -> str:
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))



#: How far back to look for an earlier order with the same orderID. Orders are
#: kept seven days, so this covers everything still stored for any account
#: that is not sending more than this many a week.
DUPLICATE_LOOKBACK = 200


def duplicate_observations(order, tenant) -> list[str]:
    """Has this account already sent an order with this orderID?

    Two different situations share a symptom, and the difference is the
    payloadID. The same payloadID again is a RETRY of one document, which is
    what the spec intends and what a supplier deduplicates on. A different
    payloadID with type="new" is a SECOND order carrying a PO number the
    supplier already holds — and depending on the system that is rejected as a
    duplicate, silently dropped, or booked as a second sales order. The last
    one is how the same goods get shipped twice.

    Only type="new" is checked: an update or delete is SUPPOSED to reuse the
    orderID, and `observations` already explains how those are stored."""
    if order.request_type != "new" or not order.order_id:
        return []
    earlier = [o for o in orders.store().recent(tenant.tenant_id,
                                                limit=DUPLICATE_LOOKBACK)
               if o.order_id == order.order_id]
    if not earlier:
        return []
    retries = [o for o in earlier
               if order.payload_id and o.payload_id == order.payload_id]
    if len(retries) == len(earlier):
        return [
            f"This exact document (payloadID {order.payload_id}) has been "
            f"received {len(earlier)} time(s) before in the last seven days. "
            "Resending with the same payloadID is how a retry is meant to look, "
            "and a supplier should recognise it rather than book a second "
            "order — but it is worth checking your side retried because it did "
            "not get a response, rather than resubmitting a requisition."]
    # Count DOCUMENTS, not receipts: a retried document is one earlier order,
    # however many times it arrived.
    fresh = len({o.payload_id for o in earlier} - {order.payload_id})
    return [
        f"orderID {order.order_id} was already used by {fresh} earlier order(s) "
        "from this account in the last seven days, each with a different "
        'payloadID, and this one is type="new" again. A supplier receiving a '
        "second new order with a PO number it already holds will reject it as "
        "a duplicate, ignore it, or book a second sales order — and the last is "
        'how the same goods ship twice. A change should be type="update"; a '
        "retry should reuse the original payloadID."]

def handle_order(request: Request, tenant, *, site_url: str) -> Response:
    """Accept an `OrderRequest`, store it, and report what we saw."""
    try:
        doc = parse(request.body)
    except XmlRejected as exc:
        return _status_response(406, "Not Acceptable", _escape(str(exc)))

    report = validate(doc, expected_type="OrderRequest")
    if report.document_type != "OrderRequest":
        return _status_response(
            400, "Bad Request",
            f"This endpoint expects an OrderRequest; received "
            f"{report.document_type or 'an unrecognised document'}.")

    order = parse_order(doc.tree)

    if not order.order_id:
        return _status_response(
            400, "Bad Request",
            "OrderRequestHeader/@orderID is empty or absent. It is #REQUIRED, "
            "and every document that follows an order — confirmation, ship "
            "notice, invoice — references it. This sandbox will not store an "
            "order it cannot later reference.")

    raw_text = request.body.decode("utf-8", "replace")
    # Checked BEFORE this order is stored, so it cannot match itself.
    notes = (observations(order)
             + identifier_observations(order, raw_text)
             + duplicate_observations(order, tenant))
    ref = new_ref()
    record = OrderRecord(
        ref=ref,
        tenant_id=tenant.tenant_id,
        order_id=order.order_id,
        payload_id=order.payload_id,
        buyer_identity=order.buyer_identity,
        currency=order.currency,
        total=str(order.total) if order.total is not None else "",
        line_count=len(order.lines),
        received_at=time.time(),
        raw=raw_text,
        conformant=report.conformant,
        error_count=len(report.errors),
        advisory_count=len(report.advisories),
        observations=notes,
    )
    orders.store().put(record)

    telemetry.event("order_received", account=telemetry.account_of(tenant),
                    lines=len(order.lines), conformant=report.conformant,
                    errors=len(report.errors), notes=len(notes))

    detail = "; ".join(
        [f"orderID={order.order_id}",
         f"lines={len(order.lines)}",
         f"total={order.currency} {order.total if order.total is not None else '(absent)'}",
         f"lineSubtotal={order.currency} {order.line_subtotal}",
         f"conformant={report.conformant}",
         f"errors={len(report.errors)}",
         f"advisories={len(report.advisories)}"]
        # The errors THEMSELVES, not just how many. Reporting a count and
        # withholding the detail made people bisect their own document to
        # find what we already knew.
        + describe_findings(report.errors)
        + notes)

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
        f'<cXML payloadID="{secrets.token_hex(8)}@punchoutsandbox.com" '
        f'timestamp="{datetime.now(timezone.utc).astimezone().isoformat()}">'
        "<Response>"
        f'<Status code="200" text="OK">{_escape(detail)}</Status>'
        "</Response></cXML>"
    )
    # The order screen is where confirmations, ship notices and invoices get
    # generated, so its URL is worth putting somewhere a human will find it.
    # A header rather than the document, because inventing an Extrinsic in a
    # Response would be a non-conformant way to say something useful.
    return Response(status=200, body=body,
                    content_type="text/xml; charset=utf-8",
                    headers={"x-punchout-sandbox-order": f"{site_url}/orders/{ref}"})
