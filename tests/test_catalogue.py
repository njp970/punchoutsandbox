"""Catalogue invariants.

These are properties that decay silently as products are added — which is
exactly why they are asserted rather than assumed. The many-to-many check
below already caught a real regression once: the first draft of the catalogue
had 19 categories spanning several UNSPSC codes but not a single UNSPSC code
appearing in more than one category, i.e. half the relationship had quietly
collapsed to 1:1.
"""
import sys

sys.path.insert(0, "/Users/neilparkes/punchout")

from app.catalogue.data import (
    BY_SKU, CATEGORIES, CATEGORY_BY_ID, CATEGORY_UNSPSC_SPREAD, PRODUCTS,
    UNSPSC_CATEGORY_SPREAD, ancestry, products_in_tree,
)
from app.catalogue.models import Quirk
from app.catalogue.taxonomy import UNSPSC, is_valid_code

failures: list[str] = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        failures.append(name)


print("\n=== Structure ===")
check("every product's category exists",
      all(p.category in CATEGORY_BY_ID for p in PRODUCTS),
      [p.sku for p in PRODUCTS if p.category not in CATEGORY_BY_ID])
check("every category parent exists",
      all(c.parent is None or c.parent in CATEGORY_BY_ID for c in CATEGORIES))
check("SKUs are unique", len(BY_SKU) == len(PRODUCTS))
check("products hang off LEAF categories only",
      not [p.sku for p in PRODUCTS
           if any(c.parent == p.category for c in CATEGORIES)],
      "a product on a non-leaf category makes browse counts double-count")
check("tree is 3 levels deep",
      max(len(ancestry(c.id)) for c in CATEGORIES) == 3,
      f"max depth {max(len(ancestry(c.id)) for c in CATEGORIES)}")

print("\n=== Classification ===")
unknown = [
    (p.sku, p.unspsc) for p in PRODUCTS
    if not p.has(Quirk.PUNCTUATED_UNSPSC)
    and (not is_valid_code(p.unspsc) or p.unspsc not in UNSPSC)
]
check("all UNSPSC codes are real and known", not unknown, unknown)
check("the punctuated-UNSPSC quirk is genuinely invalid",
      not is_valid_code(BY_SKU["MSC-Q107"].unspsc),
      "if this passes validation the quirk exercises nothing")

print("\n=== The many-to-many invariant (see models.py) ===")
multi_cat = {k: v for k, v in UNSPSC_CATEGORY_SPREAD.items() if len(v) > 1}
multi_uns = {k: v for k, v in CATEGORY_UNSPSC_SPREAD.items() if len(v) > 1}
check("a UNSPSC code appears under several categories", bool(multi_cat),
      "mapping has collapsed to one category per code — the tell of a generated catalogue")
check("a category spans several UNSPSC codes", bool(multi_uns))

print("\n=== Pricing ===")
check("price breaks descend with quantity",
      all(
          all(t.unit_price < p.unit_price for t in p.price_breaks)
          and sorted(p.price_breaks, key=lambda t: t.min_qty)
          == sorted(p.price_breaks, key=lambda t: -t.unit_price)
          for p in PRODUCTS if p.price_breaks
      ),
      "a tier that costs more at higher volume is a data error, not a strategy")
check("price_for resolves to the best qualifying tier",
      BY_SKU["MSC-1001"].price_for(100) == min(
          t.unit_price for t in BY_SKU["MSC-1001"].price_breaks),
      BY_SKU["MSC-1001"].price_for(100))
check("price_for falls back to unit_price below the first tier",
      BY_SKU["MSC-1001"].price_for(1) == BY_SKU["MSC-1001"].unit_price)
check("pack products declare pack_size > 1",
      all(p.pack_size > 1 for p in PRODUCTS if p.is_pack))

print("\n=== Deliberate imperfections ===")
quirked = [p for p in PRODUCTS if p.quirks]
check("every Quirk variant is represented in the data",
      {q for p in quirked for q in p.quirks} == set(Quirk),
      f"missing: {set(Quirk) - {q for p in quirked for q in p.quirks}}")
check("quirks are a minority of the catalogue",
      0 < len(quirked) / len(PRODUCTS) < 0.15,
      f"{len(quirked)}/{len(PRODUCTS)}")
check("the over-length description really exceeds 256 chars",
      len(BY_SKU["MSC-Q102"].description) > 256,
      len(BY_SKU["MSC-Q102"].description))
check("the over-length aux ID really exceeds 100 chars",
      len(BY_SKU["MSC-Q105"].aux_token) > 100,
      len(BY_SKU["MSC-Q105"].aux_token))
check("the non-ASCII description really is non-ASCII",
      not BY_SKU["MSC-Q104"].description.isascii())
check("the sub-penny price really has >4 decimal places... or exactly 4",
      BY_SKU["MSC-Q106"].unit_price.as_tuple().exponent < -2,
      BY_SKU["MSC-Q106"].unit_price)

print("\n=== Legal (BRIEF.md §6) ===")
# Substring matching does not work here, and the first version of this check
# proved it by failing on clean data: it flagged "3m" inside "33mm" and
# "13mm", "hp" inside "HPPE" (the fibre in cut-resistant gloves), and
# "staples" — the ordinary English word, in a catalogue that sells staples.
#
# So: word boundaries, and case-SENSITIVE for the short acronyms, where a
# case-insensitive match is indistinguishable from ordinary text.
#
# Deliberately NOT checked: "staples", "apple", "shell", "orange", "pilot",
# "canon". Each is a real trademark AND an ordinary word a genuine catalogue
# needs. No automated check can separate them, and a check that fires on
# correct data trains people to ignore it. Reviewer judgement covers those.
import re

CASE_SENSITIVE_BRANDS = ("3M", "HP", "IBM", "GE", "BIC", "RS")
BRANDS = (
    "amazon", "grainger", "dell", "lenovo", "kimberly-clark", "office depot",
    "screwfix", "cdw", "insight", "vwr", "honeywell", "post-it", "brother",
    "epson", "xerox", "samsung", "microsoft", "logitech", "jabra", "fastenal",
    "tradecentric", "ariba", "coupa", "jaggaer",
)
blob = " ".join(f"{p.manufacturer} {p.name} {p.description}" for p in PRODUCTS)
hits = [b for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", blob, re.I)]
hits += [b for b in CASE_SENSITIVE_BRANDS if re.search(rf"\b{re.escape(b)}\b", blob)]
check("no real company or brand names anywhere in the catalogue", not hits, hits)
check("every manufacturer is one of the invented house brands",
      {p.manufacturer for p in PRODUCTS} <= {
          "Meridian", "Kestrel", "Quillon", "Marlowe", "Stanmore", "Ashcroft",
          "Vantor", "Lumen", "Beckwith", "Orrell", "Pellworth", "Cartwright",
          "Brightwell", "Halyard", "Ferrum",
      },
      {p.manufacturer for p in PRODUCTS})

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all catalogue invariants hold")
