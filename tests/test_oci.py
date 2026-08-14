"""OCI round trip.

There is no DTD to validate against here — OCI has no schema at all — so the
assertions are against the SAP specification's stated rules instead. That
absence is the point: cXML documents can be judged mechanically, OCI carts
cannot, which is why every truncation and normalisation has to be reported
rather than assumed visible.
"""
import base64
import sys
from decimal import Decimal as D
from urllib.parse import quote

sys.path.insert(0, "/Users/neilparkes/punchout")

from app import sessions
from app.handler import handler
from app.oci.inbound import parse_callup, observations
from app.oci.outbound import (FIELD_LIMITS, OciItem, build_fields,
                              render_return_form)
from app.sessions import MemoryStore

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def request(path, method="GET", body=b"", query=None, cookies=None):
    ev = {"requestContext": {"http": {"method": method, "path": path}},
          "queryStringParameters": query or {}, "headers": {},
          "cookies": cookies or [],
          "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    r = handler(ev)
    b = r["body"]
    return r["statusCode"], (base64.b64decode(b).decode()
                             if r.get("isBase64Encoded") else b), r


print("\n=== 1. Indices start at 1, never 0 ===")
items = [OciItem("Widget", D("2"), "EA", D("10.00")),
         OciItem("Gadget", D("1"), "EA", D("5.00"))]
fields, _ = build_fields(items)
check("first item is [1]", "NEW_ITEM-DESCRIPTION[1]" in fields)
check("second item is [2]", "NEW_ITEM-DESCRIPTION[2]" in fields)
check("there is no [0]", "NEW_ITEM-DESCRIPTION[0]" not in fields,
      "a zero-based cart is silently ignored or truncated at the gap")

print("\n=== 2. DESCRIPTION is CHAR-40 and truncation is REPORTED ===")
long_name = "Vantor ProBook 16 Mobile Workstation i9 64GB 2TB Discrete Graphics"
fields, advisories = build_fields([OciItem(long_name, D("1"), "EA", D("2489.00"))])
check("description truncated to 40", len(fields["NEW_ITEM-DESCRIPTION[1]"]) == 40,
      f"{len(fields['NEW_ITEM-DESCRIPTION[1]'])} chars")
check("an advisory was raised", any(a.field == "DESCRIPTION" for a in advisories),
      "SRM truncates silently; this sandbox exists to say so")
check("the advisory points at LONGTEXT",
      any("LONGTEXT" in a.message for a in advisories))

print("\n=== 3. LONGTEXT uses the odd index syntax ===")
fields, _ = build_fields([OciItem("W", D("1"), "EA", D("1.00"),
                                  long_text="The full description.")])
check("field is NEW_ITEM-LONGTEXT_1:132[]",
      "NEW_ITEM-LONGTEXT_1:132[]" in fields, list(fields)[-1])
check("NOT the intuitive LONGTEXT[1]", "NEW_ITEM-LONGTEXT[1]" not in fields,
      "getting this wrong appends every item's long text to item 1")

print("\n=== 4. PRICEUNIT is always explicit ===")
fields, _ = build_fields([OciItem("W", D("1"), "EA", D("50.00"), price_unit=5)])
check("PRICEUNIT emitted", fields.get("NEW_ITEM-PRICEUNIT[1]") == "5")
fields, _ = build_fields([OciItem("W", D("1"), "EA", D("10.00"))])
check("explicit 1 rather than omitted", fields.get("NEW_ITEM-PRICEUNIT[1]") == "1",
      "an absent PRICEUNIT defaults to 1 — right until it is a 1000x error")

print("\n=== 5. Numeric format: no thousands separators, 3dp quantity ===")
fields, _ = build_fields([OciItem("W", D("1234"), "EA", D("9.5"))])
check("quantity has 3 decimals", fields["NEW_ITEM-QUANTITY[1]"] == "1234.000",
      fields["NEW_ITEM-QUANTITY[1]"])
check("no comma anywhere in numerics",
      "," not in fields["NEW_ITEM-QUANTITY[1]"] + fields["NEW_ITEM-PRICE[1]"],
      "SAP: a comma decimal or thousands comma breaks the transfer")

print("\n=== 6. UNIT is CHAR-3 ===")
fields, advisories = build_fields([OciItem("W", D("1"), "EACH", D("1.00"))])
check("EACH truncated to 3 chars", fields["NEW_ITEM-UNIT[1]"] == "EAC")
check("and flagged", any(a.field == "UNIT" for a in advisories),
      "'EACH' silently becoming 'EAC' is a real production failure")

print("\n=== 7. HOOK_URL is SPLIT — the most-missed OCI rule ===")
hook = "https://srm.example.com/sap/bc/gui/sap/its/bbpsc01?sap-client=100&sap-sessionid=ABC123"
page = render_return_form({"NEW_ITEM-DESCRIPTION[1]": "W"}, hook_url=hook)
check("action is the URL WITHOUT its query string",
      'action="https://srm.example.com/sap/bc/gui/sap/its/bbpsc01"' in page)
check("sap-client promoted to a hidden field",
      'name="sap-client" value="100"' in page)
check("sap-sessionid promoted to a hidden field",
      'name="sap-sessionid" value="ABC123"' in page)
check("no query string left in the action", "?sap-client" not in page,
      "POSTing to the whole HOOK_URL loses or duplicates parameters, silently")

print("\n=== 8. ITS control fields ===")
check("~OkCode is ADDI", 'name="~OkCode" value="ADDI"' in page,
      "the only OK code in any SAP spec")
check("~CALLER is CTLG", 'name="~CALLER" value="CTLG"' in page)
check("~target present", 'name="~target"' in page)
check("form target set (iframe symptom if wrong)", 'target="_top"' in page)
check("method is POST", 'method="POST"' in page,
      "GET hits browser URL-length limits at ~20 items")

print("\n=== 9. Call-up parsing is case-insensitive and dual-transport ===")
c = parse_callup(query={"HOOK_URL": "https://x/y", "OCI_VERSION": "4.0"},
                 form={}, method="GET")
check("reads from the query string", c.hook_url == "https://x/y")
c = parse_callup(query={}, form={"hook_url": "https://a/b", "returntarget": "_parent"},
                 method="POST")
check("reads from the form body, lowercase names", c.hook_url == "https://a/b")
check("returntarget honoured", c.return_target == "_parent")
c = parse_callup(query={}, form={"HOOK_URL": "https://x", "~target": "_self"},
                 method="POST")
check("falls back to ~target when returntarget absent", c.return_target == "_self")

print("\n=== 10. Observations warn about what SAP will not ===")
c = parse_callup(query={"HOOK_URL": "http://insecure/x", "USERNAME": "u",
                        "PASSWORD": "p"}, form={}, method="GET")
notes = " ".join(observations(c))
check("flags non-HTTPS HOOK_URL", "not HTTPS" in notes)
check("flags credentials in the URL", "query string" in notes)
check("flags the ISO-8859-1 default", "ISO-8859-1" in notes)

print("\n=== 11. End to end through the handler ===")
sessions.reset_store(MemoryStore())
st, body, raw = request("/oci/setup", "GET",
                        query={"HOOK_URL": "https://srm.example.com/hook?sid=9",
                               "OCI_VERSION": "4.0", "USERNAME": "neil"})
check("call-up redirects into the shop", st == 303, f"status {st}")
token = [c.split("=")[1].split(";")[0] for c in raw.get("cookies", [])][0]
check("session created", bool(token))

request("/cart/add", "POST", b"sku=MSC-1001&quantity=20", cookies=[f"pos={token}"])
st, page, _ = request("/cart/return", "POST", b"mode=cart", cookies=[f"pos={token}"])
check("cart returns as an OCI form", st == 200 and "NEW_ITEM-DESCRIPTION[1]" in page)
check("posts to the HOOK_URL base", 'action="https://srm.example.com/hook"' in page)
check("HOOK_URL query promoted", 'name="sid" value="9"' in page)
check("no cXML anywhere in an OCI return", "cxml" not in page.lower(),
      "protocols must not leak into one another")

sessions.reset_store(None)

# ============================================================================
# FUNCTION=VALIDATE — appended after the round-trip suite above.
# ============================================================================
print("\n=== 12. VALIDATE: the three spec rules ===")
sessions.reset_store(MemoryStore())

st, page, _ = request("/oci/setup", "GET", query={
    "HOOK_URL": "https://srm.example.com/hook?sid=7",
    "FUNCTION": "VALIDATE", "PRODUCTID": "MSC-1001", "QUANTITY": "1"})
check("VALIDATE answers 200", st == 200, f"status {st}")
check("rule 1: no visible submit button", 'type="submit"' not in page,
      "the spec forbids visible elements outright")
check("rule 1: no visible text", "Returning your cart" not in page)
check("rule 2: auto-submits by JavaScript", "submit()" in page,
      "without a button, failing to auto-submit strands the user on a blank page")
check("returns product data", "NEW_ITEM-DESCRIPTION[1]" in page)
check("HOOK_URL still split", 'name="sid" value="7"' in page)

print("\n=== 13. VALIDATE resolves scale pricing from QUANTITY ===")
# MSC-1001 is 4.85 at qty 1 and 3.60 at qty 100.
_, one, _ = request("/oci/setup", "GET", query={
    "HOOK_URL": "https://x/h", "FUNCTION": "VALIDATE",
    "PRODUCTID": "MSC-1001", "QUANTITY": "1"})
_, hundred, _ = request("/oci/setup", "GET", query={
    "HOOK_URL": "https://x/h", "FUNCTION": "VALIDATE",
    "PRODUCTID": "MSC-1001", "QUANTITY": "100"})
import re as _re
p1 = _re.search(r'name="NEW_ITEM-PRICE\[1\]" value="([^"]+)"', one).group(1)
p100 = _re.search(r'name="NEW_ITEM-PRICE\[1\]" value="([^"]+)"', hundred).group(1)
check("qty 1 gets list price", p1 == "4.85", p1)
check("qty 100 gets the tier price", p100 == "3.60", p100)
check("the two differ", p1 != p100,
      "SAP passes QUANTITY so the catalogue can resolve a scale; ignoring it "
      "is why a requisition built from a template loses its volume discount")

print("\n=== 14. VALIDATE on a discontinued product returns NOTHING ===")
_, gone, _ = request("/oci/setup", "GET", query={
    "HOOK_URL": "https://x/h", "FUNCTION": "VALIDATE",
    "PRODUCTID": "MSC-DOES-NOT-EXIST", "QUANTITY": "1"})
check("no form at all", "<form" not in gone,
      "the spec: 'If the product no longer exists, the catalog is not "
      "expected to return any data'")
check("no NEW_ITEM fields", "NEW_ITEM" not in gone)
check("not an empty form", "NEW_ITEM-PRICE" not in gone,
      "an empty form would tell SRM the item is still valid and free")

print("\n=== 15. PRICEUNIT survives VALIDATE (SAP KBA 3382679) ===")
check("PRICEUNIT present on the validate response",
      'name="NEW_ITEM-PRICEUNIT[1]"' in one,
      "SAP documents PRICEUNIT being RESET during VALIDATE, so the price "
      "changes between add-to-cart and requisition creation")

print("\n=== 16. DETAIL returns no data, just the product page ===")
st, _, raw = request("/oci/setup", "GET", query={
    "FUNCTION": "DETAIL", "PRODUCTID": "MSC-3010"})
check("DETAIL redirects to the product", st == 303, f"status {st}")
check("goes to the right product",
      raw["headers"].get("location", "").endswith("/product/MSC-3010"),
      raw["headers"].get("location"))
st, _, _ = request("/oci/setup", "GET", query={
    "FUNCTION": "DETAIL", "PRODUCTID": "NOPE"})
check("unknown PRODUCTID is a 404", st == 404)

print("\n=== 17. BACKGROUND_SEARCH is honest about not being built ===")
st, page, _ = request("/oci/setup", "GET", query={
    "HOOK_URL": "https://x/h", "FUNCTION": "BACKGROUND_SEARCH",
    "SEARCHSTRING": "paper"})
check("returns 501 rather than pretending", st == 501)
check("explains the inverted requirements", "INVERSE" in page or "inverse" in page.lower())

sessions.reset_store(None)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("OCI FUNCTION handling conforms to the SAP specification")
