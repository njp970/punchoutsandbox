"""Does the cart we return actually conform, and does it round-trip?

Two levels of proof here. First, every document is validated against the real
vendored `cXML.dtd` by our own validator — `build_punchout_order_message` and
`validate` share no code. Second, the generated cart is fed through the DIFFER
against a purchase order derived from it, which is the closest thing to
end-to-end this repository can do without a real buyer on the other end.
"""
import base64
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.cxml.punchout import (CartItem, build_cancel, build_empty_cart,
                               build_punchout_order_message, render_return_form)
from app.differ import diff
from app.validation import validate
from app.xml_safe import parse

UTC1 = timezone(timedelta(hours=1))
NOW = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC1)
COOKIE = "1CX3L4843PPZO"

IDS = dict(from_identity="meridian", to_identity="northgate",
           sender_identity="meridian")

ITEMS = [
    CartItem("MSC-1001", D("20"), D("3.99"), "Meridian A4 Copier Paper 80gsm",
             "RM", "14111507", supplier_part_auxiliary_id="CTR-STDPAPER",
             manufacturer_part_id="MER-A4-80", manufacturer_name="Meridian",
             lead_time_days=3, short_name="A4 Copier Paper 80gsm"),
    CartItem("MSC-3010", D("2"), D("245.00"), "Lumen 27in QHD IPS Monitor",
             "EA", "43211902", supplier_part_auxiliary_id="CFG-LM27Q",
             lead_time_days=7),
]

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def conformant(raw, label, expected="PunchOutOrderMessage"):
    report = validate(parse(raw), expected_type=expected)
    for f in report.errors:
        print(f"         line {f.line}: {f.message}")
    check(f"{label} is DTD-conformant", report.conformant,
          f"{len(report.errors)} errors, {len(report.advisories)} advisories")
    return report


print("\n=== 1. A normal cart ===")
raw = build_punchout_order_message(
    ITEMS, buyer_cookie=COOKIE, payload_id="c1@meridian", timestamp=NOW, **IDS)
report = conformant(raw, "cart")
check("BuyerCookie echoed unchanged", f"<BuyerCookie>{COOKIE}</BuyerCookie>".encode() in raw)
check("Total EXCLUDES tax and shipping (= sum of line subtotals)",
      b"<Money currency=\"GBP\">569.80</Money>" in raw,
      "20x3.99 + 2x245.00 = 569.80")
check("no SharedSecret on the browser leg by default", b"SharedSecret" not in raw,
      "the spec forbids credentials in one-way browser transport")
check("aux IDs survive", b"CTR-STDPAPER" in raw and b"CFG-LM27Q" in raw)
check("ShortName is nested inside Description",
      b"<Description xml:lang=\"en\"><ShortName>" in raw)

print("\n=== 2. operationAllowed is enumerated ===")
for value in ("create", "inspect", "edit"):
    r = build_punchout_order_message(ITEMS, buyer_cookie=COOKIE,
                                     payload_id="x", timestamp=NOW,
                                     operation_allowed=value, **IDS)
    conformant(r, f"operationAllowed={value}")
try:
    build_punchout_order_message(ITEMS, buyer_cookie=COOKIE, payload_id="x",
                                 timestamp=NOW, operation_allowed="replace", **IDS)
    check("an invalid operationAllowed is refused", False)
except ValueError as exc:
    check("an invalid operationAllowed is refused", True, str(exc)[:66])

print("\n=== 3. Empty cart vs 204 — opposite meanings ===")
empty = build_empty_cart(buyer_cookie=COOKIE, payload_id="e1@meridian",
                         timestamp=NOW, **IDS)
cancel = build_cancel(buyer_cookie=COOKIE, payload_id="x1@meridian",
                      timestamp=NOW, **IDS)
conformant(empty, "empty cart")
conformant(cancel, "204 cancel")
check("empty cart has no ItemIn", b"<ItemIn" not in empty)
check("cancel carries Status 204", b'<Status code="204"' in cancel)
check("empty cart does NOT carry a Status", b"<Status" not in empty,
      "a 204 on an edit means 'change nothing'; an empty list means 'delete'")

print("\n=== 4. The 'Z' timestamp cXML forbids ===")
try:
    build_punchout_order_message(ITEMS, buyer_cookie=COOKIE, payload_id="x",
                                 timestamp=datetime(2026, 8, 14), **IDS)
    check("naive datetime refused", False)
except ValueError as exc:
    check("naive datetime refused", True, str(exc)[:66])

print("\n=== 5. Browser form encodings ===")
form64 = render_return_form(raw, browser_form_post_url="https://buyer.example.com/r")
formurl = render_return_form(raw, browser_form_post_url="https://buyer.example.com/r",
                             encoding="cxml-urlencoded")
check("base64 field named cxml-base64", 'name="cxml-base64"' in form64)
check("base64 value decodes back to the exact document",
      base64.b64decode(
          form64.split('name="cxml-base64" value="')[1].split('"')[0]) == raw)
check("urlencoded field named cxml-urlencoded", 'name="cxml-urlencoded"' in formurl)
check("form posts to the BrowserFormPost URL",
      'action="https://buyer.example.com/r"' in form64)

# The us-ascii rule, which is where mojibake comes from.
accented = [CartItem("MSC-Q104", D("1"), D("15.90"),
                     "Brightwell Citron Dégraissant 5L — idéal", "EA", "47131805")]
raw_accented = build_punchout_order_message(
    accented, buyer_cookie=COOKIE, payload_id="a1", timestamp=NOW, **IDS)
form_ascii = render_return_form(raw_accented,
                                browser_form_post_url="https://b.example/r",
                                encoding="cxml-urlencoded")
check("urlencoded output is pure us-ascii", form_ascii.isascii(),
      "cxml-urlencoded must be us-ascii whatever the XML declaration says")
check("accented characters became numeric entities", "&#233;" in form_ascii,
      "e-acute -> &#233;")
form_b64 = render_return_form(raw_accented,
                              browser_form_post_url="https://b.example/r")
check("base64 needs no such surgery",
      base64.b64decode(
          form_b64.split('value="')[1].split('"')[0]) == raw_accented)

print("\n=== 6. Round trip through the differ ===")
# Build a PO that mangles the cart the way a real buyer platform does:
# aux ID truncated, UOM defaulted to EA, description cut.
po = raw.replace(b"CTR-STDPAPER", b"CTR-STDPAP")          # truncation
po = po.replace(b"<UnitOfMeasure>RM</UnitOfMeasure>",
                b"<UnitOfMeasure>EA</UnitOfMeasure>")      # UOM defaulting
po = po.replace(b"<ItemIn ", b"<ItemOut lineNumber=\"1\" ")
po = po.replace(b"</ItemIn>", b"</ItemOut>")
report = diff(parse(raw), parse(po))
codes = {f.diagnosis for line in report.lines for f in line.corrupted}
check("differ sees the truncated aux ID", "truncated" in codes, codes)
check("differ sees the UOM defaulted to EA", "uom-defaulted-to-EA" in codes, codes)
check("round trip is not clean", not report.clean)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("every cart validates against the real cXML DTD")
