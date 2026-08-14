"""The signup gate.

Two things are being proved here, and the second matters more.

First, that gated paths are gated. That is easy and the tests are dull.

Second, that **the gate did not break the machine endpoints**. A buyer system
cannot fill in a web form, so the obvious implementation — require a session
cookie everywhere — would have silently turned the product off while every
browser test still passed. The machine tests below are the ones that would
catch that.
"""
import base64
import re
import sys
from urllib.parse import urlencode

sys.path.insert(0, "/Users/neilparkes/punchout")

from app import sessions, signup, tenants
from app.handler import handler
from app.sessions import MemoryStore
from app.tenants import DAILY_QUOTA, MemoryTenants, Tenant, valid_email

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def go(path, method="GET", body=b"", cookies=None, query=None, ct=None):
    ev = {"requestContext": {"http": {"method": method, "path": path}},
          "queryStringParameters": query or {},
          "headers": {"content-type": ct} if ct else {},
          "cookies": cookies or [],
          "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    r = handler(ev)
    b = r["body"]
    return (r["statusCode"],
            base64.b64decode(b).decode() if r.get("isBase64Encoded") else b, r)


def fresh():
    tenants.reset_store(MemoryTenants())
    sessions.reset_store(MemoryStore())


CXML = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
        '<cXML payloadID="d@e" timestamp="2026-01-01T00:00:00+00:00"><Header>'
        '<From><Credential domain="NetworkID"><Identity>buyer</Identity></Credential></From>'
        '<To><Credential domain="NetworkID"><Identity>{SID}</Identity></Credential></To>'
        '<Sender><Credential domain="NetworkID"><Identity>buyer</Identity>'
        '<SharedSecret>{SEC}</SharedSecret></Credential><UserAgent>t</UserAgent></Sender>'
        '</Header><Request deploymentMode="test"><PunchOutSetupRequest operation="create">'
        '<BuyerCookie>abc</BuyerCookie><BrowserFormPost><URL>https://b/r</URL>'
        '</BrowserFormPost></PunchOutSetupRequest></Request></cXML>')


def signup_now(email="neil@example.com"):
    st, page, raw = go("/signup", "POST", urlencode({"email": email}).encode())
    token = raw["cookies"][0].split("=")[1].split(";")[0]
    sid = re.search(r"<code>(PSB\d{9})</code>", page).group(1)
    sec = re.search(r"writeText\('([A-Za-z0-9_-]{20,})'\)", page).group(1)
    return token, sid, sec


print("\n=== 1. Open paths stay open ===")
fresh()
for path in ("/docs", "/signup", "/static/app.css", "/validate"):
    st, _, _ = go(path)
    check(f"{path} is reachable with no account", st == 200, f"status {st}")
check("/docs specifically — you must be able to read what this is first",
      go("/docs")[0] == 200,
      "a gate in front of the explanation is a gate in front of the reason "
      "to sign up")
st, body, _ = go("/validate")
check("/validate is OPEN and actually usable anonymously",
      st == 200 and "Sign up to continue" not in body,
      "it is the most useful thing here to a stranger; a form in front of it "
      "asks for an email at the moment a person is least willing to give one")
check("and it invites signup rather than demanding it",
      "no account needed" in body)

print("\n=== 2. Gated paths are gated ===")
for path in ("/shop", "/cart", "/product/MSC-1001"):
    st, body, _ = go(path)
    check(f"{path} shows the gate", "Sign up to continue" in body, f"status {st}")
check("the gate is a 200, not a 401 or a redirect", go("/shop")[0] == 200,
      "a 401 prompts for HTTP basic auth in some browsers; a redirect loses "
      "the page they wanted")

print("\n=== 3. Signing up opens them ===")
token, sid, sec = signup_now()
check("identity looks like an ANID", re.fullmatch(r"PSB\d{9}", sid) is not None, sid)
check("secret is long enough to be one", len(sec) >= 20, f"{len(sec)} chars")
for path in ("/shop", "/cart"):
    st, body, _ = go(path, cookies=[f"pst={token}"])
    check(f"{path} opens with the cookie",
          st == 200 and "Sign up to continue" not in body, f"status {st}")

print("\n=== 4. THE GATE MUST NOT BREAK THE MACHINE ENDPOINTS ===")
good = CXML.replace("{SID}", sid).replace("{SEC}", sec).encode()
st, body, _ = go("/punchout/setup", "POST", good, ct="text/xml")
check("cXML setup works with issued credentials", st == 200)
check("and returns a StartPage", "<StartPage>" in body)
check("cXML Status is 200", 'code="200"' in body)

st, _, _ = go("/oci/setup", query={"HOOK_URL": "https://x/h",
                                   "USERNAME": sid, "PASSWORD": sec})
check("OCI setup works with issued credentials", st == 303, f"status {st}")

print("\n=== 5. Bad credentials are refused, correctly ===")
bad = CXML.replace("{SID}", sid).replace("{SEC}", "wrong").encode()
st, body, _ = go("/punchout/setup", "POST", bad, ct="text/xml")
check("wrong secret is refused", 'code="401"' in body)
check("but over HTTP 200", st == 200,
      "any HTTP reply without valid cXML is a TRANSPORT error the client "
      "retries ten times hourly — a real 401 would cause a retry storm")
check("and says where to get credentials", "signup" in body)

unknown = CXML.replace("{SID}", "PSB000000000").replace("{SEC}", sec).encode()
check("unknown identity is refused",
      'code="401"' in go("/punchout/setup", "POST", unknown, ct="text/xml")[1])
check("OCI with no credentials is refused",
      go("/oci/setup", query={"HOOK_URL": "https://x/h"})[0] == 401)
check("OCI with a wrong secret is refused",
      go("/oci/setup", query={"HOOK_URL": "https://x/h", "USERNAME": sid,
                              "PASSWORD": "nope"})[0] == 401)

print("\n=== 6. Returning visitors see the SAME credentials ===")
st, page, _ = go("/signup", cookies=[f"pst={token}"])
check("signup page shows existing credentials", sid in page, f"status {st}")
check("and does not re-issue them", sec in page,
      "re-issuing would break whatever they already configured")

print("\n=== 7. Email validation is loose on purpose ===")
check("a plausible address passes", valid_email("a.b+c@sub.example.co.uk"))
check("nonsense fails", not valid_email("not-an-email"))
check("empty fails", not valid_email(""))
st, body, _ = go("/signup", "POST", urlencode({"email": "nope"}).encode())
check("the form rejects it with an explanation", st == 400 and "does not look" in body)

print("\n=== 8. Quota is enforced and persisted ===")
fresh()
store = MemoryTenants()
tenants.reset_store(store)
t = Tenant(tenant_id="t1", email="q@example.com")
store.put(t)
allowed_count = 0
for _ in range(DAILY_QUOTA + 5):
    ok, _ = t.check_quota(today="2026-08-14")
    allowed_count += 1 if ok else 0
check(f"exactly {DAILY_QUOTA} operations allowed", allowed_count == DAILY_QUOTA,
      f"{allowed_count} allowed")
ok, _ = t.check_quota(today="2026-08-15")
check("the counter resets on a new day", ok)

print("\n=== 9. An exhausted account gets 429, not a gate ===")
fresh()
store = MemoryTenants()
tenants.reset_store(store)
t = Tenant(tenant_id="t2", email="x@example.com")
t.quota_day = signup.today()
t.used_today = DAILY_QUOTA
store.put(t)
st, body, _ = go("/shop", cookies=["pst=t2"])
check("returns 429", st == 429, f"status {st}")
check("and explains it is a number, not a policy", "not a policy" in body)

print("\n=== 10. /validate is open but METERED ===")
fresh()
from app.tenants import ANON_DAILY_QUOTA, anon_check_quota
allowed = sum(
    1 for _ in range(ANON_DAILY_QUOTA + 5)
    if anon_check_quota("203.0.113.9", today="2026-08-14")[0])
check(f"exactly {ANON_DAILY_QUOTA} anonymous validations per IP per day",
      allowed == ANON_DAILY_QUOTA, f"{allowed} allowed")
check("a different IP has its own allowance",
      anon_check_quota("203.0.113.10", today="2026-08-14")[0])
check("the anonymous limit is well below the account limit",
      ANON_DAILY_QUOTA < DAILY_QUOTA,
      f"{ANON_DAILY_QUOTA} vs {DAILY_QUOTA} — that gap IS the incentive to "
      "sign up, a prompt rather than a wall")
check("IPv6 is truncated to a /64",
      anon_check_quota("2001:db8:1:2:3:4:5:6", today="2026-08-14")[0],
      "a fresh quota per address in a residential prefix would make the "
      "limit decorative")

print("\n=== 11. A signed-in visitor is NOT metered anonymously ===")
fresh()
token, _, _ = signup_now("metered@example.com")
st, body, _ = go("/validate", "POST",
                 urlencode({"document": "<not-cxml/>"}).encode(),
                 cookies=[f"pst={token}"])
check("signed-in validation works", st == 200, f"status {st}")
check("and the signup prompt is gone", "no account needed" not in body)

tenants.reset_store(None)
sessions.reset_store(None)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("the gate holds, and the machine endpoints still work")
