"""What a refused login records, and what a punchout is attributed to.

The digest once reported "16 failed punchouts" for a week, and that number
could say nothing useful: not who, not why, and not even whether they were
punchouts — the 401 was shared with /order and logged as a punchout either
way. These tests pin down the replacement: a reason for every refusal, an
account only when the identity was genuinely ours, and nothing that a person
typed into the wrong field.
"""
import base64
import io
import json
import pathlib
import sys
from contextlib import redirect_stdout
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import orders, sessions, tenants
from app.handler import handler
from app.orders import MemoryOrders
from app.sessions import MemoryStore
from app.tenants import MemoryTenants, Tenant

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


tenants.reset_store(MemoryTenants())
sessions.reset_store(MemoryStore())
orders.reset_store(MemoryOrders())
T = Tenant(tenant_id="acct-1", email="buyer@company.example.org",
           sandbox_id="PSB123456789", shared_secret="the-real-secret-value")
tenants.store().put(T)


def setup_doc(identity, secret):
    secret_el = f"<SharedSecret>{secret}</SharedSecret>" if secret is not None else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="s-1@buyer.example.com" timestamp="2026-09-18T10:00:00+01:00">
 <Header>
  <From><Credential domain="NetworkID"><Identity>buyer</Identity></Credential></From>
  <To><Credential domain="NetworkID"><Identity>{identity}</Identity></Credential></To>
  <Sender><Credential domain="NetworkID"><Identity>buyer</Identity>{secret_el}</Credential>
   <UserAgent>Test</UserAgent></Sender>
 </Header>
 <Request deploymentMode="test"><PunchOutSetupRequest operation="create">
  <BuyerCookie>c-1</BuyerCookie>
  <BrowserFormPost><URL>https://buyer.example.com/return</URL></BrowserFormPost>
 </PunchOutSetupRequest></Request>
</cXML>'''.encode()


def call(path, body=b"", form=None):
    if form is not None:
        body = urlencode(form).encode()
    ev = {"requestContext": {"http": {"method": "POST", "path": path}},
          "queryStringParameters": {}, "headers": {"cf-connecting-ip": "203.0.113.9"},
          "cookies": [], "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = handler(ev)
    log = buf.getvalue()
    events = [json.loads(l) for l in log.splitlines() if l.startswith("{")]
    text = base64.b64decode(r["body"]).decode() if r.get("isBase64Encoded") else r["body"]
    return text, events, log


def named(events, name):
    return [e for e in events if e.get("event") == name]


print("\n1. A successful punchout is attributed")
text, events, _ = call("/punchout/setup", setup_doc(T.sandbox_id, T.shared_secret))
ok = named(events, "punchout_setup")
check("the setup succeeds", "StartPage" in text)
check("...and the event carries the account",
      ok and ok[0].get("account") == "PSB123456789" and ok[0].get("outcome") == "200",
      str(ok))
check("...no refusal is recorded", not named(events, "auth_refused"))


print("\n2. Every refusal says why")
CASES = [
    ("the right identity, the wrong secret", T.sandbox_id, "nope", "wrong_secret", True),
    ("the right identity, no secret at all", T.sandbox_id, None, "no_secret", True),
    ("the sample's placeholder, pasted", "YOUR-SANDBOX-IDENTITY", "x", "placeholder_identity", False),
    ("a well-formed id that is not an account", "PSB000000001", "x", "unknown_sandbox_id", False),
    ("something else entirely", "northgate-supplier", "x", "unknown_identity", False),
    # An empty To is not "no identity": extraction falls back to From, which
    # some buyer systems use instead — so what was presented is From's value.
    ("an empty To, so From's identity is tried", "", "x", "unknown_identity", False),
]
for label, identity, secret, reason, attributed in CASES:
    text, events, _ = call("/punchout/setup", setup_doc(identity, secret))
    refused = named(events, "auth_refused")
    check(f"{label} → {reason}",
          refused and refused[0].get("reason") == reason
          and refused[0].get("path") == "/punchout/setup",
          str(refused))
    if attributed:
        check("...attributed to the account it was aimed at",
              refused and refused[0].get("account") == "PSB123456789")
    else:
        check("...and attributed to nobody", refused and "account" not in refused[0])
    check("...still answered as a cXML 401 in an HTTP 200", 'code="401"' in text)
    check("...and NOT also logged as a failed punchout",
          not named(events, "punchout_setup"), str(named(events, "punchout_setup")))


print("\n3. Nothing a person typed reaches the log")
# The two ways people really get this wrong: their email address in the
# identity field, or the secret itself in the identity field.
for label, identity in [("an email address as the identity", "buyer@company.example.org"),
                        ("the secret pasted as the identity", "the-real-secret-value")]:
    _, events, log = call("/punchout/setup", setup_doc(identity, "whatever"))
    check(f"{label} is refused as unknown_identity",
          named(events, "auth_refused")[0].get("reason") == "unknown_identity")
    check("...and the string itself appears nowhere in the log",
          identity not in log, "a refusal log is not a place to keep secrets")
_, _, log = call("/punchout/setup", setup_doc(T.sandbox_id, "a-wrong-secret-guess"))
check("a wrong secret guess is not logged either", "a-wrong-secret-guess" not in log)


print("\n4. A refused ORDER is not a failed punchout")
_, events, _ = call("/order", setup_doc(T.sandbox_id, "nope"))
refused = named(events, "auth_refused")
check("a refused /order is recorded with its own path",
      refused and refused[0].get("path") == "/order", str(refused))
check("...and not as a punchout_setup", not named(events, "punchout_setup"))


print("\n5. OCI too")
_, events, log = call("/oci/setup", form={"USERNAME": T.sandbox_id, "PASSWORD": "nope",
                                          "HOOK_URL": "https://buyer.example.com/hook"})
refused = named(events, "auth_refused")
check("a wrong OCI password is wrong_secret on /oci/setup",
      refused and refused[0].get("reason") == "wrong_secret"
      and refused[0].get("path") == "/oci/setup", str(refused))


_, events, _ = call("/oci/setup", form={"HOOK_URL": "https://buyer.example.com/hook"})
refused = named(events, "auth_refused")
check("an OCI call with no USERNAME is no_identity",
      refused and refused[0].get("reason") == "no_identity", str(refused))


print("\n6. Behind Ariba Network, the secret alone is enough")
# What the network actually forwards, as a buyer reported it: its own IDs in
# From and To, sysadmin@ariba.com in Sender, and only the shared secret passed
# through from the supplier's configuration. None of it is our identity.
def network_doc(secret, request_xml):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="an-1@ariba.com" timestamp="2026-10-13T10:00:00+01:00">
 <Header>
  <From><Credential domain="NetworkID"><Identity>AN01000000001-T</Identity></Credential></From>
  <To><Credential domain="NetworkID"><Identity>AN01000000002-T</Identity></Credential></To>
  <Sender><Credential domain="AribaNetworkUserId"><Identity>sysadmin@ariba.com</Identity>
   <SharedSecret>{secret}</SharedSecret></Credential>
   <UserAgent>Your Procurement System 1.0</UserAgent></Sender>
 </Header>
 <Request deploymentMode="test">{request_xml}</Request>
</cXML>'''.encode()


NETWORK_SETUP = ('<PunchOutSetupRequest operation="create"><BuyerCookie>c-an</BuyerCookie>'
                 '<BrowserFormPost><URL>https://buyer.example.com/return</URL>'
                 '</BrowserFormPost></PunchOutSetupRequest>')
NETWORK_ORDER = ('<OrderRequest><OrderRequestHeader orderID="PO-AN-1" '
                 'orderDate="2026-10-13T10:00:00+01:00" type="new">'
                 '<Total><Money currency="GBP">10.00</Money></Total></OrderRequestHeader>'
                 '<ItemOut quantity="1" lineNumber="1"><ItemID>'
                 '<SupplierPartID>MSC-1001</SupplierPartID></ItemID></ItemOut>'
                 '</OrderRequest>')

text, events, _ = call("/punchout/setup", network_doc(T.shared_secret, NETWORK_SETUP))
check("a network-routed punchout gets a StartPage", "StartPage" in text, text[:200])
check("...is attributed to the account the secret belongs to",
      any(e.get("account") == "PSB123456789" for e in named(events, "punchout_setup")),
      str(named(events, "punchout_setup")))
by_secret = named(events, "auth_by_secret")
check("...and is recorded as a login by secret alone",
      by_secret and by_secret[0].get("path") == "/punchout/setup"
      and by_secret[0].get("account") == "PSB123456789", str(by_secret))
check("...not as a refusal", not named(events, "auth_refused"))
check("the network's own IDs are still echoed back, so you can see what arrived",
      "to=AN01000000002-T" in text and "sender=sysadmin@ariba.com" in text)

text, events, _ = call("/order", network_doc(T.shared_secret, NETWORK_ORDER))
check("a network-routed order is accepted too",
      'code="401"' not in text and not named(events, "auth_refused"), text[:300])
check("...and recorded as a login by secret alone",
      named(events, "auth_by_secret")
      and named(events, "auth_by_secret")[0].get("path") == "/order")

text, events, log = call("/punchout/setup",
                         network_doc("not-anybodys-secret", NETWORK_SETUP))
refused = named(events, "auth_refused")
check("a network-routed request with a secret nobody holds is still refused",
      'code="401"' in text and refused
      and refused[0].get("reason") == "unknown_identity", str(refused))
check("...attributed to nobody", refused and "account" not in refused[0])
check("...and the secret it tried is not logged", "not-anybodys-secret" not in log)
check("the 401 says the secret alone works behind Ariba Network",
      "the secret alone is enough" in text)

# The placeholder from our own sample, with a real secret, is the request in
# the buyer's report — it should work rather than be refused for the identity.
text, events, _ = call("/punchout/setup",
                       setup_doc("YOUR-SANDBOX-IDENTITY", T.shared_secret))
check("the sample's placeholder identity with a real secret is let in",
      "StartPage" in text and named(events, "auth_by_secret"))


print("\n7. A real identity is never served as another account")
U = Tenant(tenant_id="acct-2", email="other@company.example.org",
           sandbox_id="PSB222222222", shared_secret="the-other-accounts-secret")
tenants.store().put(U)
text, events, _ = call("/punchout/setup", setup_doc(T.sandbox_id, U.shared_secret))
refused = named(events, "auth_refused")
check("account A's identity with account B's secret is wrong_secret for A",
      'code="401"' in text and refused and refused[0].get("reason") == "wrong_secret"
      and refused[0].get("account") == "PSB123456789", str(refused))
check("...and B is not logged in instead", not named(events, "auth_by_secret"))

import time as _time
E = Tenant(tenant_id="acct-3", email="gone@company.example.org",
           sandbox_id="PSB333333333", shared_secret="an-expired-accounts-secret",
           created_at=_time.time() - 400 * 86400)
tenants.store().put(E)
text, _, _ = call("/punchout/setup", network_doc(E.shared_secret, NETWORK_SETUP))
check("an expired account's secret logs nobody in", 'code="401"' in text)


print("\n8. The DynamoDB store follows a pointer, then checks the secret itself")
# MemoryTenants scans; DynamoTenants follows a pointer row. They have to agree,
# and the second can only be tested with a table — a dict is enough of one.
class FakeTable:
    def __init__(self):
        self.rows = {}

    def put_item(self, Item):
        self.rows[(Item["pk"], Item["sk"])] = dict(Item)

    def get_item(self, Key):
        found = self.rows.get((Key["pk"], Key["sk"]))
        return {"Item": dict(found)} if found else {}


dyn = tenants.DynamoTenants("unused")
dyn._table = FakeTable()
dyn.put(Tenant(tenant_id="acct-d", email="d@company.example.org",
               sandbox_id="PSB444444444", shared_secret="the-dynamo-secret"))
pointer_keys = [pk for pk, _ in dyn._table.rows if pk.startswith("SECRET#")]
check("put writes a SECRET# pointer", len(pointer_keys) == 1, str(list(dyn._table.rows)))
check("...keyed on a digest, so the secret is not in the key",
      pointer_keys and "the-dynamo-secret" not in pointer_keys[0])
found = dyn.by_shared_secret("the-dynamo-secret")
check("by_shared_secret finds the account", found and found.sandbox_id == "PSB444444444")
check("a wrong secret finds nothing", dyn.by_shared_secret("a-wrong-secret") is None)
check("an empty secret finds nothing", dyn.by_shared_secret("") is None)
# A pointer that names an account whose secret does not match — debris, or
# somebody who can write the table — must authenticate nothing.
dyn._table.put_item({"pk": f"SECRET#{tenants.secret_digest('forged')}",
                     "sk": "POINTER", "tenant_id": "acct-d", "expires_at": 0})
check("a pointer to an account with a different secret authenticates nothing",
      dyn.by_shared_secret("forged") is None)


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("Every refused login says why, and nothing anybody typed is kept.")
