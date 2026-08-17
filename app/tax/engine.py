"""Tax determination and calculation.

*Spec: `docs/reference/invoice-and-tax.md`. Read §1 before editing — several
"obvious" facts about cXML tax are wrong.*

=============================================================================
DECIMAL, NEVER FLOAT — AND THIS IS A HARD CONFORMANCE REQUIREMENT
=============================================================================
PEPPOL rules `BR-DEC-01` … `BR-DEC-28` cap every monetary amount at two
decimal places, and a float serialiser emitting `1250.0000000001` fails
fatally. Beyond that, floats cannot represent 0.1, so a float engine invents
rounding errors and then this sandbox reports them as the buyer's fault.

There is no float anywhere in this module and there must never be one.

=============================================================================
THE ROUNDING DECISION IS THE INTERESTING PART
=============================================================================
Tax on a five-line invoice can be computed two ways:

  PER LINE   round(line1 x rate) + round(line2 x rate) + ...
  HEADER     round((line1 + line2 + ...) x rate)

**These give different answers**, by a penny or two, and both are defensible.
Buyers and suppliers pick different ones and then disagree at invoice
matching — one of the documented failure modes in `platform-conformance.md`.

So `Rounding` is an explicit input, not a hidden default, and
`TaxCalculation.rounding_delta` reports what the *other* method would have
produced. Being able to show a user "your ERP computes this per line, your
supplier computes it at header level, here is the 2p that will block your
three-way match every month" is worth more than picking a side silently.

=============================================================================
REVERSE CHARGE IN cXML IS A CONVENTION WE ARE INVENTING
=============================================================================
This must be surfaced honestly wherever it appears. **cXML has no
reverse-charge construct.** UBL has `AE` as a first-class category code;
cXML has nothing.

The working pattern — `exemptDetail="exempt"`, zero rate, both parties' VAT
numbers present, statutory wording in `TaxDetail/Description`, and a
`TaxExemption exemptCode="AE"` borrowing the UNCL5305 code so a downstream
PEPPOL mapper can round-trip it — is assembled from DTD primitives and
industry practice. It appears in no specification.

`TaxTreatment.REVERSE_CHARGE` therefore carries `is_convention=True`, and any
UI rendering it must say so. Presenting a convention as a standard is exactly
the false authority `validation.py` refuses to engage in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal as D
from enum import Enum
from typing import Optional

from . import currency as currency_precision
from .rates import EU_MEMBERS, Jurisdiction, TaxSystem, get

TWO_PLACES = D("0.01")


def money(value: D, currency: str = "") -> D:
    """Round to the currency's own precision, half-up. The single rounding
    entry point.

    Half-up rather than Python's default banker's rounding: ROUND_HALF_EVEN
    would turn 2.345 into 2.34, which is correct statistically and wrong
    commercially — invoices round half away from zero, and a buyer checking
    our arithmetic by hand would get a different answer.

    `currency` decides the number of places. It used to be two, unconditionally
    — so a yen invoice read `JPY 1000.00`, and the yen has no minor unit. An
    amount claiming a precision its currency does not have is wrong in exactly
    the quiet way this service exists to catch: the DTD only knows the field is
    a number, so it validates, and the buyer platform decides what to do about
    it. Empty means two, which is what almost every currency is."""
    return currency_precision.quantize(value, currency)


class Rounding(str, Enum):
    PER_LINE = "per-line"
    HEADER = "header"


class TaxTreatment(str, Enum):
    """How a supply is taxed. The value doubles as the UI slug."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO_RATED = "zero-rated"
    EXEMPT = "exempt"
    REVERSE_CHARGE = "reverse-charge"
    INTRA_COMMUNITY = "intra-community"
    OUT_OF_SCOPE = "out-of-scope"
    EXPORT = "export"

    @property
    def is_zero(self) -> bool:
        return self in (
            TaxTreatment.ZERO_RATED, TaxTreatment.EXEMPT,
            TaxTreatment.REVERSE_CHARGE, TaxTreatment.INTRA_COMMUNITY,
            TaxTreatment.OUT_OF_SCOPE, TaxTreatment.EXPORT,
        )

    @property
    def is_convention(self) -> bool:
        """True when cXML has no native construct and we are inventing the
        encoding. See the module docstring."""
        return self in (
            TaxTreatment.REVERSE_CHARGE,
            TaxTreatment.INTRA_COMMUNITY,
            TaxTreatment.OUT_OF_SCOPE,
            TaxTreatment.EXPORT,
        )

    @property
    def uncl5305(self) -> str:
        """The PEPPOL/EN 16931 category code (UNCL5305).

        Note there are TEN codes, not nine — `B` (Italian split payment) is
        the one hardcoded enums omit, and omitting it wrongly rejects Italian
        invoices. It has no cXML treatment equivalent, so it is not produced
        here, but any *validator* we write must accept it."""
        return {
            TaxTreatment.STANDARD: "S",
            TaxTreatment.REDUCED: "S",       # reduced is still category S, lower rate
            TaxTreatment.ZERO_RATED: "Z",
            TaxTreatment.EXEMPT: "E",
            TaxTreatment.REVERSE_CHARGE: "AE",
            TaxTreatment.INTRA_COMMUNITY: "K",
            TaxTreatment.EXPORT: "G",
            TaxTreatment.OUT_OF_SCOPE: "O",
        }[self]

    @property
    def vatex(self) -> Optional[str]:
        """The VATEX exemption-reason code PEPPOL hard-binds to this category.

        These bindings are fatal rules, not suggestions: P0104 requires
        category G to carry VATEX-EU-G, P0106 binds K to VATEX-EU-IC, P0107
        binds AE to VATEX-EU-AE. You cannot pick freely."""
        return {
            TaxTreatment.REVERSE_CHARGE: "VATEX-EU-AE",
            TaxTreatment.INTRA_COMMUNITY: "VATEX-EU-IC",
            TaxTreatment.EXPORT: "VATEX-EU-G",
            TaxTreatment.OUT_OF_SCOPE: "VATEX-EU-O",
        }.get(self)

    @property
    def statutory_wording(self) -> Optional[str]:
        """The narrative an invoice must carry for this treatment. Legally
        required on the document itself, not merely nice to have."""
        return {
            TaxTreatment.REVERSE_CHARGE: (
                "Reverse charge — VAT to be accounted for by the recipient "
                "under Article 196 of Council Directive 2006/112/EC"
            ),
            TaxTreatment.INTRA_COMMUNITY: (
                "Intra-Community supply — zero-rated under Article 138 of "
                "Council Directive 2006/112/EC"
            ),
            TaxTreatment.EXPORT: "Export of goods — outside the scope of VAT",
            TaxTreatment.OUT_OF_SCOPE: "Not subject to VAT",
        }.get(self)


@dataclass(frozen=True)
class TaxableLine:
    """One invoice line reduced to what tax cares about."""

    line_number: int
    net_amount: D                  # quantity x unit price, already resolved
    treatment: Optional[TaxTreatment] = None   # None -> use the document default
    rate_override: Optional[D] = None          # e.g. a reduced rate on one line


@dataclass(frozen=True)
class TaxLine:
    """One computed tax band — becomes a cXML `TaxDetail` or a UBL
    `TaxSubtotal`."""

    treatment: TaxTreatment
    rate: D
    taxable_amount: D
    tax_amount: D
    category: str                  # cXML TaxDetail@category — FREE TEXT, see below
    uncl5305: str
    exempt_detail: Optional[str] = None   # (zeroRated | exempt) — the ONLY enum
    vatex: Optional[str] = None
    description: str = ""


@dataclass
class TaxCalculation:
    jurisdiction: Jurisdiction
    treatment: TaxTreatment
    rounding: Rounding
    subtotal: D
    tax_total: D
    net_total: D                   # subtotal + tax
    lines: list[TaxLine] = field(default_factory=list)
    #: What the OTHER rounding method would have produced, minus what we
    #: produced. Zero most of the time; a penny or two when it matters.
    rounding_delta: D = D("0.00")
    notes: list[str] = field(default_factory=list)

    @property
    def is_convention(self) -> bool:
        return self.treatment.is_convention

    def as_dict(self) -> dict:
        return {
            "jurisdiction": self.jurisdiction.code,
            "treatment": self.treatment.value,
            "isConvention": self.is_convention,
            "rounding": self.rounding.value,
            "subtotal": str(self.subtotal),
            "taxTotal": str(self.tax_total),
            "netTotal": str(self.net_total),
            "roundingDelta": str(self.rounding_delta),
            "notes": self.notes,
            "lines": [
                {
                    "treatment": t.treatment.value,
                    "rate": str(t.rate),
                    "taxableAmount": str(t.taxable_amount),
                    "taxAmount": str(t.tax_amount),
                    "category": t.category,
                    "uncl5305": t.uncl5305,
                    "exemptDetail": t.exempt_detail,
                    "vatex": t.vatex,
                    "description": t.description,
                }
                for t in self.lines
            ],
        }


# --------------------------------------------------------------------------- #
# Determination
# --------------------------------------------------------------------------- #
def determine(
    *,
    supplier_country: str,
    buyer_country: str,
    buyer_has_tax_id: bool,
    goods: bool = True,
) -> tuple[TaxTreatment, list[str]]:
    """Decide how a supply is taxed, and explain why.

    Returns `(treatment, reasons)`. The reasons are shown to the user, because
    "why is this zero-rated?" is the question a sandbox exists to answer.

    The rules encoded here are the ordinary ones and are deliberately shallow:
    place-of-supply for services, distance selling thresholds, the One Stop
    Shop, and triangulation are all real and none is modelled. A tool that
    silently produced a confident answer for a case it does not understand
    would be worse than one that says so, hence `notes`."""
    reasons: list[str] = []
    supplier = get(supplier_country)

    # Checked before the domestic branch: a jurisdiction with no transaction
    # tax has no "standard rate" to apply, and calling it STANDARD-at-0%
    # would emit a document claiming a tax band that does not exist.
    if supplier.system is TaxSystem.NONE:
        reasons.append(
            f"{supplier.name} levies no transaction tax at all, so there is no "
            "tax band to report — not a zero-rated one, and not an exempt one. "
            "This is genuinely out of scope."
        )
        return TaxTreatment.OUT_OF_SCOPE, reasons

    if supplier_country == buyer_country:
        reasons.append(
            f"Domestic supply within {supplier.name}: {supplier.tax_name} at "
            f"the applicable rate."
        )
        return TaxTreatment.STANDARD, reasons

    # --- US sales tax: no reverse charge exists. See rates.py docstring. ----
    if supplier.system is TaxSystem.SALES_TAX:
        reasons.append(
            "US sales tax has NO reverse-charge mechanism. A cross-border or "
            "out-of-state supply is handled by the buyer self-assessing USE "
            "TAX, or by an exemption/resale certificate — not by shifting the "
            "liability on the invoice. Treated as out of scope here."
        )
        return TaxTreatment.OUT_OF_SCOPE, reasons

    # --- EU intra-community -------------------------------------------------
    if supplier.eu and buyer_country in EU_MEMBERS:
        if not buyer_has_tax_id:
            reasons.append(
                "Buyer is in another EU member state but supplied no VAT "
                "number, so this cannot be zero-rated: the supplier must "
                "charge domestic VAT. A VAT number is the evidence the "
                "zero-rating depends on."
            )
            return TaxTreatment.STANDARD, reasons
        if goods:
            reasons.append(
                "Intra-Community supply of goods to a VAT-registered business "
                "in another member state — zero-rated under Article 138. Both "
                "VAT numbers must appear on the invoice, and PEPPOL "
                "additionally requires the actual delivery date and the "
                "deliver-to country code (BR-IC-11, BR-IC-12)."
            )
            return TaxTreatment.INTRA_COMMUNITY, reasons
        reasons.append(
            "B2B service to a VAT-registered business in another member "
            "state — place of supply is the customer's (Article 44) and the "
            "reverse charge applies (Article 196)."
        )
        return TaxTreatment.REVERSE_CHARGE, reasons

    # --- Export out of the tax area ----------------------------------------
    if supplier.eu and buyer_country not in EU_MEMBERS:
        reasons.append(
            f"Export from the EU to {buyer_country} — outside the scope of EU "
            "VAT. Evidence of export is required to support the zero rate."
        )
        return TaxTreatment.EXPORT, reasons

    if supplier.reverse_charge and buyer_has_tax_id:
        reasons.append(
            f"Cross-border B2B supply from {supplier.name}; the recipient "
            "accounts for the tax under the reverse charge."
        )
        return TaxTreatment.REVERSE_CHARGE, reasons

    reasons.append(
        f"Cross-border supply from {supplier.name} with no reverse charge "
        "available (buyer is not tax-registered), so domestic "
        f"{supplier.tax_name} applies."
    )
    return TaxTreatment.STANDARD, reasons


# --------------------------------------------------------------------------- #
# Calculation
# --------------------------------------------------------------------------- #
def _rate_for(jurisdiction: Jurisdiction, treatment: TaxTreatment,
              override: Optional[D]) -> D:
    if override is not None:
        return override
    if treatment.is_zero:
        return D("0")
    if treatment is TaxTreatment.REDUCED:
        if not jurisdiction.reduced:
            raise ValueError(
                f"{jurisdiction.name} has no reduced rate in our data; asking "
                "for one would invent a figure."
            )
        return jurisdiction.reduced[0]
    return jurisdiction.standard


def calculate(
    lines: list[TaxableLine],
    *,
    jurisdiction_code: str,
    treatment: TaxTreatment,
    rounding: Rounding = Rounding.PER_LINE,
    currency: str = "",
) -> TaxCalculation:
    """Compute the tax bands for an invoice.

    Lines are grouped by `(treatment, rate)` — **not by treatment alone**.
    PEPPOL's `BR-S-08` is explicit that the breakdown groups by the
    combination, and the classic bug is producing one `S` subtotal that sums
    two different standard rates."""
    j = get(jurisdiction_code)
    notes: list[str] = []

    # Group by (treatment, rate). See BR-S-08.
    bands: dict[tuple[TaxTreatment, D], D] = {}
    for line in lines:
        line_treatment = line.treatment or treatment
        rate = _rate_for(j, line_treatment, line.rate_override)
        key = (line_treatment, rate)
        bands[key] = bands.get(key, D("0")) + line.net_amount

    tax_lines: list[TaxLine] = []
    for (band_treatment, rate), taxable in sorted(
        bands.items(), key=lambda kv: (kv[0][0].value, kv[0][1])
    ):
        taxable = money(taxable, currency)
        if rounding is Rounding.PER_LINE:
            amount = sum(
                (money(l.net_amount * rate / D("100"), currency) for l in lines
                 if (l.treatment or treatment, _rate_for(j, l.treatment or treatment,
                                                         l.rate_override)) == (band_treatment, rate)),
                D("0"),
            )
        else:
            amount = money(taxable * rate / D("100"), currency)

        tax_lines.append(
            TaxLine(
                treatment=band_treatment,
                rate=rate,
                taxable_amount=taxable,
                tax_amount=money(amount, currency),
                # FREE TEXT, deliberately. TaxDetail@category is %string; in the
                # DTD, not an enumeration — real traffic carries "vat", "CA" and
                # "Standard Rate". A validator that enum-checks this rejects
                # valid documents, which is the one thing we may not do.
                category=j.system.value,
                uncl5305=band_treatment.uncl5305,
                exempt_detail=(
                    "zeroRated" if band_treatment is TaxTreatment.ZERO_RATED
                    else "exempt" if band_treatment.is_zero else None
                ),
                vatex=band_treatment.vatex,
                description=(
                    band_treatment.statutory_wording
                    or f"{j.tax_name} @ {rate}%"
                ),
            )
        )

    subtotal = money(sum((l.net_amount for l in lines), D("0")), currency)
    tax_total = money(sum((t.tax_amount for t in tax_lines), D("0")), currency)

    # What would the other method have given? Reported, never silently applied.
    other = Rounding.HEADER if rounding is Rounding.PER_LINE else Rounding.PER_LINE
    alt_total = D("0")
    for (band_treatment, rate), taxable in bands.items():
        if other is Rounding.HEADER:
            alt_total += money(money(taxable, currency) * rate / D("100"), currency)
        else:
            alt_total += sum(
                (money(l.net_amount * rate / D("100"), currency) for l in lines
                 if (l.treatment or treatment, _rate_for(j, l.treatment or treatment,
                                                         l.rate_override)) == (band_treatment, rate)),
                D("0"),
            )
    delta = money(alt_total, currency) - tax_total
    if delta:
        notes.append(
            f"Rounding {rounding.value} gives {tax_total}; rounding "
            f"{other.value} would give {money(alt_total, currency)}, a difference of "
            f"{delta}. Both are defensible, and a buyer using the other method "
            "will disagree with this invoice by that amount every time."
        )

    if treatment.is_convention:
        notes.append(
            f"cXML has NO native construct for '{treatment.value}'. The "
            "encoding produced here (zero rate, exemptDetail, statutory "
            "wording in the description, and a borrowed UNCL5305 code) is an "
            "industry convention assembled from DTD primitives — it appears "
            "in no specification. UBL expresses it properly as category "
            f"'{treatment.uncl5305}'."
        )
    if not j.verified:
        notes.append(
            f"The standard rate for {j.name} was NOT verified against a cited "
            "2026 source. Treat it as illustrative."
        )
    if j.caveat:
        notes.append(f"{j.name}: {j.caveat}")

    return TaxCalculation(
        jurisdiction=j,
        treatment=treatment,
        rounding=rounding,
        subtotal=subtotal,
        tax_total=tax_total,
        net_total=money(subtotal + tax_total, currency),
        lines=tax_lines,
        rounding_delta=delta,
        notes=notes,
    )
