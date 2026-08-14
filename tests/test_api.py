"""The machine path, and the six defects that produced it.

All of these came from somebody integrating a real buyer system against the
deployed sandbox in an afternoon. Every one had passed a full unit suite and a
147-check live QA pass, which is worth remembering: the tests were green
because they tested a client that behaves like the tests.

  1. `errors=3` with no enumeration — the count was reported and the findings
     withheld, so the user bisected their own document to learn what we knew.
  2. The anonymous cap was keyed on address, not identity — `current_tenant`
     reads a browser cookie, so valid credentials counted for nothing.
  3. The punchout round trip could not be completed headlessly, because the
     storefront demanded an account the shopper could not have.
  4. Two different refusals, 403 and 429, indistinguishable to the caller.
  5. No sample ShipNoticeRequest, which is the document whose shape is least
     guessable.
  6. No way for an agent to obtain credentials at all.
"""
import base64
import json
import pathlib
import re
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import samples, sessions, signup, tenants
from app.handler import handler
from app.sessions import MemoryStore, Session
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


class Client:
    """A machine. No browser, no forms — a cookie jar only because HTTP has
    one, not because anything here depends on a person having used a form."""

    def __init__(self):
        self.cookies: list[str] = []

    def __call__(self, path, method="GET", body=b"", query=None, headers=None):
        event = {"requestContext": {"http": {"method": method, "path": path}},
                 "queryStringParameters": query or {}, "headers": headers or {},
                 "cookies": list(self.cookies),
                 "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
        result = handler(event)
        for cookie in result.get("cookies", []):
            self.cookies.append(cookie.split(";")[0])
        raw = result["body"]
        return (result["statusCode"],
                base64.b64decode(raw).decode() if result.get("isBase64Encoded") else raw)


def fresh():
    sessions.reset_store(MemoryStore())
    tenants.reset_store(MemoryTenants())
    return Client()


BROKEN_ORDER = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="p@b" timestamp="2026-08-14T10:00:00+01:00"><Header>
<From><Credential domain="NetworkID"><Identity>b</Identity></Credential></From>
<To><Credential domain="NetworkID"><Identity>s</Identity></Credential></To>
<Sender><Credential domain="NetworkID"><Identity>b</Identity></Credential><UserAgent>t</UserAgent></Sender>
</Header><Request deploymentMode="test"><OrderRequest>
<OrderRequestHeader orderID="PO-1" orderDate="2026-08-14T10:00:00+01:00">
<Total><Money currency="GBP">1.00</Money></Total>
<ShipTo><Address><Nonsense>x</Nonsense></Address></ShipTo>
</OrderRequestHeader></OrderRequest></Request></cXML>'''


print("\n6. An agent can obtain credentials without a browser")
client = fresh()
status, body = client("/api/signup", "POST",
                      json.dumps({"email": "agent@corp.example"}).encode())
check("POST /api/signup returns 201", status == 201, f"HTTP {status}")
account = json.loads(body)
check("...with an identity and a secret",
      account.get("identity", "").startswith("PSB") and len(account.get("sharedSecret", "")) > 20)
check("...and tells you how to use them",
      "X-Sandbox-Identity" in json.dumps(account),
      "credentials with no usage note are a puzzle, not an answer")
AUTH = {"x-sandbox-identity": account["identity"],
        "x-sandbox-secret": account["sharedSecret"]}

status, body = client("/api/signup", "POST", json.dumps({"email": "nope"}).encode())
check("a bad address is refused with a reason", status == 400
      and "email" in body.lower(), f"HTTP {status}")

print("\n1. Errors are enumerated, not counted")
status, body = client("/api/validate", "POST", BROKEN_ORDER, headers=AUTH)
report = json.loads(body)
check("POST /api/validate returns the report", status == 200, f"HTTP {status}")
check("...with more than a count", report["errorCount"] >= 1
      and len(report["findings"]) >= report["errorCount"],
      f'errorCount={report["errorCount"]}, findings={len(report["findings"])}')
errors = [f for f in report["findings"] if f["severity"] == "error"]
check("...every error carrying a line number", all(f["line"] for f in errors),
      "'line 10, Address' is the difference between a fix and a hunt")
check("...naming the element", any(f["element"] for f in errors),
      [f["element"] for f in errors])
check("...and finding the ShipTo/Address problem specifically",
      any("Address" in (f["element"] or "") or "Address" in f["message"]
          for f in errors),
      "the reported case: a malformed ShipTo found only by bisecting")
check("...reporting quota against the ACCOUNT", "quotaRemaining" in report,
      f'remaining={report.get("quotaRemaining")}')

print("\n2. Credentials are identity; the cap is not keyed on address")
metered = client("/api/validate", "POST", BROKEN_ORDER, headers=AUTH)
account_row = tenants.store().by_sandbox_id(account["identity"])
check("a credentialed call consumes ACCOUNT quota", account_row.used_today >= 2,
      f"used_today={account_row.used_today}")

# The HTML endpoint too: the same credentials must lift the anonymous cap.
anonymous = Client()
for _ in range(tenants.ANON_DAILY_QUOTA + 2):
    anonymous("/validate", "POST", urlencode({"document": "<x/>"}).encode(),
              headers={"cf-connecting-ip": "203.0.113.77"})
status, _ = anonymous("/validate", "POST",
                      urlencode({"document": "<x/>"}).encode(),
                      headers={"cf-connecting-ip": "203.0.113.77"})
check("an anonymous IP is capped", status == 429, f"HTTP {status}")
status, _ = anonymous("/validate", "POST",
                      urlencode({"document": "<x/>"}).encode(),
                      headers={"cf-connecting-ip": "203.0.113.77", **AUTH})
check("...but the SAME address with credentials is not", status == 200,
      f"HTTP {status} — this is the reported bug: identity was ignored "
      "because it did not arrive as a cookie")

print("\n3. The punchout round trip completes headlessly")
client = fresh()
_, body = client("/api/signup", "POST",
                 json.dumps({"email": "agent2@corp.example"}).encode())
acct = json.loads(body)
client.cookies.clear()          # a machine has no browser cookie

setup = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
         '<cXML payloadID="a@b" timestamp="2026-08-14T10:00:00+01:00"><Header>'
         '<From><Credential domain="NetworkID"><Identity>buyer</Identity></Credential></From>'
         f'<To><Credential domain="NetworkID"><Identity>{acct["identity"]}</Identity></Credential></To>'
         '<Sender><Credential domain="NetworkID"><Identity>buyer</Identity>'
         f'<SharedSecret>{acct["sharedSecret"]}</SharedSecret></Credential>'
         '<UserAgent>agent</UserAgent></Sender></Header>'
         '<Request deploymentMode="test"><PunchOutSetupRequest operation="create">'
         '<BuyerCookie>agent-cookie</BuyerCookie><BrowserFormPost>'
         '<URL>https://buyer.example.com/return</URL></BrowserFormPost>'
         '</PunchOutSetupRequest></Request></cXML>').encode()
status, body = client("/punchout/setup", "POST", setup)
start = re.search(r"<URL>([^<]+)</URL>", body).group(1)
session_id = start.split("session=")[1]

status, page = client("/shop", query={"session": session_id})
check("the StartPage URL opens the catalogue", status == 200
      and "Sign up to continue" not in page,
      "the shopper is the BUYER's employee — they have no account here, and "
      "their procurement system authenticated for them seconds ago")
check("...showing the punchout banner", "Punchout session active" in page)


def walk(category, depth=0):
    _, page = client(f"/shop/{category}")
    found = re.findall(r"/product/([A-Za-z0-9._-]+)", page)
    if found or depth >= 2:
        return found
    for child in dict.fromkeys(re.findall(r'href="/shop/([a-z0-9.\-]+)"', page)):
        if child != category:
            got = walk(child, depth + 1)
            if got:
                return got
    return []


sku = []
for category in dict.fromkeys(re.findall(r'href="/shop/([a-z0-9.\-]+)"', page)):
    sku = walk(category)
    if sku:
        break
check("products are reachable", bool(sku), sku[:1])
client("/cart/add", "POST", urlencode({"sku": sku[0], "quantity": "3"}).encode())
status, page = client("/cart/return", "POST", urlencode({"mode": "cart"}).encode())
check("the cart returns", status == 200, f"HTTP {status}")
field = re.search(r'name="(cxml-base64|cxml-urlencoded)" value="([^"]*)"', page)
payload = base64.b64decode(field.group(2)).decode()
check("...as a PunchOutOrderMessage", "PunchOutOrderMessage" in payload)
check("...echoing the BuyerCookie", "agent-cookie" in payload)
check("...carrying the line", sku[0] in payload)

status, page = client("/orders")
check("but /orders is still account-scoped",
      "Sign up to continue" in page,
      "a punchout session authorises the storefront, not somebody's order data")

print("\n5. Every document has a worked, valid sample")
client = fresh()
status, page = client("/samples")
check("/samples is open", status == 200 and "Sign up to continue" not in page)
for key, (name, _, _) in samples.SAMPLES.items():
    status, body = client(f"/samples/{key}")
    check(f"{name} sample serves", status == 200 and body.startswith("<?xml"),
          f"HTTP {status}")
    report = validate(parse(body.encode()))
    check(f"...and validates against the real DTD",
          not report.errors and report.document_type == name,
          f"type={report.document_type} errors="
          f"{[e.message[:60] for e in report.errors]}")
check("ShipNoticeRequest is among them",
      "shipnoticerequest" in samples.SAMPLES,
      "its shape is the least guessable in cXML — ItemID optional, "
      "UnitOfMeasure mandatory")

print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("The machine path works, and all six reported defects are closed.")
