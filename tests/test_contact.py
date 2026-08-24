"""The contact form, and the anonymous-quota alert.

The interesting assertions are not "does the form work". They are:

  * that the recipient cannot be influenced by anything a visitor submits,
    which is the property separating a contact form from an open relay;
  * that a failure to send does not lose the message;
  * that hitting the anonymous /validate limit produces an operator alert,
    and that a hundred visitors hitting it do not produce a hundred emails.
"""
import base64
import sys
from urllib.parse import urlencode

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import contact, mailer, sessions, signup, tenants
from app.handler import handler
from app.sessions import MemoryStore
from app.tenants import MemoryTenants, Tenant

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def go(path, method="GET", form=None, cookies=None, ip="203.0.113.9"):
    body = urlencode(form or {}).encode() if form is not None else b""
    ev = {"requestContext": {"http": {"method": method, "path": path}},
          "queryStringParameters": {},
          "headers": {"cf-connecting-ip": ip},
          "cookies": cookies or [],
          "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    r = handler(ev)
    b = r["body"]
    return (r["statusCode"],
            base64.b64decode(b).decode() if r.get("isBase64Encoded") else b)


def fresh():
    tenants.reset_store(MemoryTenants())
    sessions.reset_store(MemoryStore())
    mailer.outbox.clear()


GOOD = {"email": "someone@buyer.example", "name": "A Person",
        "topic": "conformance", "message": "Coupa truncates Description at 254."}


print("\n1. The form is reachable without an account")
fresh()
status, body = go("/contact")
check("GET /contact is 200 with no cookie", status == 200, f"got {status}")
check("it is the form, not the gate", "<textarea" in body)

print("\n2. A submission reaches the outbox")
fresh()
status, body = go("/contact", "POST", GOOD)
check("POST /contact is 200", status == 200, f"got {status}")
check("thanks the sender", "Sent" in body)
check("exactly one message queued", len(mailer.outbox) == 1,
      f"{len(mailer.outbox)} queued")
if mailer.outbox:
    sent = mailer.outbox[0]
    check("the message text is carried", "Coupa truncates" in sent["body"])
    check("reply-to is the submitter", sent["reply_to"] == "someone@buyer.example")
    check("the body marks the text as untrusted",
          "visitor-supplied" in sent["body"])
    check("no raw IP in the body", "203.0.113.9" not in sent["body"])

print("\n3. The recipient cannot be influenced by the submitter")
fresh()
# Every field a spammer would try to use to redirect delivery.
go("/contact", "POST", {**GOOD,
                        "to": "victim@example.com",
                        "recipient": "victim@example.com",
                        "cc": "victim@example.com",
                        "email": "attacker@example.com\nBcc: victim@example.com"})
check("an address carrying a header break is refused outright",
      len(mailer.outbox) == 0, f"{len(mailer.outbox)} queued")

fresh()
go("/contact", "POST", {**GOOD, "to": "victim@example.com"})
check("a stray `to` field is ignored, not honoured",
      len(mailer.outbox) == 1 and "to" not in mailer.outbox[0])
check("mailer.send takes no recipient argument at all",
      "to" not in mailer.send.__code__.co_varnames,
      "if this fails someone added one — that is the open relay")

print("\n4. Rubbish is refused")
fresh()
status, _ = go("/contact", "POST", {**GOOD, "email": "not-an-address"})
check("a malformed address is a 400", status == 400, f"got {status}")
check("nothing queued", len(mailer.outbox) == 0)

fresh()
status, _ = go("/contact", "POST", {**GOOD, "message": "hi"})
check("a too-short message is a 400", status == 400, f"got {status}")

print("\n5. The honeypot")
fresh()
status, body = go("/contact", "POST", {**GOOD, contact.HONEYPOT_FIELD: "http://x"})
check("a filled honeypot still returns the success page", status == 200)
check("...and it thanks them", "Sent" in body,
      "a bot told it failed retries; a bot told it succeeded leaves")
check("...but nothing was queued", len(mailer.outbox) == 0)

print("\n6. Per-IP daily cap")
fresh()
for n in range(tenants.CONTACT_DAILY_LIMIT):
    go("/contact", "POST", {**GOOD, "message": f"message number {n} here"})
check(f"{tenants.CONTACT_DAILY_LIMIT} messages get through",
      len(mailer.outbox) == tenants.CONTACT_DAILY_LIMIT,
      f"{len(mailer.outbox)} queued")
status, _ = go("/contact", "POST", GOOD)
check("the next one is a 429", status == 429, f"got {status}")
check("...and is not queued",
      len(mailer.outbox) == tenants.CONTACT_DAILY_LIMIT)
status, _ = go("/contact", "POST", GOOD, ip="198.51.100.4")
check("a different IP is unaffected", status == 200, f"got {status}")

print("\n7. Long input is truncated, not rejected")
fresh()
go("/contact", "POST", {**GOOD, "message": "x" * 50_000})
check("an oversized message still sends",
      len(mailer.outbox) == 1)
check("...truncated to the cap",
      len(mailer.outbox[0]["body"]) < contact.MAX_MESSAGE + 500)

print("\n8. Hitting the anonymous /validate limit alerts the operator")
fresh()
doc = b"<?xml version='1.0'?><nonsense/>"
for _ in range(tenants.ANON_DAILY_QUOTA):
    go("/validate", "POST", {"document": doc.decode()})
alerts_before = [m for m in mailer.outbox if m["kind"] == "quota-alert"]
check("no alert while under the limit", len(alerts_before) == 0,
      f"{len(alerts_before)} sent")

status, _ = go("/validate", "POST", {"document": doc.decode()})
check("the request over the limit is a 429", status == 429, f"got {status}")
alerts = [m for m in mailer.outbox if m["kind"] == "quota-alert"]
check("an alert is raised", len(alerts) == 1, f"{len(alerts)} sent")
if alerts:
    check("it names the limit that was hit",
          str(tenants.ANON_DAILY_QUOTA) in alerts[0]["body"])
    check("it says where to change it",
          "ANON_DAILY_QUOTA" in alerts[0]["body"])
    check("no raw IP in the alert", "203.0.113.9" not in alerts[0]["body"])

print("\n9. Alerts are capped so one bad day cannot fill the mailbox")
fresh()
# Fresh IP per visitor, each one exhausting its own quota.
for visitor in range(tenants.ALERT_DAILY_LIMIT + 4):
    ip = f"198.51.100.{visitor + 10}"
    for _ in range(tenants.ANON_DAILY_QUOTA + 1):
        go("/validate", "POST", {"document": doc.decode()}, ip=ip)
alerts = [m for m in mailer.outbox if m["kind"] == "quota-alert"]
check(f"at most {tenants.ALERT_DAILY_LIMIT} alerts in a day",
      len(alerts) == tenants.ALERT_DAILY_LIMIT, f"{len(alerts)} sent")

print("\n10. The counters are independent of each other")
fresh()
for _ in range(tenants.ANON_DAILY_QUOTA + 1):
    go("/validate", "POST", {"document": doc.decode()})
status, _ = go("/contact", "POST", GOOD)
check("exhausting /validate does not block /contact", status == 200,
      f"got {status} — someone rate-limited out of the tool must still be "
      "able to say so")

print("\n11. A signed-in visitor's account is attached to the message")
fresh()
tenant = Tenant(tenant_id="acct-1", email="buyer@corp.example",
                company="Corp")
tenants.store().put(tenant)
status, _ = go("/contact", "POST", GOOD, cookies=[f"pst={tenant.tenant_id}"])
check("submission succeeds", status == 200, f"got {status}")
check("the account is named in the body",
      mailer.outbox and tenant.email in mailer.outbox[0]["body"])

print("\n12. The digest tells real usage from ours")
# The point of the digest is subtraction. A first version reported "nobody has
# used it" directly above six orders — all from the QA suite — because it
# filtered test ACCOUNTS and then counted every order. A number contradicting
# the headline above it destroys the credibility of both.
from app import digest

check("the QA domain is recognised as test traffic",
      digest._is_test("qa+1@punchoutsandbox.example"))
check("...even when the address is malformed",
      digest._is_test("qa@punchoutsandbox.example}"),
      "a shell quoting slip once created exactly this, and an anchored "
      "pattern let it survive every cleanup")
check("a real address is not", not digest._is_test("someone@company.com"))
check("case does not matter", digest._is_test("QA@PunchOutSandbox.Example"))


print("\n13. Events name the account without logging an address")
# signup.html promises we store an address, a company and a counter. An email
# in a log line is a fourth thing, accumulating where nobody audits it — so
# events carry the ISSUED sandbox id and the digest joins it back to an
# address from DynamoDB, keeping the personal data in the declared place.
import io as _io
from contextlib import redirect_stdout as _redirect
from app import telemetry as _tel
from app.tenants import Tenant as _T

person = _T(tenant_id="t9", email="someone@company.com", sandbox_id="PSB999",
            shared_secret="s")
check("account_of returns the issued id", _tel.account_of(person) == "PSB999")
check("...and None for a stranger", _tel.account_of(None) is None)

buffer = _io.StringIO()
with _redirect(buffer):
    _tel.event("validate", account=_tel.account_of(person), errors=2)
emitted = buffer.getvalue()
check("the event names the account", '"account": "PSB999"' in emitted, emitted.strip())
check("...and the address appears nowhere in it",
      "someone@company.com" not in emitted,
      "an email in a log is personal data in an undeclared place")


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All contact-form checks passed.")
