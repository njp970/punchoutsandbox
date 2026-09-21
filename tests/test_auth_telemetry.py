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


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("Every refused login says why, and nothing anybody typed is kept.")
