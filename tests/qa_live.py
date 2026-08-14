"""End-to-end QA against a DEPLOYED instance.

Unlike everything else in `tests/`, this suite talks to a real server over the
real network. It exists because the unit suites all run against in-process
stores and an in-process handler, and every bug that has actually reached
production here slipped through precisely that gap — DynamoDB rejecting a
float that `MemoryOrders` accepted, a Lambda Function URL rejecting a Host
header no local test sends, a nav link to a route that did not exist.

    .venv/bin/python tests/qa_live.py [https://punchoutsandbox.com]

It signs up a throwaway account and drives the full supplier flow: punchout,
shop, cart return, purchase order, confirmation, ship notice, invoice,
delivery. Then it attacks itself.

WHAT IT WILL DO TO THE TARGET
  * create one account (an email address is the only thing stored)
  * create punchout sessions and one purchase order
  * generate documents and attempt ONE outbound delivery to example.com
It does not delete anything, and everything it creates expires on its own.

Exit code is 0 only if nothing FAILED. WARN items are printed and do not fail
the run — they are judgement calls for a human.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://punchoutsandbox.com").rstrip("/")

results: list[tuple[str, str, str]] = []   # (level, name, detail)
_section = ""


def section(title: str) -> None:
    global _section
    _section = title
    print(f"\n\033[1m{title}\033[0m")


def record(level: str, name: str, detail: str = "") -> None:
    colour = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m",
              "INFO": "\033[36m"}[level]
    print(f"  {colour}[{level}]\033[0m {name}")
    if detail:
        print(f"         {detail}")
    results.append((level, f"{_section} :: {name}", detail))


def check(name: str, condition: bool, detail: str = "", *,
          warn_only: bool = False) -> bool:
    record("PASS" if condition else ("WARN" if warn_only else "FAIL"), name, detail)
    return condition


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    # Redirects are NOT followed. Every redirect in this application is a
    # deliberate 303 after a POST, and following them silently would hide a
    # redirect loop or a wrong Location.
    type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {
        "redirect_request": lambda *a, **k: None})(),
)


class Reply:
    def __init__(self, status: int, body: bytes, headers: dict, url: str):
        self.status = status
        self.raw = body
        self.text = body.decode("utf-8", "replace")
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.url = url

    @property
    def location(self) -> str:
        return self.headers.get("location", "")


def request(path: str, method: str = "GET", *, data: Optional[bytes] = None,
            form: Optional[dict] = None, headers: Optional[dict] = None,
            use_cookies: bool = True, timeout: int = 45) -> Reply:
    url = path if path.startswith("http") else BASE + path
    body = data
    hdrs = dict(headers or {})
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs.setdefault("content-type", "application/x-www-form-urlencoded")
    # An explicit User-Agent, because the absence of one is itself a finding:
    # Cloudflare's Browser Integrity Check answers 403 (error 1010) to an empty
    # UA and to Python-urllib's default. Section A tests that separately; the
    # rest of the suite should not trip over it on every request.
    hdrs.setdefault("user-agent", "PunchOutSandbox-QA/1.0")
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    opener = _opener if use_cookies else urllib.request.build_opener(
        type("NR", (urllib.request.HTTPRedirectHandler,),
             {"redirect_request": lambda *a, **k: None})())
    try:
        with opener.open(req, timeout=timeout) as response:
            return Reply(response.status, response.read(),
                         dict(response.getheaders()), url)
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read(), dict(exc.headers or {}), url)
    except Exception as exc:                       # network-level failure
        return Reply(0, str(exc).encode(), {}, url)


def cxml_status(text: str) -> tuple[str, str]:
    match = re.search(r'<Status[^>]*code="(\d+)"[^>]*text="([^"]*)"', text)
    return (match.group(1), match.group(2)) if match else ("", "")


# =========================================================================== #
section("A. Reachability and transport")
# =========================================================================== #
home = request("/")
check("the site answers", home.status in (200, 301, 302, 303),
      f"HTTP {home.status} from {BASE}")
if home.status == 0:
    print("\nTarget unreachable — aborting.")
    sys.exit(1)

check("served through Cloudflare",
      "cf-ray" in home.headers or "cloudflare" in home.headers.get("server", ""),
      f"server={home.headers.get('server')} — the rate limiting and the origin "
      "bypass protection both depend on this")

plain = request("http://punchoutsandbox.com/docs", use_cookies=False)
check("plain HTTP redirects rather than serving content",
      plain.status in (301, 302, 308),
      f"HTTP {plain.status} -> {plain.location or '(no Location)'} — serving over "
      "http means a session cookie can travel in cleartext, and this site issues "
      "shared secrets")

# THE MACHINE-CLIENT CHECK. This product's users are HTTP clients inside
# procurement systems, not browsers, and Cloudflare's Browser Integrity Check
# blocks several of them outright with a 403 and no explanation.
section("A2. Machine clients are not blocked at the edge")
MACHINE_AGENTS = [
    ("", "no User-Agent at all — common in enterprise integration middleware"),
    ("Python-urllib/3.12", "the Python standard library"),
    ("python-requests/2.31.0", "the most common Python HTTP client"),
    ("Java/17.0.1", "a plain Java URLConnection"),
    ("Apache-HttpClient/4.5.13", "the usual Java/SAP client"),
    ("libwww-perl/6.72", "legacy middleware, and still out there"),
    ("Go-http-client/2.0", "Go"),
    ("PHP-SOAP/8.2", "PHP"),
]
# The MACHINE endpoints must accept every client — this is a hard failure.
for agent, who in MACHINE_AGENTS:
    reply = request("/punchout/setup", "POST", data=b"<cXML/>",
                    headers={"user-agent": agent}, use_cookies=False)
    check(f"POST /punchout/setup as {agent or '(no UA)'!r}",
          reply.status == 200 and "cXML" in reply.text,
          f"HTTP {reply.status} — {who}. A buyer system blocked here gets a "
          "Cloudflare HTML error page instead of a cXML Status, with no way "
          "to tell it apart from an outage.")
for path in ("/oci/setup", "/order"):
    reply = request(path, "POST", data=b"x", headers={"user-agent": "Python-urllib/3.12"},
                    use_cookies=False)
    check(f"POST {path} as Python-urllib", reply.status in (200, 400, 401),
          f"HTTP {reply.status}")

# The BROWSER pages keep Browser Integrity Check on purpose, so an exotic
# user-agent being turned away there is a deliberate trade, not a defect.
# Reported as INFO so a change in either direction is visible.
for agent, who in MACHINE_AGENTS[:3]:
    reply = request("/docs", headers={"user-agent": agent}, use_cookies=False)
    record("INFO", f"/docs as {agent or '(no UA)'!r}: HTTP {reply.status}",
           "browser pages keep the integrity check; only the machine "
           "endpoints are exempted" if reply.status != 200 else "")

section("A3. Response headers")
docs = request("/docs")
hsts = docs.headers.get("strict-transport-security", "")
check("HSTS is set", bool(hsts),
      hsts or "absent — a first visit over http is interceptable even though "
      "it redirects", warn_only=True)

record("INFO", "x-frame-options is deliberately absent",
       "some buyer platforms open a punchout catalogue in an IFRAME; a "
       "supplier that refuses to be framed does not work for them, and a cart "
       "of invented products is not worth clickjacking")
for header, why in [
    ("x-content-type-options", "stops MIME sniffing of stored documents"),
    ("content-security-policy", "defence in depth for stored-document XSS"),
    ("referrer-policy", "stops session URLs leaking to third parties"),
]:
    check(f"{header} present", header in docs.headers,
          docs.headers.get(header, f"absent — {why}"), warn_only=True)


# =========================================================================== #
section("B. Public pages render")
# =========================================================================== #
for path, marker in [("/docs", "How to use this"),
                     ("/validate", "<textarea"),
                     ("/contact", "Get in touch"),
                     ("/signup", "Get access")]:
    reply = request(path)
    check(f"GET {path}", reply.status == 200 and marker in reply.text,
          f"HTTP {reply.status}")

css = request("/static/app.css")
check("stylesheet serves", css.status == 200 and "text/css" in css.headers.get("content-type", ""),
      f"HTTP {css.status} {css.headers.get('content-type')}")

# Every internal link on the public pages must resolve. /sessions was linked
# from the nav for weeks while 404ing, which is the bug this catches.
links = set()
for path in ("/docs", "/validate", "/contact", "/signup"):
    links |= set(re.findall(r'href="(/[^"{#]*)"', request(path).text))
broken = []
for link in sorted(links):
    reply = request(link)
    if reply.status >= 400:
        broken.append(f"{link} -> {reply.status}")
check(f"all {len(links)} internal links resolve", not broken, "; ".join(broken))


# =========================================================================== #
section("C. The gate")
# =========================================================================== #
GATE = "Sign up to continue"
for path in ("/docs", "/validate", "/contact"):
    reply = request(path, use_cookies=False)
    check(f"{path} is open", GATE not in reply.text, f"HTTP {reply.status}")
for path in ("/shop", "/cart", "/orders", "/settings", "/sessions", "/console"):
    reply = request(path, use_cookies=False)
    check(f"{path} is gated", GATE in reply.text, f"HTTP {reply.status}")

anon_setup = request("/punchout/setup", "POST", data=b"<cXML/>", use_cookies=False)
code, _ = cxml_status(anon_setup.text)
check("/punchout/setup refuses unknown credentials in cXML", code == "401",
      f"HTTP {anon_setup.status}, cXML {code or 'none'}")
check("...over HTTP 200, not an HTTP error code", anon_setup.status == 200,
      f"got {anon_setup.status} — an HTTP error makes clients retry hourly for "
      "ten hours")

anon_order = request("/order", "POST", data=b"<cXML/>", use_cookies=False)
check("/order refuses unknown credentials the same way",
      cxml_status(anon_order.text)[0] == "401", f"HTTP {anon_order.status}")


# =========================================================================== #
section("D. Signup issues working credentials")
# =========================================================================== #
stamp = int(time.time())
email = f"qa+{stamp}@punchoutsandbox.example"
signup = request("/signup", "POST", form={"email": email, "company": "QA Run"})
check("signup succeeds", signup.status == 200, f"HTTP {signup.status}")

cookie_header = signup.headers.get("set-cookie", "")
check("the session cookie is HttpOnly", "HttpOnly" in cookie_header, cookie_header)
check("...and SameSite-constrained", "SameSite" in cookie_header, cookie_header)
check("...and marked Secure", "Secure" in cookie_header,
      cookie_header or "absent — the cookie may be sent over plaintext http")

identity = (re.search(r"<code>(PSB\d{9})</code>", signup.text) or [None, ""])[1]
secret_match = re.search(r"<code>([A-Za-z0-9_-]{30,})</code>", signup.text)
secret = secret_match.group(1) if secret_match else ""
check("an identity is issued", bool(identity), identity or "not found on page")
check("a shared secret is issued", len(secret) >= 30,
      f"{len(secret)} chars" if secret else "not found on page")

if not (identity and secret):
    print("\nNo credentials — cannot continue the API tests.")
    sys.exit(1)

for path in ("/shop", "/orders", "/settings"):
    reply = request(path)
    check(f"{path} is now reachable", GATE not in reply.text and reply.status == 200,
          f"HTTP {reply.status}")

dup = request("/signup", "POST", form={"email": "not-an-email", "company": ""})
check("a malformed email is rejected", "does not look" in dup.text.lower()
      or "valid" in dup.text.lower(), f"HTTP {dup.status}")


CXML_HEADER = (
    '<Header>'
    '<From><Credential domain="NetworkID"><Identity>qa-buyer</Identity></Credential></From>'
    f'<To><Credential domain="NetworkID"><Identity>{identity}</Identity></Credential></To>'
    '<Sender><Credential domain="NetworkID"><Identity>qa-buyer</Identity>'
    f'<SharedSecret>{secret}</SharedSecret></Credential>'
    '<UserAgent>PunchOut Sandbox QA</UserAgent></Sender>'
    '</Header>')


def envelope(body: str, *, dtd: str = "cXML") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/{dtd}.dtd">'
        f'<cXML payloadID="qa-{stamp}@punchoutsandbox.example" '
        'timestamp="2026-08-14T10:00:00+01:00">'
        + CXML_HEADER + body + "</cXML>").encode()


# =========================================================================== #
section("E. cXML punchout — the handshake")
# =========================================================================== #
setup_body = (
    '<Request deploymentMode="test"><PunchOutSetupRequest operation="create">'
    '<BuyerCookie>qa-cookie-1</BuyerCookie>'
    '<BrowserFormPost><URL>https://buyer.example.com/punchout/return</URL>'
    '</BrowserFormPost></PunchOutSetupRequest></Request>')
setup = request("/punchout/setup", "POST", data=envelope(setup_body),
                headers={"content-type": "text/xml"}, use_cookies=False)
code, text = cxml_status(setup.text)
check("PunchOutSetupRequest is accepted", code == "200", f"cXML {code} {text}")
check("...answered as XML", "xml" in setup.headers.get("content-type", ""),
      setup.headers.get("content-type", ""))
start_page = (re.search(r"<URL>([^<]+)</URL>", setup.text) or [None, ""])[1]
check("a StartPage URL comes back", start_page.startswith(BASE),
      start_page or "none")
check("the Status text reports what we sent",
      "sharedSecret=present" in setup.text and "buyerCookie=present" in setup.text,
      "no other tool tells you which field your credentials arrived in")

bad_secret = envelope(setup_body).replace(secret.encode(), b"wrong-secret")
reply = request("/punchout/setup", "POST", data=bad_secret, use_cookies=False)
check("a wrong shared secret is refused", cxml_status(reply.text)[0] == "401",
      f"cXML {cxml_status(reply.text)[0]}")

no_return = envelope(
    '<Request deploymentMode="test"><PunchOutSetupRequest operation="create">'
    '<BuyerCookie>x</BuyerCookie></PunchOutSetupRequest></Request>')
reply = request("/punchout/setup", "POST", data=no_return, use_cookies=False)
check("a missing BrowserFormPost/URL is refused with a reason",
      cxml_status(reply.text)[0] == "400" and "nowhere to return" in reply.text,
      f"cXML {cxml_status(reply.text)[0]}")

wrong_doc = envelope('<Request deploymentMode="test"><OrderRequest/></Request>')
reply = request("/punchout/setup", "POST", data=wrong_doc, use_cookies=False)
check("a non-PunchOutSetupRequest is refused",
      cxml_status(reply.text)[0] in ("400", "406"),
      f"cXML {cxml_status(reply.text)[0]}")


# =========================================================================== #
section("F. The storefront, inside a punchout session")
# =========================================================================== #
session_id = urllib.parse.parse_qs(urllib.parse.urlparse(start_page).query).get("session", [""])[0]
shop = request(f"/shop?session={session_id}")
check("the StartPage URL opens the storefront", shop.status == 200,
      f"HTTP {shop.status}")
check("...and shows the punchout banner", "Punchout session active" in shop.text,
      "a user must never lose track of being inside someone else's transaction")

# /shop is the department index; products live in LEAF categories, and
# category ids are dotted ("office.paper"), which is why the walk below is a
# walk rather than one regex. A department that bottoms out with neither
# products nor children is a real failure.
CATEGORY_HREF = r'href="/shop/([a-z0-9.\-]+)"'


def walk_for_products(category: str, depth: int = 0) -> list[str]:
    page = request(f"/shop/{category}?session={session_id}")
    if page.status != 200:
        return []
    products = re.findall(r'/product/([A-Za-z0-9._-]+)"', page.text)
    if products:
        return products
    if depth >= 2:
        return []
    found: list[str] = []
    for child in dict.fromkeys(re.findall(CATEGORY_HREF, page.text)):
        if child != category:
            found.extend(walk_for_products(child, depth + 1))
        if found:
            break
    return found


categories = [c for c in dict.fromkeys(re.findall(CATEGORY_HREF, shop.text))]
check("departments are listed", len(categories) >= 3,
      f"{len(categories)} categories")
skus: list[str] = []
for category in categories[:4]:
    found = walk_for_products(category)
    check(f"/shop/{category} leads to products",
          bool(found), f"{len(found)} found by walking down")
    skus.extend(found)
skus = sorted(set(skus))
check("products are reachable by browsing", len(skus) >= 3,
      f"{len(skus)} distinct SKUs")

search = request(f"/shop?session={session_id}&q=paper")
check("search returns something", search.status == 200
      and ("/product/" in search.text or "no results" in search.text.lower()),
      f"HTTP {search.status}")
if skus:
    product = request(f"/product/{skus[0]}")
    check(f"a product page renders ({skus[0]})", product.status == 200,
          f"HTTP {product.status}")
    add = request("/cart/add", "POST", form={"sku": skus[0], "quantity": "3"})
    check("adding to the cart succeeds", add.status in (200, 303),
          f"HTTP {add.status}")
    cart = request("/cart")
    check("the cart shows the line", skus[0] in cart.text, f"HTTP {cart.status}")

    ret = request("/cart/return", "POST", form={})
    check("the cart return produces a form post", ret.status == 200,
          f"HTTP {ret.status}")
    check("...targeting the buyer's URL",
          "buyer.example.com/punchout/return" in ret.text)
    field = re.search(r'name="(cxml-base64|cxml-urlencoded)" value="([^"]*)"',
                      ret.text)
    check("...in the field name buyer platforms look for", bool(field),
          "cxml-base64 or cxml-urlencoded; anything else is not read")
    if field:
        import base64 as _b64
        payload = (_b64.b64decode(field.group(2)).decode("utf-8", "replace")
                   if field.group(1) == "cxml-base64"
                   else urllib.parse.unquote_plus(field.group(2)))
        check("...decoding to a PunchOutOrderMessage",
              "PunchOutOrderMessage" in payload, payload[:80])
        check("...echoing the BuyerCookie unchanged",
              "<BuyerCookie>qa-cookie-1</BuyerCookie>" in payload,
              "it is minted by the buyer and binds this cart to the "
              "requisition that opened it — we never generate or alter it")
        check("...carrying the cart line", skus[0] in payload, skus[0])
        check("...declaring operationAllowed", "operationAllowed=" in payload,
              "buyers key their edit/inspect behaviour off it")

missing = request("/product/NO-SUCH-SKU")
check("an unknown SKU 404s", missing.status == 404, f"HTTP {missing.status}")


# =========================================================================== #
section("G. OCI")
# =========================================================================== #
hook = "https://buyer.example.com/sap/hook"
oci = request("/oci/setup", "POST", form={
    "USERNAME": identity, "PASSWORD": secret, "HOOK_URL": hook,
    "OCI_VERSION": "4.0", "http_content_charset": "utf-8"})
check("an OCI call-up is accepted", oci.status in (200, 303),
      f"HTTP {oci.status}")

oci_get = request(f"/oci/setup?USERNAME={identity}&PASSWORD="
                  f"{urllib.parse.quote(secret)}&HOOK_URL={urllib.parse.quote(hook)}")
check("...over GET as well as POST", oci_get.status in (200, 303),
      f"HTTP {oci_get.status} — SRM Customizing decides which, and plenty of "
      "older configurations use GET")

detail = request("/oci/setup", "POST", form={
    "USERNAME": identity, "PASSWORD": secret, "HOOK_URL": hook,
    "FUNCTION": "DETAIL", "PRODUCTID": skus[0] if skus else "MSC-1001"})
check("FUNCTION=DETAIL answers", detail.status in (200, 303),
      f"HTTP {detail.status}")

validate_fn = request("/oci/setup", "POST", form={
    "USERNAME": identity, "PASSWORD": secret, "HOOK_URL": hook,
    "FUNCTION": "VALIDATE", "PRODUCTID": skus[0] if skus else "MSC-1001",
    "QUANTITY": "5"})
check("FUNCTION=VALIDATE returns an auto-submitting form",
      validate_fn.status == 200 and "NEW_ITEM-" in validate_fn.text,
      f"HTTP {validate_fn.status}")
check("...with the fields hidden", 'type="hidden"' in validate_fn.text,
      "VALIDATE is machine-to-machine; a visible form would be a bug")

search = request("/oci/setup", "POST", form={
    "USERNAME": identity, "PASSWORD": secret, "HOOK_URL": hook,
    "FUNCTION": "BACKGROUND_SEARCH", "SEARCHSTRING": "paper"})
check("FUNCTION=BACKGROUND_SEARCH answers", search.status == 200,
      f"HTTP {search.status}")

sourcing = request("/oci/setup", "POST", form={
    "USERNAME": identity, "PASSWORD": secret, "HOOK_URL": hook,
    "FUNCTION": "SOURCING"})
check("FUNCTION=SOURCING is handled rather than crashing",
      sourcing.status in (200, 303), f"HTTP {sourcing.status}")

no_hook = request("/oci/setup", "POST", form={"USERNAME": identity,
                                              "PASSWORD": secret})
check("a missing HOOK_URL is reported, not ignored",
      no_hook.status in (200, 303, 400), f"HTTP {no_hook.status}")

# use_cookies=False matters: a signed-in BROWSER is allowed through the gate
# by its cookie, so sending one here would test nothing. SRM has no cookie.
bad_oci = request("/oci/setup", "POST", use_cookies=False,
                  form={"USERNAME": identity, "PASSWORD": "wrong",
                        "HOOK_URL": hook})
check("wrong OCI credentials are refused", bad_oci.status == 401,
      f"HTTP {bad_oci.status}")
no_creds = request("/oci/setup", "POST", use_cookies=False,
                   form={"HOOK_URL": hook})
check("absent OCI credentials are refused", no_creds.status == 401,
      f"HTTP {no_creds.status}")
good_oci = request("/oci/setup", "POST", use_cookies=False,
                   form={"USERNAME": identity, "PASSWORD": secret,
                         "HOOK_URL": hook})
check("correct OCI credentials get in without a browser session",
      good_oci.status in (200, 303), f"HTTP {good_oci.status}")


# =========================================================================== #
section("H. Purchase order to invoice, end to end")
# =========================================================================== #
ORDER_ID = f"PO-QA-{stamp}"
order_body = (
    '<Request deploymentMode="test"><OrderRequest>'
    f'<OrderRequestHeader orderID="{ORDER_ID}" '
    'orderDate="2026-08-14T10:00:00+01:00" type="new">'
    '<Total><Money currency="GBP">120.00</Money></Total>'
    '<ShipTo><Address isoCountryCode="DE"><Name xml:lang="en">Berlin Depot</Name>'
    '<PostalAddress><Street>1 Alexanderplatz</Street><City>Berlin</City>'
    '<Country isoCountryCode="DE">Germany</Country></PostalAddress>'
    '</Address></ShipTo>'
    '<BillTo><Address isoCountryCode="DE"><Name xml:lang="en">Northgate GmbH</Name>'
    '<PostalAddress><Street>1 Alexanderplatz</Street><City>Berlin</City>'
    '<Country isoCountryCode="DE">Germany</Country></PostalAddress>'
    '</Address></BillTo>'
    '</OrderRequestHeader>'
    '<ItemOut quantity="10" lineNumber="1">'
    f'<ItemID><SupplierPartID>{skus[0]}</SupplierPartID></ItemID>'
    '<ItemDetail><UnitPrice><Money currency="GBP">10.00</Money></UnitPrice>'
    '<Description xml:lang="en">QA line one</Description>'
    '<UnitOfMeasure>BX</UnitOfMeasure>'
    '<Classification domain="UNSPSC">14111507</Classification>'
    '</ItemDetail></ItemOut>'
    '<ItemOut quantity="2">'
    f'<ItemID><SupplierPartID>{skus[1] if len(skus) > 1 else skus[0]}</SupplierPartID></ItemID>'
    '<ItemDetail><UnitPrice><Money currency="GBP">7.50</Money></UnitPrice>'
    '<Description xml:lang="en">QA line two, no lineNumber</Description>'
    '<UnitOfMeasure>EA</UnitOfMeasure>'
    '<Classification domain="UNSPSC">44121704</Classification>'
    '</ItemDetail></ItemOut>'
    '</OrderRequest></Request>')

posted = request("/order", "POST", data=envelope(order_body),
                 headers={"content-type": "text/xml"}, use_cookies=False)
code, _ = cxml_status(posted.text)
check("the purchase order is accepted", code == "200", f"cXML {code}")
check("the Status text reconciles header Total against the lines",
      "does not equal" in posted.text,
      "120.00 header vs 115.00 of lines — reported, never silently repaired")
check("...and flags the missing lineNumber",
      "no ItemOut/@lineNumber" in posted.text)
order_url = posted.headers.get("x-punchout-sandbox-order", "")
check("a link to the order screen comes back", order_url.startswith(BASE),
      order_url or "header absent")

order_ref = order_url.rsplit("/", 1)[-1]
detail = request(f"/orders/{order_ref}")
check("the order screen renders", detail.status == 200 and ORDER_ID in detail.text,
      f"HTTP {detail.status}")
check("...showing the inferred line number", "inferred" in detail.text,
      "line 2 had no lineNumber")

listing = request("/orders")
check("the order appears in the list", ORDER_ID in listing.text,
      f"HTTP {listing.status}")

source = request(f"/orders/{order_ref}/source")
check("the document is retrievable exactly as sent",
      source.status == 200 and ORDER_ID in source.text, f"HTTP {source.status}")

generated = {}
for action, kind in [("confirm", "ConfirmationRequest"),
                     ("ship", "ShipNoticeRequest"),
                     ("invoice", "InvoiceDetailRequest")]:
    made = request(f"/orders/{order_ref}/{action}", "POST",
                   form={"header_type": "accept", "carrier": "UPSN"})
    check(f"generate {kind}", made.status == 303, f"HTTP {made.status}")
    generated[kind] = made.location

page = request(f"/orders/{order_ref}")
doc_ids = re.findall(rf"/orders/{re.escape(order_ref)}/doc/([0-9a-zA-Z_-]+)", page.text)
doc_ids = list(dict.fromkeys(doc_ids))
check("all three documents are listed", len(doc_ids) == 3,
      f"{len(doc_ids)} documents")

for doc_id in doc_ids:
    doc = request(f"/orders/{order_ref}/doc/{doc_id}")
    check(f"{doc_id.split('-')[1]} document downloads",
          doc.status == 200 and doc.text.startswith("<?xml"),
          f"HTTP {doc.status}")
    check(f"...declares the right DTD for its type",
          ("Fulfill.dtd" in doc.text if "confirmation" in doc_id or "shipnotice" in doc_id
           else "InvoiceDetail.dtd" in doc.text),
          "the wrong DOCTYPE is the first thing that goes wrong here")

invoice_doc = next((d for d in doc_ids if "invoice" in d), "")
if invoice_doc:
    body = request(f"/orders/{order_ref}/doc/{invoice_doc}").text
    check("the invoice is taxed in the ShipTo country", 'DE' in body,
          "the order ships to Berlin; the supplier is in the UK")
    check("...and both mandatory indicators are present, in order",
          body.index("InvoiceDetailHeaderIndicator") < body.index("InvoiceDetailLineIndicator"),
          "both are mandatory and ordered even when empty")

section("H2. Delivery")
sent = request(f"/orders/{order_ref}/send/{doc_ids[0]}", "POST",
               form={"endpoint": "https://example.com/cxml/inbox"})
check("a send is accepted", sent.status == 303, f"HTTP {sent.status}")
after = request(f"/orders/{order_ref}").text
check("the outcome is recorded on the page",
      "not delivered" in after or "delivered" in after or "rejected" in after)
check("...with the HTTP status from the far end", "405" in after or "404" in after,
      "example.com is not a cXML inbox, which is exactly the failure a user "
      "needs to see reported rather than retried away")
check("...and guidance rather than a bare code",
      "cXML inbox is almost always" in after or "permanent refusal" in after)


# =========================================================================== #
section("I. Hostile XML")
# =========================================================================== #
# Every one of these must be REFUSED, and refused before the document is
# processed — `xml_safe.py` decides hostility with defusedxml first and only
# then hands accepted bytes to lxml.
XXE = ('<?xml version="1.0"?><!DOCTYPE cXML [<!ENTITY xxe SYSTEM '
       '"file:///etc/passwd">]><cXML>&xxe;</cXML>')
XXE_REMOTE = ('<?xml version="1.0"?><!DOCTYPE cXML [<!ENTITY xxe SYSTEM '
              '"http://169.254.169.254/latest/meta-data/">]><cXML>&xxe;</cXML>')
BILLION = ('<?xml version="1.0"?><!DOCTYPE cXML [<!ENTITY a "aaaaaaaaaa">'
           '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
           '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
           '<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]><cXML>&d;</cXML>')
DEEP = "<?xml version='1.0'?><cXML>" + "<a>" * 400 + "</a>" * 400 + "</cXML>"

for name, payload in [("an external entity (file://)", XXE),
                      ("an external entity (cloud metadata)", XXE_REMOTE),
                      ("entity expansion", BILLION),
                      ("400 levels of nesting", DEEP)]:
    reply = request("/validate", "POST", form={"document": payload})
    refused = ("refus" in reply.text.lower() or "entit" in reply.text.lower()
               or "depth" in reply.text.lower() or "nest" in reply.text.lower())
    check(f"/validate refuses {name}", reply.status == 200 and refused,
          f"HTTP {reply.status}")
    check(f"...and does not leak the target", "root:" not in reply.text
          and "ami-id" not in reply.text)

    machine = request("/punchout/setup", "POST", data=payload.encode(),
                      use_cookies=False)
    code, _ = cxml_status(machine.text)
    check(f"/punchout/setup refuses {name}", code in ("401", "406"),
          f"cXML {code} — 406 Not Acceptable is the spec's parse-failure code")

big = request("/validate", "POST", form={"document": "<a>" + "x" * 600_000 + "</a>"})
check("an oversized paste is refused rather than parsed",
      "bytes" in big.text or "accepts up to" in big.text, f"HTTP {big.status}")


# =========================================================================== #
section("J. Injection into the pages")
# =========================================================================== #
XSS = '<?xml version="1.0"?><cXML><a>"><script>alert(1)</script></a></cXML>'
reply = request("/validate", "POST", form={"document": XSS})
check("a pasted script tag comes back escaped",
      "<script>alert(1)</script>" not in reply.text,
      "the paste is echoed with line numbers, so this is a stored-XSS surface")

# The same payload through the ORDER path, which is stored and re-rendered.
xss_order = envelope(
    '<Request deploymentMode="test"><OrderRequest>'
    f'<OrderRequestHeader orderID="PO-XSS-{stamp}" '
    'orderDate="2026-08-14T10:00:00+01:00" type="new">'
    '<Total><Money currency="GBP">1.00</Money></Total>'
    '<BillTo><Address><Name xml:lang="en">&lt;script&gt;alert(2)&lt;/script&gt;</Name>'
    '</Address></BillTo></OrderRequestHeader>'
    '<ItemOut quantity="1" lineNumber="1">'
    '<ItemID><SupplierPartID>&lt;img src=x onerror=alert(3)&gt;</SupplierPartID></ItemID>'
    '<ItemDetail><UnitPrice><Money currency="GBP">1.00</Money></UnitPrice>'
    '<Description xml:lang="en">&lt;svg onload=alert(4)&gt;</Description>'
    '<UnitOfMeasure>EA</UnitOfMeasure>'
    '<Classification domain="UNSPSC">14111507</Classification>'
    '</ItemDetail></ItemOut></OrderRequest></Request>')
posted = request("/order", "POST", data=xss_order, use_cookies=False)
xss_url = posted.headers.get("x-punchout-sandbox-order", "")
if xss_url:
    page = request("/orders/" + xss_url.rsplit("/", 1)[-1]).text
    for payload in ("<script>alert(2)</script>", "<img src=x onerror=alert(3)>",
                    "<svg onload=alert(4)>"):
        check(f"stored {payload[:22]}… is escaped when rendered",
              payload not in page,
              "a buyer's document is untrusted input rendered back to a human")
    check("the raw document is not served as HTML",
          "html" not in request("/orders/" + xss_url.rsplit("/", 1)[-1]
                                + "/source").headers.get("content-type", ""),
          "served as text/xml with nosniff, so a browser will not execute it")


# =========================================================================== #
section("K. Access control")
# =========================================================================== #
other_jar = http.cookiejar.CookieJar()
_saved_jar = jar
second_email = f"qa2+{stamp}@punchoutsandbox.example"


def as_second_account(path: str, method: str = "GET", **kw) -> Reply:
    """Runs a request under a SECOND account's cookies."""
    global _opener
    keep = _opener
    _opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(other_jar),
        type("NR", (urllib.request.HTTPRedirectHandler,),
             {"redirect_request": lambda *a, **k: None})())
    try:
        return request(path, method, **kw)
    finally:
        _opener = keep


signup2 = as_second_account("/signup", "POST",
                            form={"email": second_email, "company": "QA Two"})
check("a second account can be created", signup2.status == 200,
      f"HTTP {signup2.status}")

for path, what in [(f"/orders/{order_ref}", "the order screen"),
                   (f"/orders/{order_ref}/source", "the raw document"),
                   (f"/orders/{order_ref}/doc/{doc_ids[0]}", "a generated document")]:
    reply = as_second_account(path)
    check(f"another account cannot read {what}", reply.status == 404,
          f"HTTP {reply.status} — order refs appear in URLs, so scoping is "
          "the only thing between one buyer's PO and another's")

reply = as_second_account(f"/orders/{order_ref}/confirm", "POST",
                          form={"header_type": "accept"})
check("another account cannot generate against it", reply.status == 404,
      f"HTTP {reply.status}")
reply = as_second_account(f"/orders/{order_ref}/send/{doc_ids[0]}", "POST",
                          form={"endpoint": "https://example.com/x"})
check("another account cannot send it", reply.status == 404,
      f"HTTP {reply.status}")

forged = request("/orders", headers={"cookie": "pst=not-a-real-account"},
                 use_cookies=False)
check("a forged account cookie falls back to the gate", GATE in forged.text,
      f"HTTP {forged.status}")


# =========================================================================== #
section("L. SSRF, at the send step and not only in settings")
# =========================================================================== #
for target, why in [
    ("http://example.com/x", "plain http"),
    ("https://127.0.0.1/x", "loopback"),
    ("https://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ("https://10.0.0.1/x", "RFC1918"),
    ("https://[::1]/x", "IPv6 loopback"),
    ("https://example.com:22/x", "a non-443 port"),
    ("file:///etc/passwd", "a non-http scheme"),
    ("https://metadata.google.internal/x", "a name that resolves privately"),
]:
    request(f"/orders/{order_ref}/send/{doc_ids[0]}", "POST",
            form={"endpoint": target})
    page = request(f"/orders/{order_ref}").text
    blocked = ("not a public address" in page or "must be https" in page
               or "not allowed" in page or "does not resolve" in page
               or "not accepted" in page)
    check(f"{why} is refused at the send step", blocked, target)

saved = request("/settings", "POST", form={"endpoint": "https://169.254.169.254/x"})
check("...and in settings", "refused" in saved.text.lower(), f"HTTP {saved.status}")


# =========================================================================== #
section("M. Routing and method handling")
# =========================================================================== #
check("an unknown path is 404", request("/no-such-page").status == 404)
check("a POST-only route answers 405 to GET",
      request("/cart/return").status == 405,
      "a supplier debugging with a browser GET should be told the verb is "
      "wrong, not that the endpoint does not exist")
for probe in ("/static/../handler.py", "/static/..%2fhandler.py",
              "/static/%2e%2e/%2e%2e/etc/passwd"):
    reply = request(probe)
    # 404 from us, 400 from Cloudflare for the percent-encoded forms — both
    # are refusals. What matters is that no file content comes back.
    check(f"path traversal refused: {probe}",
          400 <= reply.status < 500 and "def " not in reply.text
          and "root:" not in reply.text, f"HTTP {reply.status}")

import os

origin = os.environ.get("ORIGIN_URL")
if origin:
    direct = request(origin.rstrip("/") + "/docs", use_cookies=False)
    check("the origin refuses requests that skip Cloudflare",
          direct.status == 404,
          f"HTTP {direct.status} — every rate limit lives at the edge")
else:
    record("INFO", "origin bypass not tested",
           "set ORIGIN_URL to the Lambda Function URL to include it")


# =========================================================================== #
# Opt-in: QA_RATE_LIMIT=1. Exhausting the anonymous allowance locks THIS IP
# out of anonymous /validate until midnight UTC and sends the operator a quota
# alert, so it is not something to run on every pass.
if os.environ.get("QA_RATE_LIMIT"):
    section("M2. The anonymous rate limit actually fires")
    doc = "<?xml version='1.0'?><cXML/>"
    limited_at = None
    for attempt in range(1, 40):
        reply = request("/validate", "POST", form={"document": doc},
                        use_cookies=False)
        if reply.status == 429:
            limited_at = attempt
            break
    check("an anonymous flood is eventually refused", limited_at is not None,
          f"stopped at request {limited_at}" if limited_at
          else "40 requests, never limited — the counter is not working")
    if limited_at:
        check("...with a 429 and a signup prompt rather than a blank wall",
              GATE in reply.text or "limit" in reply.text.lower())
        signed_in_still = request("/validate", "POST", form={"document": doc})
        check("...while a signed-in account is unaffected",
              signed_in_still.status == 200,
              f"HTTP {signed_in_still.status} — the gap between 25 and 500 is "
              "the whole signup incentive")
else:
    record("INFO", "rate limit not exercised",
           "set QA_RATE_LIMIT=1 to include it; it locks this IP out of "
           "anonymous /validate for the day and emails the operator")


# =========================================================================== #
section("N. Summary")
# =========================================================================== #
fails = [n for level, n, _ in results if level == "FAIL"]
warns = [n for level, n, _ in results if level == "WARN"]
passes = [n for level, n, _ in results if level == "PASS"]
print(f"\n  {len(passes)} passed, {len(warns)} warnings, {len(fails)} failed")
for name in warns:
    print(f"    WARN  {name}")
for name in fails:
    print(f"    FAIL  {name}")
print(f"\n  account used: {email}")
print(f"  order:        {order_ref}")
sys.exit(1 if fails else 0)
