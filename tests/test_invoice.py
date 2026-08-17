"""Do the invoices we generate actually conform?

This is the test that matters most in the repository. A sandbox that ships a
DTD validator AND an invoice generator, where the generator's own output
fails the validator, would be indefensible — so every document built here is
run through `validation.validate()` against the real vendored
`InvoiceDetail.dtd`.

Note what that gives us that a round-trip test cannot: `build_invoice` and
`validate` share no code and no assumptions. The DTD is a third party's
description of the format. This is the "independent judge" of BRIEF.md §2
being applied to ourselves.
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.cxml.invoice import Invoice, InvoiceLine, Party, build_invoice
from app.tax.engine import Rounding, TaxTreatment, TaxableLine, calculate
from app.validation import validate
from app.xml_safe import parse

UTC1 = timezone(timedelta(hours=1))
NOW = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC1)

SUPPLIER = Party("remitTo", "Meridian Supply Co.", "1 Trade Park", "Leeds",
                 "LS1 1AA", "GB", "United Kingdom", "GB123456789")
BUYER = Party("billTo", "Northgate Industries Ltd", "8 Kingsway", "Manchester",
              "M1 2AB", "GB", "United Kingdom", "GB987654321")

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def make(lines, treatment, jurisdiction="GB", **kw):
    taxable = [TaxableLine(l.line_number, l.subtotal) for l in lines]
    calc = calculate(taxable, jurisdiction_code=jurisdiction, treatment=treatment)
    return Invoice(
        invoice_id=kw.pop("invoice_id", "INV-2026-0001"),
        invoice_date=NOW, order_id="PO-4500012345",
        order_payload_id="1755162000.1234@buyer.example.com",
        currency=kw.pop("currency", "GBP"), lines=lines, tax=calc,
        parties=[SUPPLIER, BUYER], **kw,
    )


def emit(invoice):
    return build_invoice(
        invoice, payload_id="inv-1@meridian.example",
        timestamp=NOW, from_identity="meridian", to_identity="northgate",
        sender_identity="meridian", shared_secret="s3cret",
    )


def conformant(raw, label):
    report = validate(parse(raw), expected_type="InvoiceDetailRequest")
    if not report.conformant:
        for f in report.errors:
            print(f"         line {f.line}: {f.message}")
    check(f"{label} is DTD-conformant", report.conformant,
          f"{len(report.errors)} errors, {len(report.advisories)} advisories")
    return report


LINES = [
    InvoiceLine(1, D("10"), "EA", D("4.85"), "MSC-1001",
                "Meridian A4 Copier Paper 80gsm", 1,
                supplier_part_auxiliary_id="CTR-STDPAPER",
                classification="14111507",
                manufacturer_part_id="MER-A4-80", manufacturer_name="Meridian"),
    InvoiceLine(2, D("2"), "BX", D("11.40"), "MSC-1100",
                "Marlowe Ballpoint Pen Blue, Box of 50", 2,
                supplier_part_auxiliary_id="CTR-PENS",
                classification="44121704"),
]

print("\n=== 1. Standard domestic VAT invoice ===")
inv = make(LINES, TaxTreatment.STANDARD)
raw = emit(inv)
conformant(raw, "standard invoice")
check("tax is 20% of subtotal", inv.tax.tax_total == D("14.26"),
      f"subtotal={inv.tax.subtotal} tax={inv.tax.tax_total}")
check("Tax carries the mandatory Description", b"<Description xml:lang=\"en\">VAT<" in raw)
check("both indicators present and ordered",
      raw.index(b"InvoiceDetailHeaderIndicator") < raw.index(b"InvoiceDetailLineIndicator"))
check("UnitOfMeasure precedes UnitPrice inside the item",
      raw.index(b"<UnitOfMeasure>") < raw.index(b"<UnitPrice>"))
check("UnitPrice precedes the item reference",
      raw.index(b"<UnitPrice>") < raw.index(b"<InvoiceDetailItemReference"))

print("\n=== 2. Reverse charge (cXML has no native construct) ===")
inv = make(LINES, TaxTreatment.REVERSE_CHARGE, jurisdiction="DE", currency="EUR")
raw = emit(inv)
conformant(raw, "reverse-charge invoice")
check("tax total is zero", inv.tax.tax_total == D("0.00"))
check("exemptDetail is 'exempt'", b'exemptDetail="exempt"' in raw)
check("borrowed VATEX code present for PEPPOL round-trip",
      b'exemptCode="VATEX-EU-AE"' in raw)
check("statutory Article 196 wording present", b"Article 196" in raw)
check("the convention is flagged, not presented as standard",
      any("NO native construct" in n for n in inv.tax.notes))

print("\n=== 3. Zero-rated and exempt ===")
inv = make(LINES, TaxTreatment.ZERO_RATED)
raw = emit(inv)
conformant(raw, "zero-rated invoice")
check("exemptDetail is 'zeroRated' not 'exempt'", b'exemptDetail="zeroRated"' in raw)

print("\n=== 4. Credit memo ===")
inv = make(LINES, TaxTreatment.STANDARD, invoice_id="CN-2026-0001",
           purpose="creditMemo",
           original_payload_id="inv-1@meridian.example")
raw = emit(inv)
conformant(raw, "credit memo")
check("isHeaderInvoice='yes' as the rules require", b'isHeaderInvoice="yes"' in raw)
check("DueAmount is negative",
      b"<DueAmount><Money currency=\"GBP\">-" in raw,
      raw[raw.index(b"<DueAmount>"):raw.index(b"<DueAmount>") + 60].decode())
check("DocumentReference identifies the original", b"<DocumentReference" in raw)

try:
    make(LINES, TaxTreatment.STANDARD, purpose="creditMemo")
    emit(make(LINES, TaxTreatment.STANDARD, purpose="creditMemo"))
    check("credit memo without DocumentReference is refused", False)
except ValueError as exc:
    check("credit memo without DocumentReference is refused", True, str(exc)[:70])

print("\n=== 5. Mixed rates in one invoice (BR-S-08 grouping) ===")
taxable = [TaxableLine(1, D("100.00")),
           TaxableLine(2, D("50.00"), TaxTreatment.REDUCED),
           TaxableLine(3, D("25.00"), TaxTreatment.ZERO_RATED)]
calc = calculate(taxable, jurisdiction_code="GB", treatment=TaxTreatment.STANDARD)
inv = Invoice("INV-MIX", NOW, "PO-1", "p@x", "GBP", LINES, calc, [SUPPLIER, BUYER])
raw = emit(inv)
conformant(raw, "mixed-rate invoice")
check("three separate TaxDetail bands", raw.count(b"<TaxDetail ") == 3,
      f"{raw.count(b'<TaxDetail ')} bands")
check("standard and reduced are NOT merged into one S band",
      len({(t.treatment, t.rate) for t in calc.lines}) == 3)

print("\n=== 6. The 'Z' timestamp cXML forbids ===")
try:
    build_invoice(make(LINES, TaxTreatment.STANDARD), payload_id="x",
                  timestamp=datetime(2026, 8, 14, 10, 0, 0),  # naive
                  from_identity="a", to_identity="b", sender_identity="a",
                  shared_secret="s")
    check("naive datetime is refused", False)
except ValueError as exc:
    check("naive datetime is refused", True, str(exc)[:70])

print("\n=== 7. Escaping ===")
nasty = [InvoiceLine(1, D("1"), "EA", D("1.00"), "P&<1",
                     'Bolts & Nuts <3" dia> "premium"', 1,
                     supplier_part_auxiliary_id="a&b<c")]
inv = make(nasty, TaxTreatment.STANDARD)
raw = emit(inv)
conformant(raw, "invoice with characters needing escaping")
check("no raw ampersand survived", b"& " not in raw and b"&<" not in raw)
check("no double-encoding", b"&amp;amp;" not in raw)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("every generated invoice validates against the real cXML DTD")


print("\n=== Currencies without two decimal places ===")
# Everything quantized to 2dp because sterling, the euro and the dollar have
# two and they are what got tested. So a yen invoice read `JPY 1000.00`, and
# the yen has no minor unit. It validated against the DTD — the DTD only knows
# the field is a number — which is exactly the quiet kind of wrong this
# service exists to catch.
from app.tax import currency as _cur

check("the yen has no minor unit", _cur.minor_units("JPY") == 0)
check("the Kuwaiti dinar has three", _cur.minor_units("KWD") == 3)
check("sterling has two", _cur.minor_units("GBP") == 2)
check("an unknown code assumes two rather than raising",
      _cur.minor_units("ZZZ") == 2,
      "an invoice in a currency we have not enumerated is better emitted "
      "with the ordinary assumption than refused")
check("lowercase is accepted", _cur.minor_units("jpy") == 0)

for code, amount, expected in (("JPY", D("1000.00"), "1000"),
                               ("GBP", D("1000"), "1000.00"),
                               ("KWD", D("1.5"), "1.500"),
                               ("JPY", D("1000.6"), "1001")):
    got = str(_cur.quantize(amount, code))
    check(f"{code} {amount} formats as {expected}", got == expected, got)

for code in ("JPY", "GBP", "KWD", "KRW"):
    lines = [InvoiceLine(1, D("3"), "EA", D("1000"), "SKU", "Widget", 1,
                         classification="14111507")]
    calc = calculate([TaxableLine(1, lines[0].subtotal)], jurisdiction_code="GB",
                     treatment=TaxTreatment.STANDARD, rounding=Rounding.PER_LINE,
                     currency=code)
    inv = Invoice("INV-C", NOW, "PO-C", "p@x", code, lines, calc,
                  [SUPPLIER, BUYER])
    raw = build_invoice(inv, payload_id="c@s", timestamp=NOW,
                        from_identity="s", to_identity="b",
                        sender_identity="s", shared_secret="k").decode()
    places = _cur.minor_units(code)
    amounts = re.findall(rf'<Money currency="{code}">([\d.]+)</Money>', raw)
    def dp(value):
        return len(value.split(".")[1]) if "." in value else 0
    check(f"every Money element in a {code} invoice carries {places} places",
          amounts and all(dp(a) == places for a in amounts),
          f"{sorted(set(amounts))}")
    report = validate(parse(raw.encode()))
    check(f"...and the {code} invoice still validates", not report.errors,
          "; ".join(e.message[:50] for e in report.errors))
