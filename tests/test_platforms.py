"""Platform ingestion profiles — what a buyer system does to a valid cart.

The claim being tested is narrow and important: **a document can pass every
validator and still arrive wrong.** These profiles model the gap, so the
assertions here are mostly about that gap being reported honestly —

  * that a silent corruption is labelled as silent, because it is the only
    outcome that costs real money;
  * that a rule we cannot fully evidence is marked unverified rather than
    asserted or dropped;
  * that the same cart gets DIFFERENT verdicts from different platforms,
    which is the whole reason a single "is it valid" answer is not enough.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import platforms
from app.differ import Line
from app.platforms import CORRUPT, PRESERVE, REJECT_LOUD, PROFILES, ingest

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def line(**kw):
    base = dict(index=1, line_number="1", supplier_part_id="MSC-1001",
                quantity="1", unit_price="9.99", currency="GBP",
                unit_of_measure="EA", description="Nitrile gloves",
                classification="UNSPSC:14111507")
    base.update(kw)
    return Line(**base)


def effects_for(result, field_name):
    return [e for e in result.effects if e.field == field_name]


print("\n1. A clean cart survives every platform")
clean = [line()]
for profile in PROFILES:
    result = ingest(clean, profile.key)
    # Oracle renames EA to EACH, which IS a change and should be reported.
    if profile.key == "oracle":
        check(f"{profile.key} renames EA to EACH",
              result.lines[0].unit_of_measure == "EACH",
              "many Oracle configurations require EACH")
        continue
    check(f"{profile.key} leaves it alone", result.verdict == "clean",
          f"{[e.detail[:50] for e in result.effects]}")

print("\n2. The £299.70 case — JAGGAER's silent unit default")
boxed = [line(unit_of_measure="BX30", quantity="1", unit_price="9.99")]
jaggaer = ingest(boxed, "jaggaer")
uom = effects_for(jaggaer, "unit_of_measure")
check("an unrecognised unit is defaulted", len(uom) == 1, f"{len(uom)} effects")
check("...to EA", jaggaer.lines[0].unit_of_measure == "EA")
check("...silently, which is what makes it dangerous",
      uom[0].outcome == CORRUPT,
      "no error is raised; the buyer computes a different total")
check("...and the explanation names the real consequence",
      "299.70" in uom[0].detail, uom[0].detail[:80])

print("\n3. Coupa fails loudly where JAGGAER fails silently")
coupa = ingest(boxed, "coupa")
check("Coupa rejects the same cart", coupa.verdict == "rejected",
      "the cart import fails when the unit does not already exist")
check("...and the unit is left untouched, because nothing was ingested",
      coupa.lines[0].unit_of_measure == "BX30")
check("the two platforms disagree about the same document",
      jaggaer.verdict != coupa.verdict,
      f"JAGGAER={jaggaer.verdict}, Coupa={coupa.verdict} — one number cannot "
      "describe both")

print("\n4. Forbidden characters are a hard reject, not a truncation")
bad_id = [line(supplier_part_id="ABC?123")]
for key in ("ariba", "strict"):
    result = ingest(bad_id, key)
    check(f"{key} rejects '?' in a SupplierPartID",
          result.verdict == "rejected"
          and any(e.outcome == REJECT_LOUD for e in result.effects))
check("...and JAGGAER, which has no such rule, does not",
      ingest(bad_id, "jaggaer").verdict != "rejected")

print("\n5. Ariba counts BYTES, not characters")
# 200 CJK characters is 600 bytes — comfortably inside a 255-CHARACTER limit
# and comfortably outside a 255-BYTE one. Getting this wrong is the reason
# Japanese descriptions truncate at about 666 characters.
cjk = [line(description="製" * 200)]
ariba = ingest(cjk, "ariba")
check("200 CJK characters exceed Ariba's 255-byte description limit",
      any(e.field == "description" and e.outcome == CORRUPT
          for e in ariba.effects),
      "600 bytes — a character-counting implementation would call this fine")
check("...and the surviving text is still valid UTF-8",
      "�" not in ariba.lines[0].description,
      "cutting a multi-byte character in half would corrupt it further")

jaggaer_cjk = ingest(cjk, "jaggaer")
check("JAGGAER, counting characters, accepts the same description",
      not any(e.field == "description" for e in jaggaer_cjk.effects),
      "200 characters is inside 256 — the same data, two answers")

print("\n6. Truncation, rounding and code cleaning")
messy = [line(description="x" * 400, unit_price="9.99123",
              classification="UNSPSC:14-11-15-07")]
strict = ingest(messy, "strict")
check("an over-long description is truncated",
      len(strict.lines[0].description) == 255,
      f"{len(strict.lines[0].description)} chars")
check("a price with 5 decimals is rounded to 4",
      strict.lines[0].unit_price == "9.9912",
      strict.lines[0].unit_price)
check("punctuation is stripped from the UNSPSC code",
      strict.lines[0].classification == "UNSPSC:14111507",
      strict.lines[0].classification)
check("every one of those is reported as silent",
      all(e.outcome == CORRUPT for e in strict.effects),
      "a change nobody is told about is the failure mode that costs money")

print("\n7. Non-ASCII and the cXML-urlencoded rule")
accented = [line(description="Café crème, 30 × gloves")]
result = ingest(accented, "strict")
check("characters outside us-ascii are flagged",
      any(e.field == "description" for e in result.effects))
check("...and shown becoming '?'", "?" in result.lines[0].description,
      result.lines[0].description)
check("...with the fix named",
      any("cXML-base64" in e.detail for e in result.effects),
      "flagging a problem without the remedy is half an answer")

print("\n8. Uncertain rules say so")
oracle = ingest([line()], "oracle")
check("the Oracle EACH rule is marked unverified",
      any(not e.verified for e in oracle.effects),
      "configuration-dependent, so a likely failure rather than a certain one")
check("...and the profile carries a caveat",
      bool(platforms.BY_KEY["oracle"].caveat))
verified_rules = [e for e in ingest(messy, "jaggaer").effects if e.verified]
check("documented rules are NOT marked unverified", bool(verified_rules),
      "otherwise the label means nothing")

print("\n9. The differ agrees with the effects")
report = strict.report
check("the differ independently sees the damage",
      report is not None and not report.clean,
      "two views of the same change that disagree would be worse than one")
mechanisms = {f.diagnosis for line_diff in report.lines
              for f in line_diff.fields if f.diagnosis}
check("...and names the mechanisms", bool(mechanisms), sorted(mechanisms))

print("\n10. An unknown platform is an error, not a silent pass")
try:
    ingest([line()], "not-a-platform")
    check("unknown profile raises", False, "it returned instead")
except KeyError:
    check("unknown profile raises", True)

print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("Platform ingestion is modelled, and its uncertainty is labelled.")
