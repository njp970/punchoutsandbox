"""Tax jurisdictions and 2026 rates.

=============================================================================
⚠️ THIS IS RESEARCH OUTPUT, NOT A MAINTAINED TAX TABLE
=============================================================================
Every rate here was compiled on 2026-08-14 (see
`docs/reference/invoice-and-tax.md` §5) and carries a `verified` flag
recording whether it was confirmed against a cited source or recalled.

**Nothing in this sandbox may present these as authoritative.** The UI must
say the rates are illustrative, and `Jurisdiction.caveat` exists so a screen
can show *why* a particular figure is shaky rather than implying they are all
equally solid. A test harness that quietly ships a wrong VAT rate is worse
than one with no rates at all, because the wrongness is invisible: the
arithmetic is internally consistent either way.

The failure mode this guards against is specific and recent. South Africa
announced VAT rises to 15.5% (May 2025) and 16% (April 2026), **both of which
were withdrawn**. Every source published in March–April 2025 says 15.5% or
16% and is wrong; the rate is 15%. Similarly India abolished its 12% and 28%
slabs in September 2025, and Nova Scotia cut HST to 14% in April 2025. Those
three figures are the ones most likely to be stale in any inherited reference
data, so they are flagged individually below.

=============================================================================
WHY REVERSE CHARGE IS A PER-JURISDICTION FLAG AND NOT A GLOBAL RULE
=============================================================================
`reverse_charge` records whether a jurisdiction shifts the liability to the
buyer for cross-border B2B supplies. The EU/UK/EFTA answer is yes and is the
one everyone assumes.

**The United States is the trap.** There is no reverse charge in US sales tax
at all. The mechanism is *use tax*, self-assessed by the buyer, plus resale
and exemption certificates — a different legal construct that happens to
produce a similar-looking zero on the invoice. Modelling it as reverse charge
would emit a document that is confidently, invisibly wrong, so US
jurisdictions carry `reverse_charge=False` and `SALES_TAX`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from enum import Enum
from typing import Optional


class TaxSystem(str, Enum):
    VAT = "vat"          # EU/UK/EFTA-style value added tax
    GST = "gst"          # goods and services tax (AU, NZ, IN, CA federal, SG)
    SALES_TAX = "sales"  # US-style, no reverse charge, use-tax mechanism
    NONE = "none"        # no transaction tax at all


@dataclass(frozen=True)
class Jurisdiction:
    code: str                    # ISO 3166-1 alpha-2, or "US-CA" for a subdivision
    name: str
    system: TaxSystem
    tax_name: str                # what the invoice must call it: VAT, IVA, MwSt...
    standard: D                  # percentage, e.g. D("20") not D("0.20")
    reduced: tuple[D, ...] = ()
    currency: str = "EUR"
    tax_id_label: str = "VAT number"
    tax_id_pattern: Optional[str] = None   # format only; check digits not expressed
    reverse_charge: bool = True
    eu: bool = False
    #: True when the standard rate was confirmed against a cited 2026 source.
    verified: bool = True
    #: Why this entry is uncertain, or what recently changed about it. Shown
    #: in the UI next to the figure — see the module docstring.
    caveat: Optional[str] = None


# --------------------------------------------------------------------------- #
# UK, EU and EFTA
# --------------------------------------------------------------------------- #
_EU_UK: tuple[Jurisdiction, ...] = (
    Jurisdiction(
        "GB", "United Kingdom", TaxSystem.VAT, "VAT", D("20"), (D("5"), D("0")),
        "GBP", "VAT registration number", r"^(GB|XI)?(\d{9}(\d{3})?|(GD|HA)\d{3})$",
        caveat=("Domestic electricity is zero-rated 1 Oct 2026 – 31 Mar 2027, "
                "Great Britain only — Northern Ireland excluded. A date- and "
                "region-sensitive rate, and a good edge case to exercise."),
    ),
    Jurisdiction(
        "IE", "Ireland", TaxSystem.VAT, "VAT", D("23"),
        (D("13.5"), D("9"), D("4.8"), D("0")), "EUR", "VAT number",
        r"^IE(\d{7}[A-W][A-IW]?|\d[A-Z0-9+*]\d{5}[A-W])$", eu=True,
        caveat=("The 9% rate for food service and hairdressing was made "
                "permanent on 1 Jul 2026. Alcohol and soft drinks in "
                "restaurants remain at 23%."),
    ),
    Jurisdiction(
        "DE", "Germany", TaxSystem.VAT, "MwSt", D("19"), (D("7"),), "EUR",
        "USt-IdNr.", r"^DE\d{9}$", eu=True,
        caveat=("From 1 Jan 2026 all restaurant and catering food is "
                "permanently 7%, uniform across dine-in, takeaway and "
                "delivery. Beverages are excluded and stay at 19%."),
    ),
    Jurisdiction("FR", "France", TaxSystem.VAT, "TVA", D("20"),
                 (D("10"), D("5.5"), D("2.1")), "EUR", "No. TVA",
                 r"^FR[A-HJ-NP-Z0-9]{2}\d{9}$", eu=True,
                 caveat="Corsica and the DOM run separate schedules, not modelled here."),
    Jurisdiction("NL", "Netherlands", TaxSystem.VAT, "BTW", D("21"), (D("9"),),
                 "EUR", "BTW-nummer", r"^NL\d{9}B\d{2}$", eu=True,
                 caveat=("From 1 Jan 2026 short-stay accommodation moved from 9% "
                         "to 21%. The proposed rise for culture, books and sport "
                         "was rejected by Parliament and did NOT happen.")),
    Jurisdiction("ES", "Spain", TaxSystem.VAT, "IVA", D("21"), (D("10"), D("4")),
                 "EUR", "NIF-IVA", r"^ES[A-Z0-9]\d{7}[A-Z0-9]$", eu=True,
                 verified=False,
                 caveat=("Standard rate confirmed; the expiry of the temporary "
                         "0%/5% food cuts and the recargo de equivalencia rates "
                         "were NOT re-verified for 2026.")),
    Jurisdiction("IT", "Italy", TaxSystem.VAT, "IVA", D("22"),
                 (D("10"), D("5"), D("4")), "EUR", "Partita IVA", r"^IT\d{11}$", eu=True),
    Jurisdiction("PL", "Poland", TaxSystem.VAT, "PTU", D("23"),
                 (D("8"), D("5"), D("0")), "PLN", "NIP", r"^PL\d{10}$", eu=True,
                 verified=False,
                 caveat=("No 2026 change found, but no definitive statement that "
                         "none occurred. The long-promised reversion to 22%/7% "
                         "still has not happened.")),
    Jurisdiction("SE", "Sweden", TaxSystem.VAT, "Moms", D("25"), (D("12"), D("6")),
                 "SEK", "Momsnummer", r"^SE\d{10}01$", eu=True,
                 caveat=("Food moves 12% -> 6% from 1 Apr 2026 to 31 Dec 2027, "
                         "excluding alcohol and tap water. Takeaway 6%, dine-in "
                         "stays 12% — a split worth exercising.")),
    Jurisdiction("CH", "Switzerland", TaxSystem.VAT, "MWST", D("8.1"),
                 (D("3.8"), D("2.6")), "CHF", "UID",
                 r"^CHE-?\d{3}\.?\d{3}\.?\d{3}( ?(MWST|TVA|IVA))?$",
                 caveat=("A rise to 8.5% passed parliament in Jun 2026 but faces "
                         "a Nov 2026 referendum and would take effect no earlier "
                         "than 1 Jan 2028. DO NOT apply it in 2026.")),
    Jurisdiction("NO", "Norway", TaxSystem.VAT, "MVA", D("25"), (D("15"), D("12")),
                 "NOK", "Org.nr MVA", r"^(NO)?\d{9}\s?MVA$"),
)

# --------------------------------------------------------------------------- #
# Americas
# --------------------------------------------------------------------------- #
_AMERICAS: tuple[Jurisdiction, ...] = (
    # US: sales tax, NOT reverse charge. See the module docstring.
    Jurisdiction("US-CA", "United States — California", TaxSystem.SALES_TAX,
                 "Sales Tax", D("7.25"), (), "USD", "Seller's Permit / EIN",
                 r"^\d{2}-?\d{7}$", reverse_charge=False,
                 caveat=("State rate only. Average local surtax adds ~1.78%, "
                         "combined ~9.03%. There is NO reverse charge in US "
                         "sales tax — the buyer self-assesses use tax instead.")),
    Jurisdiction("US-NY", "United States — New York", TaxSystem.SALES_TAX,
                 "Sales Tax", D("4.00"), (), "USD", "Certificate of Authority / EIN",
                 r"^\d{2}-?\d{7}$", reverse_charge=False,
                 caveat="State rate only; combined average ~8.54%."),
    Jurisdiction("US-TX", "United States — Texas", TaxSystem.SALES_TAX,
                 "Sales Tax", D("6.25"), (), "USD", "Sales & Use Tax Permit",
                 None, reverse_charge=False, verified=False,
                 caveat="State rate verified; the 11-digit permit format is not."),
    Jurisdiction("US-OR", "United States — Oregon", TaxSystem.NONE,
                 "Sales Tax", D("0"), (), "USD", "EIN", r"^\d{2}-?\d{7}$",
                 reverse_charge=False,
                 caveat=("No state sales tax, and local sales taxes are not "
                         "permitted. The cleanest zero-tax case available.")),
    Jurisdiction("CA-ON", "Canada — Ontario", TaxSystem.GST, "HST", D("13"), (),
                 "CAD", "Business Number", r"^\d{9}RT\d{4}$",
                 caveat="GST/HST registration number required on invoices over C$30."),
    Jurisdiction("CA-NS", "Canada — Nova Scotia", TaxSystem.GST, "HST", D("14"), (),
                 "CAD", "Business Number", r"^\d{9}RT\d{4}$",
                 caveat=("CUT FROM 15% ON 1 APR 2025. This is one of the two "
                         "figures most likely to be stale in inherited data.")),
    Jurisdiction("CA-BC", "Canada — British Columbia", TaxSystem.GST,
                 "GST + PST", D("12"), (D("5"),), "CAD", "Business Number",
                 r"^\d{9}RT\d{4}$",
                 caveat=("5% federal GST plus 7% provincial PST, which are "
                         "separate taxes with separate registrations. PST is "
                         "not recoverable. Emit as TWO TaxDetail elements.")),
    Jurisdiction("MX", "Mexico", TaxSystem.VAT, "IVA", D("16"), (D("8"), D("0")),
                 "MXN", "RFC", r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$", verified=False,
                 caveat=("Invoices must be CFDI 4.0 XML stamped by a "
                         "SAT-authorised PAC with a UUID — a cXML invoice alone "
                         "is not a valid Mexican invoice. The 8% border-zone "
                         "regime's continuation into 2026 is unverified.")),
    Jurisdiction("BR", "Brazil", TaxSystem.VAT, "CBS + IBS", D("1.0"), (), "BRL",
                 "CNPJ", r"^[A-Z0-9]{12}\d{2}$", verified=False,
                 caveat=("2026 is a PARALLEL-RUNNING TEST YEAR. Legacy ICMS, "
                         "IPI, PIS, COFINS and ISS are still collected, plus "
                         "CBS 0.9% + IBS 0.1% which must be DISPLAYED but are "
                         "NOT collected. A realistic Brazilian invoice needs "
                         "seven tax lines, two informational — the best stress "
                         "test available for repeated TaxDetail. All legacy "
                         "rates are placeholders. Note CNPJ went ALPHANUMERIC "
                         "on 1 Jul 2026, so numeric-only validation is already "
                         "broken for new registrations.")),
)

# --------------------------------------------------------------------------- #
# APAC, Middle East, Africa
# --------------------------------------------------------------------------- #
_REST: tuple[Jurisdiction, ...] = (
    Jurisdiction("AU", "Australia", TaxSystem.GST, "GST", D("10"), (D("0"),),
                 "AUD", "ABN", r"^\d{11}$"),
    Jurisdiction("NZ", "New Zealand", TaxSystem.GST, "GST", D("15"), (D("0"),),
                 "NZD", "IRD number", r"^\d{8,9}$"),
    Jurisdiction("JP", "Japan", TaxSystem.GST, "Consumption Tax", D("10"), (D("8"),),
                 "JPY", "Qualified Invoice Issuer Registration No.", r"^T\d{13}$",
                 caveat=("Transitional input-credit relief for unregistered "
                         "suppliers drops from 80% to 50% on 1 Oct 2026.")),
    Jurisdiction("IN", "India", TaxSystem.GST, "GST", D("18"),
                 (D("5"), D("0"), D("40")), "INR", "GSTIN",
                 r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
                 caveat=("GST 2.0 from 22 Sep 2025: the slabs are 0/5/18/40 and "
                         "the 12% and 28% slabs were ABOLISHED — the other "
                         "figure most likely stale in inherited data. "
                         "Intra-state supplies split into CGST+SGST at half the "
                         "headline each; inter-state and imports use IGST at "
                         "the full rate. Reverse charge applies to ALL imported "
                         "services with no threshold.")),
    Jurisdiction("AE", "United Arab Emirates", TaxSystem.VAT, "VAT", D("5"), (D("0"),),
                 "AED", "TRN", r"^\d{15}$", verified=False,
                 caveat="Rate inferred from absence of change, not a direct FTA citation."),
    Jurisdiction("SG", "Singapore", TaxSystem.GST, "GST", D("9"), (D("0"),),
                 "SGD", "GST registration number", None,
                 caveat=("Tax-ID format deliberately omitted: sources actively "
                         "conflict on whether GST registrants use their UEN, an "
                         "M-prefixed number, or an OVR-prefixed one. Verify "
                         "against IRAS before writing a pattern.")),
    Jurisdiction("ZA", "South Africa", TaxSystem.VAT, "VAT", D("15"), (D("0"),),
                 "ZAR", "VAT number", r"^4\d{9}$",
                 caveat=("15%, NOT 15.5% or 16%. Both announced rises were "
                         "WITHDRAWN on 24 Apr 2025 and reversed by statute. Any "
                         "source published Mar–Apr 2025 says otherwise and is "
                         "wrong.")),
)

JURISDICTIONS: dict[str, Jurisdiction] = {
    j.code: j for j in (*_EU_UK, *_AMERICAS, *_REST)
}

#: EU member states, for the intra-community / reverse-charge decision.
EU_MEMBERS: frozenset[str] = frozenset(
    j.code for j in JURISDICTIONS.values() if j.eu
)


def get(code: str) -> Jurisdiction:
    try:
        return JURISDICTIONS[code]
    except KeyError:
        raise KeyError(
            f"no tax data for {code!r}. Known: {', '.join(sorted(JURISDICTIONS))}"
        ) from None


def unverified() -> list[Jurisdiction]:
    """Jurisdictions whose standard rate was not confirmed against a cited
    2026 source. The UI shows this list rather than hiding it — see the module
    docstring on why silent wrongness is the thing to avoid."""
    return [j for j in JURISDICTIONS.values() if not j.verified]
