"""The order flow: PO in, confirmation / ship notice / invoice out.

The assertions that matter most here are not "does it produce XML". They are:

  * that every generated document validates against the REAL DTD, because a
    sandbox that emits invalid cXML is worse than no sandbox;
  * that the rules the DTD CANNOT express are enforced anyway, since those are
    exactly the ones a buyer rejects with no useful explanation;
  * that one account cannot read another's purchase orders;
  * that the delivery endpoint refuses to be an SSRF primitive.
"""
import base64
import sys
from decimal import Decimal as D
from urllib.parse import urlencode

sys.path.insert(0, "/Users/neilparkes/punchout")

from datetime import datetime, timezone

from app import delivery, orderflow, orders, sessions, signup, tenants
from app.cxml.fulfilment import (Confirmation, ConfirmationLine, Shipment,
                                 ShipmentLine, build_confirmation,
                                 build_ship_notice, check_confirmation,
                                 check_shipment)
from app.handler import handler
from app.orders import MemoryOrders
from app.sessions import MemoryStore
from app.tenants import MemoryTenants, Tenant
from app.validation import validate
from app.xml_safe import parse

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


ORDER = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="ord-1@buyer.example.com" timestamp="2026-08-14T10:00:00+01:00">
 <Header>
  <From><Credential domain="NetworkID"><Identity>buyer</Identity></Credential></From>
  <To><Credential domain="NetworkID"><Identity>{SID}</Identity></Credential></To>
  <Sender><Credential domain="NetworkID"><Identity>buyer</Identity>
   <SharedSecret>{SEC}</SharedSecret></Credential>
   <UserAgent>Test Buyer 1.0</UserAgent></Sender>
 </Header>
 <Request deploymentMode="test">
  <OrderRequest>
   <OrderRequestHeader orderID="{OID}" orderDate="2026-08-14T10:00:00+01:00" type="new">
    <Total><Money currency="GBP">120.00</Money></Total>
    <ShipTo><Address isoCountryCode="DE"><Name xml:lang="en">Berlin Depot</Name>
      <PostalAddress><Street>1 Alexanderplatz</Street><City>Berlin</City>
       <PostalCode>10178</PostalCode>
       <Country isoCountryCode="DE">Germany</Country></PostalAddress>
     </Address></ShipTo>
    <BillTo><Address isoCountryCode="DE"><Name xml:lang="en">Northgate GmbH</Name>
      <PostalAddress><Street>1 Alexanderplatz</Street><City>Berlin</City>
       <Country isoCountryCode="DE">Germany</Country></PostalAddress>
     </Address></BillTo>
   </OrderRequestHeader>
   <ItemOut quantity="10" lineNumber="1">
    <ItemID><SupplierPartID>MSC-1001</SupplierPartID></ItemID>
    <ItemDetail>
     <UnitPrice><Money currency="GBP">10.00</Money></UnitPrice>
     <Description xml:lang="en">Kestrel A4 copier paper, 80gsm</Description>
     <UnitOfMeasure>BX</UnitOfMeasure>
     <Classification domain="UNSPSC">14111507</Classification>
    </ItemDetail>
   </ItemOut>
   <ItemOut quantity="2">
    <ItemID><SupplierPartID>MSC-2002</SupplierPartID></ItemID>
    <ItemDetail>
     <UnitPrice><Money currency="GBP">7.50</Money></UnitPrice>
     <Description xml:lang="en">Vantor gel pen, black</Description>
     <UnitOfMeasure>EA</UnitOfMeasure>
     <Classification domain="UNSPSC">44121704</Classification>
    </ItemDetail>
   </ItemOut>
  </OrderRequest>
 </Request>
</cXML>'''

NOW = datetime.now(timezone.utc).astimezone()


def go(path, method="GET", body=b"", form=None, cookies=None):
    if form is not None:
        body = urlencode(form).encode()
    ev = {"requestContext": {"http": {"method": method, "path": path}},
          "queryStringParameters": {},
          "headers": {"cf-connecting-ip": "203.0.113.9"},
          "cookies": cookies or [],
          "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    r = handler(ev)
    b = r["body"]
    return (r["statusCode"],
            base64.b64decode(b).decode() if r.get("isBase64Encoded") else b, r)


def fresh():
    tenants.reset_store(MemoryTenants())
    sessions.reset_store(MemoryStore())
    orders.reset_store(MemoryOrders())
    t = Tenant(tenant_id="acct-1", email="a@b.example", sandbox_id="PSB1",
               shared_secret="secret-1")
    tenants.store().put(t)
    return t


def order_body(tenant, order_id="PO-9001"):
    return (ORDER.replace("{SID}", tenant.sandbox_id)
            .replace("{SEC}", tenant.shared_secret)
            .replace("{OID}", order_id).encode())


print("\n1. The order inbox")
tenant = fresh()
status, body, _ = go("/order", "POST", b"<nope/>")
check("an unauthenticated POST is refused", "401" in body, body[:120])
check("...as cXML over HTTP 200, not an HTTP error", status == 200,
      f"got {status} — an HTTP error triggers ten hourly retries")

status, body, resp = go("/order", "POST", order_body(tenant))
check("an authenticated OrderRequest is accepted", 'code="200"' in body)
check("the Status text reports the line count", "lines=2" in body)
check("...and the Total/line-sum mismatch", "does not equal" in body)
check("...and the missing lineNumber", "no ItemOut/@lineNumber" in body)
check("the response points at the order screen",
      "x-punchout-sandbox-order" in resp["headers"],
      "a URL a human can open beats a verdict nobody sees")

stored = orders.store().recent("acct-1")
check("the order was stored", len(stored) == 1, f"{len(stored)} stored")
record = stored[0]
check("stored verbatim", record.raw.startswith("<?xml"),
      "a reserialised copy is evidence of OUR parser, not their system")

print("\n2. Documents that are not orders")
status, body, _ = go("/order", "POST",
                     order_body(tenant).replace(b"OrderRequest", b"ConfirmationRequest"))
check("a non-OrderRequest is refused with a business status",
      'code="400"' in body, body[:160])

no_id = order_body(tenant).replace(b'orderID="PO-9001"', b'orderID=""')
status, body, _ = go("/order", "POST", no_id)
check("an order with no orderID is refused", 'code="400"' in body,
      "everything downstream references it")

print("\n3. Confirmations validate against the real DTD")
fresh_tenant = tenant
for header_type in ("accept", "detail", "backordered", "reject"):
    doc, problems = orderflow.build_confirmation_document(
        record, header_type=header_type, shared_secret="s",
        buyer_identity="buyer")
    if problems:
        check(f'header type "{header_type}" builds', False, "; ".join(problems))
        continue
    report = validate(parse(doc.xml.encode()))
    check(f'header type "{header_type}" validates',
          not report.errors and report.document_type == "ConfirmationRequest",
          "; ".join(e.message for e in report.errors))

print("\n4. Rules the DTD cannot express are enforced anyway")
bad = Confirmation(confirm_id="C", notice_date=NOW, order_id="PO",
                   order_payload_id="p", header_type="accept",
                   lines=[ConfirmationLine(1, D("1"), "EA", status="reject")])
problems = check_confirmation(bad)
check("status=reject under header type=accept is refused",
      any("not permitted under header type" in p for p in problems),
      "; ".join(problems))

empty_detail = Confirmation(confirm_id="C", notice_date=NOW, order_id="PO",
                            order_payload_id="p", header_type="detail",
                            lines=[ConfirmationLine(1, D("1"), "EA", status="detail")])
check('status=detail that changes nothing is refused',
      any("changes nothing" in p for p in check_confirmation(empty_detail)))

header_only = Confirmation(confirm_id="C", notice_date=NOW, order_id="PO",
                           order_payload_id="p", header_type="except", lines=[])
check("a line-level header type with no lines is refused",
      any("no ConfirmationItem" in p for p in check_confirmation(header_only)))

update = Confirmation(confirm_id="C", notice_date=NOW, order_id="PO",
                      order_payload_id="p", header_type="accept",
                      operation="update",
                      lines=[ConfirmationLine(1, D("1"), "EA")])
check('operation="update" without a DocumentReference is refused',
      any("previous ConfirmationRequest" in p for p in check_confirmation(update)))

print("\n5. The doubled UnitOfMeasure is actually emitted")
doc, _ = orderflow.build_confirmation_document(
    record, header_type="accept", shared_secret="s", buyer_identity="buyer")
check("ConfirmationItem and ConfirmationStatus each carry one",
      doc.xml.count("<UnitOfMeasure>") == 2 * record.line_count,
      f"{doc.xml.count('<UnitOfMeasure>')} for {record.line_count} lines — "
      "emitting it once is the commonest confirmation failure")

print("\n6. Ship notices")
doc, problems = orderflow.build_ship_notice_document(
    record, shared_secret="s", buyer_identity="buyer")
check("a ship notice builds", not problems, "; ".join(problems))
if doc:
    report = validate(parse(doc.xml.encode()))
    check("...and validates",
          not report.errors and report.document_type == "ShipNoticeRequest",
          "; ".join(e.message for e in report.errors))
    check("...carrying a tracking number", "trackingNumber" in doc.xml)

duplicate = Shipment(shipment_id="S", notice_date=NOW, order_id="PO",
                     order_payload_id="p",
                     lines=[ShipmentLine(1, D("1"), "EA"),
                            ShipmentLine(1, D("2"), "EA")])
check("the same order line twice in one notice is refused",
      any("more than once" in p for p in check_shipment(duplicate)))

no_service = Shipment(shipment_id="S", notice_date=NOW, order_id="PO",
                      order_payload_id="p", service_level="",
                      lines=[ShipmentLine(1, D("1"), "EA")])
check("a missing ServiceLevel is refused even though the DTD allows it",
      any("ServiceLevel" in p for p in check_shipment(no_service)))

print("\n7. Invoices")
doc, problems, calc = orderflow.build_invoice_document(
    record, shared_secret="s", buyer_identity="buyer")
check("an invoice builds from the order", not problems, "; ".join(problems))
if doc:
    report = validate(parse(doc.xml.encode()))
    check("...and validates against InvoiceDetail.dtd",
          not report.errors and report.document_type == "InvoiceDetailRequest",
          "; ".join(e.message for e in report.errors))
    check("...taxed in the ShipTo country, not the supplier's",
          calc.jurisdiction.code == "DE",
          f"got {calc.jurisdiction.code} — the order ships to Berlin")
    check("...and explains why", bool(calc.notes),
          "a tax figure with no reasoning answers the boring half")
    check("...pricing only the lines that carry a price",
          doc.xml.count("<InvoiceDetailItem ") == 2)

doc, problems, _ = orderflow.build_invoice_document(
    record, shared_secret="s", buyer_identity="buyer", buyer_country="ZZ")
check("an unknown jurisdiction is refused, not guessed",
      doc is None and any("No tax rates" in p for p in problems),
      "; ".join(problems))

print("\n8. One account cannot read another's orders")
other = Tenant(tenant_id="acct-2", email="c@d.example", sandbox_id="PSB2",
               shared_secret="secret-2")
tenants.store().put(other)
status, body, _ = go(f"/orders/{record.ref}", cookies=["pst=acct-2"])
check("a foreign order ref is a 404", status == 404, f"got {status}")
status, body, _ = go(f"/orders/{record.ref}", cookies=["pst=acct-1"])
check("the owner sees it", status == 200, f"got {status}")
check("...with the line table", "MSC-1001" in body)

print("\n9. Delivery refuses to be an SSRF primitive")
for url, why in [
    ("http://example.com/x", "plain HTTP"),
    ("https://127.0.0.1/x", "loopback"),
    ("https://169.254.169.254/latest/meta-data/", "link-local metadata"),
    ("https://10.0.0.1/x", "private range"),
    ("https://example.com:8080/x", "a non-443 port"),
    ("https://user:pass@example.com/x", "credentials in the URL"),
    ("file:///etc/passwd", "a non-http scheme"),
]:
    refused = False
    try:
        delivery.vet_url(url)
    except delivery.DeliveryRefused:
        refused = True
    check(f"{why} is refused", refused, url)

print("\n10. Endpoint settings are checked when saved")
status, body, _ = go("/settings", "POST", form={"endpoint": "https://127.0.0.1/inbox"},
                     cookies=["pst=acct-1"])
check("a refused endpoint is reported on the form", "refused" in body.lower(),
      "better than discovering it at send time")
check("...and not stored",
      tenants.store().get("acct-1").buyer_endpoint == "")

status, body, _ = go("/settings", "POST",
                     form={"endpoint": "https://example.com/cxml/inbox"},
                     cookies=["pst=acct-1"])
check("a good endpoint is stored",
      tenants.store().get("acct-1").buyer_endpoint == "https://example.com/cxml/inbox")

print("\n11. The order screens are gated, the machine endpoint is not")
status, body, _ = go("/orders")
check("/orders shows the signup gate, not the order list",
      "PO-9001" not in body and "/signup" in body,
      f"got {status} — the gate answers 200 with a form, which is why the "
      "status code alone proves nothing")
status, body, _ = go("/order", "POST", b"<x/>")
check("/order answers machines in cXML rather than redirecting",
      "cXML" in body, body[:80])

print("\n12. Generated documents are not sent until asked")
doc, _ = orderflow.build_confirmation_document(
    record, header_type="accept", shared_secret="s", buyer_identity="buyer")
check("a freshly built document is in the 'generated' state",
      doc.state == "generated" and doc.delivered is None,
      "building one to look at it is a normal thing to want")

print("\n13. Nothing float-shaped reaches DynamoDB")
# This section exists because of a real 502. MemoryOrders accepts a float
# happily, so every test above passed while the first document generated
# against the real table died inside boto3's serialiser with "Float types are
# not supported". The store now converts centrally; this proves it, without
# needing a table.
from dataclasses import asdict

from app.orders import _no_floats


def floats_in(value, path="item"):
    if isinstance(value, float):
        return [path]
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in floats_in(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [f for i, v in enumerate(value) for f in floats_in(v, f"{path}[{i}]")]
    return []

doc, _ = orderflow.build_confirmation_document(
    record, header_type="accept", shared_secret="s", buyer_identity="buyer")
payload = {"doc": asdict(doc), "expires_at": record.expires_at,
           "received_at": record.received_at}
check("a document payload contains floats before conversion",
      bool(floats_in(payload)), "if this fails the test is no longer testing anything")
leftover = floats_in(_no_floats(payload))
check("...and none after", not leftover, ", ".join(leftover))
check("Decimal conversion goes through str, not binary",
      str(_no_floats(0.1)) == "0.1",
      "Decimal(0.1) captures the binary representation, not the number written")

print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("The order flow holds, and every generated document validates.")
