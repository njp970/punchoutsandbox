"""What /order says about the characters in an order, and about repeats.

Written from a real week of traffic. One integrator sent orderIDs built from
accents, CJK, emoji, 83 shopping trolleys in a row, and — for two days — half
an emoji written out as the literal text `\\uD83D`, which stopped on the third
day. They also sent the same orderID twice, three times over. Every one of
those documents was valid cXML, so the validator had nothing to say. These
tests hold the sandbox to saying it.

The backslash in the escape tests is built with chr(92) on purpose: while this
was being written, a `\\u00e9` in a test string was silently decoded to `é` by
a layer in between before Python ever saw it. That is the same bug, and it is
worth not depending on every tool in the chain getting it right.
"""
import base64
import io
import json
import pathlib
import sys
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import orders, sessions, tenants
from app.cxml.order import ORDER_ID_REFERENCE_LIMIT
from app.handler import handler
from app.orders import MemoryOrders
from app.sessions import MemoryStore
from app.tenants import MemoryTenants, Tenant

failures: list[str] = []
BS = chr(92)


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def fresh():
    tenants.reset_store(MemoryTenants())
    sessions.reset_store(MemoryStore())
    orders.reset_store(MemoryOrders())
    t = Tenant(tenant_id="acct-1", email="a@b.example", sandbox_id="PSB100000001",
               shared_secret="secret-1")
    tenants.store().put(t)
    return t


def xml_attr(s):
    return (s.replace("&", "&amp;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;"))


def order(t, order_id, payload="p-1", description="Copier paper", kind="new"):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="{payload}@buyer.example.com" timestamp="2026-09-18T10:00:00+01:00">
 <Header>
  <From><Credential domain="NetworkID"><Identity>buyer</Identity></Credential></From>
  <To><Credential domain="NetworkID"><Identity>{t.sandbox_id}</Identity></Credential></To>
  <Sender><Credential domain="NetworkID"><Identity>buyer</Identity>
   <SharedSecret>{t.shared_secret}</SharedSecret></Credential>
   <UserAgent>Test</UserAgent></Sender>
 </Header>
 <Request deploymentMode="test"><OrderRequest>
  <OrderRequestHeader orderID="{xml_attr(order_id)}" orderDate="2026-09-18T10:00:00+01:00" type="{kind}">
   <Total><Money currency="GBP">10.00</Money></Total>
   <ShipTo><Address isoCountryCode="GB"><Name xml:lang="en">Depot</Name>
    <PostalAddress><Street>1 Road</Street><City>Bristol</City>
     <Country isoCountryCode="GB">United Kingdom</Country></PostalAddress></Address></ShipTo>
   <BillTo><Address isoCountryCode="GB"><Name xml:lang="en">Buyer Ltd</Name>
    <PostalAddress><Street>1 Road</Street><City>Bristol</City>
     <Country isoCountryCode="GB">United Kingdom</Country></PostalAddress></Address></BillTo>
  </OrderRequestHeader>
  <ItemOut quantity="1" lineNumber="1">
   <ItemID><SupplierPartID>MSC-1001</SupplierPartID></ItemID>
   <ItemDetail><UnitPrice><Money currency="GBP">10.00</Money></UnitPrice>
    <Description xml:lang="en">{description}</Description>
    <UnitOfMeasure>EA</UnitOfMeasure>
    <Classification domain="UNSPSC">14111507</Classification></ItemDetail>
  </ItemOut>
 </OrderRequest></Request>
</cXML>'''.encode("utf-8")


def post(body):
    ev = {"requestContext": {"http": {"method": "POST", "path": "/order"}},
          "queryStringParameters": {}, "headers": {"cf-connecting-ip": "203.0.113.9"},
          "cookies": [], "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = handler(ev)
    text = base64.b64decode(r["body"]).decode() if r.get("isBase64Encoded") else r["body"]
    events = [json.loads(l) for l in buf.getvalue().splitlines() if l.startswith("{")]
    return text, events


print("\n1. A plain order says none of this")
t = fresh()
text, _ = post(order(t, "PO-9001"))
check("accepted", 'code="200"' in text)
for phrase in ("characters (", "non-ASCII", "JSON-style", "doubly-encoded",
               "already used", "received 1 time"):
    check(f"...and does not mention '{phrase}'", phrase not in text)


print("\n2. Length")
t = fresh()
long_id = "🛒 " * 40 + "- 2026 - 00117"
text, _ = post(order(t, long_id))
check("a long orderID is still accepted — it is legal", 'code="200"' in text)
check("...but the length is reported", f"orderID is {len(long_id)} characters" in text,
      text[text.find('orderID is'):][:90])
check("...in bytes too, which is what fields are sized in",
      f"({len(long_id.encode())} bytes as UTF-8)" in text)
text, _ = post(order(t, "X" * ORDER_ID_REFERENCE_LIMIT, payload="p-2"))
check(f"exactly {ORDER_ID_REFERENCE_LIMIT} characters is fine",
      "characters (" not in text)


print("\n3. Non-ASCII")
t = fresh()
text, _ = post(order(t, "Ácme Büro & Co. — 日本 - 2026 - 00351"))
check("non-ASCII in the orderID is reported", "non-ASCII characters" in text)
check("...naming the characters", "日" in text and "Á" in text)
check("...and the ampersand was escaped properly on the way in",
      "doubly-encoded" not in text)


print("\n4. A JSON escape that leaked into the XML")
t = fresh()
half = f"Ácme 🛒 {BS}uD83D - 2026 - 00351"
text, _ = post(order(t, half))
check("half a surrogate pair is caught", "half of a surrogate pair" in text,
      text[text.find('JSON-style'):][:120])
check("...named as the exact sequence", f"{BS}uD83D" in text)
check("...and located in the orderID", "including in the orderID" in text)

t = fresh()
text, _ = post(order(t, "PO-1", description=f"Caf{BS}u00e9 table"))
check("an ordinary escape in a description is caught too",
      "JSON-style escape" in text and "half of a surrogate" not in text)
check("...and not blamed on the orderID", "including in the orderID" not in text)

t = fresh()
text, _ = post(order(t, "PO-1", description=f"C:{BS}users{BS}buyer path"))
check("a backslash that is not an escape is left alone", "JSON-style" not in text)


print("\n5. Double encoding")
t = fresh()
text, _ = post(order(t, "PO-1", description="Fish &amp;amp; chips, caf&amp;#233; table"))
check("an escaped entity is reported", "2 doubly-encoded entities" in text,
      text[text.find('doubly'):][:80])
t = fresh()
text, _ = post(order(t, "PO-1", description="Fish &amp; chips"))
check("a single, correct &amp; is not", "doubly-encoded" not in text)


print("\n6. The same orderID again")
t = fresh()
post(order(t, "PO-651", payload="p-a"))
text, _ = post(order(t, "PO-651", payload="p-a"))
check("same payloadID again is recognised as a retry",
      "received 1 time(s) before" in text and "already used" not in text)

text, events = post(order(t, "PO-651", payload="p-b"))
check("a new payloadID with the same orderID is a second order",
      "already used by 1 earlier order(s)" in text,
      text[text.find('orderID PO-651'):][:120])
check("...and says what to send instead", 'type="update"' in text)
check("...the order is still stored — reporting, not refusing",
      len(orders.store().recent("acct-1")) == 3)
check("the telemetry counts the notes",
      any(e.get("event") == "order_received" and e.get("notes", 0) >= 1 for e in events))

text, _ = post(order(t, "PO-651", payload="p-c", kind="update"))
check('type="update" reusing the orderID is not called a duplicate',
      "already used" not in text)

other = Tenant(tenant_id="acct-2", email="c@d.example", sandbox_id="PSB100000002",
               shared_secret="secret-2")
tenants.store().put(other)
text, _ = post(order(other, "PO-651", payload="p-z"))
check("another account's orderIDs are not this account's duplicates",
      "already used" not in text and "time(s) before" not in text,
      "one account must not learn anything about another's orders")


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("Valid cXML that will still hurt somebody is said out loud.")
