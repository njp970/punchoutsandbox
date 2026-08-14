"""Does the differ actually catch the silent corruptions it exists for?

Each case reproduces a failure documented in docs/reference/ — not an invented
one. If a case here stops failing, either we fixed something or the differ
went blind; the assertion messages say which finding is expected so a
regression reads as a sentence rather than a boolean.
"""
import sys

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.differ import CORRUPTED, CRITICAL, DROPPED, diff
from app.xml_safe import parse

DOCTYPE = '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
HEADER = """<Header>
 <From><Credential domain="DUNS"><Identity>supplier</Identity></Credential></From>
 <To><Credential domain="DUNS"><Identity>buyer</Identity></Credential></To>
 <Sender><Credential domain="NetworkID"><Identity>s</Identity></Credential>
  <UserAgent>Test</UserAgent></Sender>
</Header>"""


def cart(items, total="100.00"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>{DOCTYPE}
<cXML payloadID="c@x" timestamp="2026-08-14T10:00:00+01:00">{HEADER}
 <Message>
  <PunchOutOrderMessage>
   <BuyerCookie>abc</BuyerCookie>
   <PunchOutOrderMessageHeader operationAllowed="edit">
    <Total><Money currency="GBP">{total}</Money></Total>
   </PunchOutOrderMessageHeader>
   {items}
  </PunchOutOrderMessage>
 </Message>
</cXML>""".encode()


def order(items, total="100.00"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>{DOCTYPE}
<cXML payloadID="o@x" timestamp="2026-08-14T11:00:00+01:00">{HEADER}
 <Request>
  <OrderRequest>
   <OrderRequestHeader orderID="PO-1" orderDate="2026-08-14T11:00:00+01:00" type="new">
    <Total><Money currency="GBP">{total}</Money></Total>
    <BillTo><Address><Name xml:lang="en">B</Name></Address></BillTo>
   </OrderRequestHeader>
   {items}
  </OrderRequest>
 </Request>
</cXML>""".encode()


def item_in(part, aux, qty="1", price="100.00", uom="EA", desc="Widget", cls="44121704"):
    aux_el = f"<SupplierPartAuxiliaryID>{aux}</SupplierPartAuxiliaryID>" if aux else ""
    return f"""<ItemIn quantity="{qty}">
  <ItemID><SupplierPartID>{part}</SupplierPartID>{aux_el}</ItemID>
  <ItemDetail>
   <UnitPrice><Money currency="GBP">{price}</Money></UnitPrice>
   <Description xml:lang="en">{desc}</Description>
   <UnitOfMeasure>{uom}</UnitOfMeasure>
   <Classification domain="UNSPSC">{cls}</Classification>
  </ItemDetail>
 </ItemIn>"""


def item_out(part, aux, qty="1", price="100.00", uom="EA", desc="Widget", cls="44121704"):
    aux_el = f"<SupplierPartAuxiliaryID>{aux}</SupplierPartAuxiliaryID>" if aux else ""
    return f"""<ItemOut quantity="{qty}" lineNumber="1">
  <ItemID><SupplierPartID>{part}</SupplierPartID>{aux_el}</ItemID>
  <ItemDetail>
   <UnitPrice><Money currency="GBP">{price}</Money></UnitPrice>
   <Description xml:lang="en">{desc}</Description>
   <UnitOfMeasure>{uom}</UnitOfMeasure>
   <Classification domain="UNSPSC">{cls}</Classification>
  </ItemDetail>
 </ItemOut>"""


def run(cart_items, order_items, cart_total="100.00", order_total="100.00"):
    return diff(parse(cart(cart_items, cart_total)), parse(order(order_items, order_total)))


def find(report, field):
    for line in report.lines:
        for f in line.fields:
            if f.field == field:
                return f
    for f in report.header:
        if f.field == field:
            return f
    return None


results = []


def check(name, condition, detail=""):
    results.append((name, condition, detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {detail}")


print("\n=== 1. Clean round trip ===")
r = run(item_in("P1", "AUX1"), item_out("P1", "AUX1"))
check("clean verdict", r.clean, f"clean={r.clean}, notes={r.notes}")

print("\n=== 2. Aux ID truncated at 100 chars (JAGGAER limit) ===")
long_aux = "CFG-" + "x" * 120
r = run(item_in("P1", long_aux), item_out("P1", long_aux[:100]))
f = find(r, "supplier_part_auxiliary_id")
check("corruption detected", f is not None and f.outcome == CORRUPTED, f"outcome={f.outcome if f else None}")
check("diagnosed as truncation", f is not None and f.diagnosis == "truncated", f"diagnosis={f.diagnosis if f else None}")
check("severity critical", f is not None and f.severity == CRITICAL)
check("lines still paired despite corrupt key",
      r.lines and r.lines[0].order_index is not None,
      f"matched_by={r.lines[0].matched_by if r.lines else None}")

print("\n=== 3. UOM silently defaulted to EA (the JAGGAER 100x error) ===")
r = run(item_in("P1", "A", uom="BX"), item_out("P1", "A", uom="EA"))
f = find(r, "unit_of_measure")
check("corruption detected", f is not None and f.outcome == CORRUPTED)
check("diagnosed as UOM defaulting", f is not None and f.diagnosis == "uom-defaulted-to-EA",
      f"diagnosis={f.diagnosis if f else None}")
check("severity critical", f is not None and f.severity == CRITICAL)

print("\n=== 4. Aux ID dropped entirely ===")
r = run(item_in("P1", "AUX1"), item_out("P1", None))
f = find(r, "supplier_part_auxiliary_id")
check("reported as dropped not corrupted", f is not None and f.outcome == DROPPED,
      f"outcome={f.outcome if f else None}")
check("not clean", not r.clean)

print("\n=== 5. Same part ID, two lines, aux ID dropped -> AMBIGUOUS ===")
# The exact case the cXML spec invented SupplierPartAuxiliaryID for:
# same part, different price for EA vs BOX.
cart_items = item_in("P1", "EA", price="10.00", uom="EA") + item_in("P1", "BOX", price="90.00", uom="BX")
order_items = (item_out("P1", None, price="10.00", uom="EA")
               + item_out("P1", None, price="90.00", uom="BX"))
r = run(cart_items, order_items)
check("ambiguity flagged", any(l.ambiguous for l in r.lines),
      f"matched_by={[l.matched_by for l in r.lines]}")
check("note explains the spec rationale",
      any("EA vs BOX" in n for n in r.notes), f"notes={r.notes}")

print("\n=== 6. Leading zeros stripped from UNSPSC ===")
r = run(item_in("P1", "A", cls="01010101"), item_out("P1", "A", cls="1010101"))
f = find(r, "classification")
check("diagnosed as leading-zero loss", f is not None and f.diagnosis == "leading-zeros-stripped",
      f"diagnosis={f.diagnosis if f else None}")

print("\n=== 7. Price rounded (JAGGAER 4dp rule) ===")
r = run(item_in("P1", "A", price="10.2345"), item_out("P1", "A", price="10.23"))
f = find(r, "unit_price")
check("diagnosed as rounding", f is not None and f.diagnosis == "rounded",
      f"diagnosis={f.diagnosis if f else None}")

print("\n=== 8. Cosmetic reformat is NOT reported as corruption ===")
r = run(item_in("P1", "A", price="10.00"), item_out("P1", "A", price="10.0"))
f = find(r, "unit_price")
check("downgraded to preserved", f is not None and f.outcome != CORRUPTED,
      f"outcome={f.outcome if f else None}, diagnosis={f.diagnosis if f else None}")

print("\n=== 9. Header Total recomputed by the buyer ===")
r = run(item_in("P1", "A"), item_out("P1", "A"), cart_total="199.99", order_total="15.80")
f = find(r, "total")
check("total corruption detected", f is not None and f.outcome == CORRUPTED)
check("explanation names the JAGGAER behaviour",
      f is not None and f.explanation and "recomputing" in f.explanation)

print("\n=== 10. Description truncated at 256 (JAGGAER) ===")
long_desc = "A" * 300
r = run(item_in("P1", "A", desc=long_desc), item_out("P1", "A", desc=long_desc[:256]))
f = find(r, "description")
check("diagnosed as truncation", f is not None and f.diagnosis == "truncated")
check("severity is warning not critical", f is not None and f.severity == "warning",
      f"severity={f.severity if f else None}")

print("\n=== 11. Unmatched cart line (buyer silently dropped it) ===")
r = run(item_in("P1", "A") + item_in("P2", "B"), item_out("P1", "A"))
check("unmatched cart line reported", r.unmatched_cart == [1], f"unmatched_cart={r.unmatched_cart}")
check("not clean", not r.clean)

print("\n" + "=" * 62)
passed = sum(1 for _, c, _ in results if c)
print(f"{passed}/{len(results)} checks passed")
if passed != len(results):
    print("\nFAILURES:")
    for name, cond, detail in results:
        if not cond:
            print(f"  - {name}: {detail}")
    sys.exit(1)
