"""PunchOut Sandbox — the round-trip differ.

*This is the independent judge from BRIEF.md §2, in its concrete form. Read
`docs/reference/platform-conformance.md` §6a before changing anything here.*

=============================================================================
WHY THIS EXISTS RATHER THAN MORE BREAK SCENARIOS
=============================================================================
The obvious way to build a conformance harness is to inject deliberate errors
and see whether the buyer notices. That catches a real class of bug, and it
cannot catch the class that actually costs people money, because **the worst
documented failures are silent**:

- JAGGAER maps an unrecognised UOM to `EA` without complaint, so a line of
  `BX` (box of 100) becomes 100 individual items.
- JAGGAER discards the supplier's `<Total>` and recomputes it.
- Oracle Fusion defaults an unmapped classification, and silently treats an
  invalid `itemClassification` as Goods.
- Dell truncates ship-to fields at 30 characters.
- OCI truncates every description at 40.

Nothing errors in any of those. The document is accepted; the data is wrong.
No amount of error injection surfaces them, because there is no error.

What surfaces them is comparing the two halves of the round trip. The spec
even supplies the rule to enforce, verbatim:

    ItemDetail data (with the possible exception of Extrinsic elements)
    contained within ItemIn elements must not be removed when converting
    from ItemIn to ItemOut.

Every free tool in RESEARCH.md sees only ONE side of the exchange. Seeing both
is the entire reason this service can say something nobody else can.

=============================================================================
THE MATCHING PROBLEM — WHY IT IS THE HARD PART
=============================================================================
To diff two line items you must first decide which cart line corresponds to
which PO line. The obvious key is `SupplierPartID` + `SupplierPartAuxiliaryID`.

**That key is made of exactly the fields most likely to have been corrupted.**
A dropped or truncated aux ID is the single most common real failure, so
matching on it means the differ fails hardest precisely when it has the most
to say.

So `_match_lines` degrades through tiers, and **records which tier succeeded**,
because needing a weaker tier is itself a finding:

  1. `lineNumber` — the buyer's own correlation, when present on both sides.
  2. `SupplierPartID` + `SupplierPartAuxiliaryID`, exact.
  3. `SupplierPartID` + aux ID compared loosely (prefix / case / trim), which
     detects truncation while still pairing the lines.
  4. `SupplierPartID` alone — reported as AMBIGUOUS when it is not unique.

Tier 4 being non-unique is not a defect in the differ. It is the exact
situation the cXML spec invented `SupplierPartAuxiliaryID` for: *"a supplier
might use the same SupplierPartID for an item, but have a different price for
units of 'EA' and 'BOX'."* If we land there, the buyer has destroyed the only
thing distinguishing two legitimately different lines, and that deserves a
finding of its own rather than a silent guess.

=============================================================================
OUTCOMES, AND WHY "CORRUPTED" IS ITS OWN BUCKET
=============================================================================
Every compared field lands in exactly one bucket:

| Outcome | Meaning |
|---|---|
| `preserved` | round-tripped identically — correct |
| `corrupted` | came back **different** — the dangerous one |
| `dropped`   | sent, did not come back |
| `added`     | not sent, appeared on the PO (usually buyer enrichment) |
| `absent`    | not sent, not returned — nothing to say |

A conventional diff would merge `corrupted` and `dropped` into "changed".
Keeping them apart matters because they have different causes and different
fixes: dropped is usually an unmapped field in the buyer's model, corrupted is
usually a length limit or a normalisation rule. Telling a user which one they
are looking at is most of the value.

=============================================================================
DIAGNOSES — NAMING THE MECHANISM, NOT JUST THE DIFFERENCE
=============================================================================
"These two strings differ" is nearly worthless to someone debugging. "This
looks like a 100-character limit" is actionable. `_diagnose` therefore
pattern-matches the *shape* of each corruption against the mechanisms
documented in `docs/reference/`, and returns `None` rather than inventing a
mechanism it cannot see. An unexplained difference is reported as an
unexplained difference.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from .xml_safe import SafeDocument

# --------------------------------------------------------------------------- #
# Outcome and severity vocabularies
# --------------------------------------------------------------------------- #
PRESERVED = "preserved"
CORRUPTED = "corrupted"
DROPPED = "dropped"
ADDED = "added"
ABSENT = "absent"

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

# Which fields, when corrupted or dropped, actually break an integration.
# Sourced from docs/reference/platform-conformance.md — this is not a guess at
# what "feels important", it is what the vendor documentation says goes wrong.
_FIELD_SEVERITY: dict[str, str] = {
    # The spec states outright that this must not change across the round trip,
    # and Ariba composes item identity from it. Dell ships a permanent
    # per-platform workaround because Oracle would not round-trip it.
    "supplier_part_auxiliary_id": CRITICAL,
    "supplier_part_id": CRITICAL,
    # A silently defaulted UOM turns "1 box of 100" into "1 each" (or 100
    # each). This is the most financially damaging documented failure.
    "unit_of_measure": CRITICAL,
    "unit_price": CRITICAL,
    "currency": CRITICAL,
    "quantity": CRITICAL,
    # Wrong spend category: reporting and approval routing break, the order
    # does not.
    "classification": WARNING,
    "description": WARNING,
    "manufacturer_part_id": WARNING,
    "manufacturer_name": WARNING,
    "lead_time": INFO,
}


@dataclass(frozen=True)
class FieldDiff:
    field: str
    outcome: str
    sent: Optional[str]
    returned: Optional[str]
    severity: str
    diagnosis: Optional[str] = None   # stable slug naming the mechanism
    explanation: Optional[str] = None  # human sentence, safe to show a user


@dataclass
class LineDiff:
    cart_index: Optional[int]
    order_index: Optional[int]
    matched_by: Optional[str]          # which tier paired these lines
    ambiguous: bool = False
    fields: list[FieldDiff] = field(default_factory=list)

    @property
    def corrupted(self) -> list[FieldDiff]:
        return [f for f in self.fields if f.outcome == CORRUPTED]

    @property
    def dropped(self) -> list[FieldDiff]:
        return [f for f in self.fields if f.outcome == DROPPED]


@dataclass
class DiffReport:
    lines: list[LineDiff] = field(default_factory=list)
    header: list[FieldDiff] = field(default_factory=list)
    unmatched_cart: list[int] = field(default_factory=list)
    unmatched_order: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True only if nothing was corrupted, dropped or left unmatched.

        Deliberately strict: `added` fields do not spoil a clean verdict
        (buyers legitimately enrich a PO with accounting data), but anything
        the supplier sent and did not get back does."""
        if self.unmatched_cart or self.unmatched_order:
            return False
        for group in (self.header, *(line.fields for line in self.lines)):
            for f in group:
                if f.outcome in (CORRUPTED, DROPPED):
                    return False
        return True

    def by_severity(self, severity: str) -> list[FieldDiff]:
        out = [f for f in self.header if f.severity == severity and f.outcome in (CORRUPTED, DROPPED)]
        for line in self.lines:
            out.extend(
                f for f in line.fields
                if f.severity == severity and f.outcome in (CORRUPTED, DROPPED)
            )
        return out

    def as_dict(self) -> dict:
        return {
            "clean": self.clean,
            "criticalCount": len(self.by_severity(CRITICAL)),
            "warningCount": len(self.by_severity(WARNING)),
            "unmatchedCartLines": self.unmatched_cart,
            "unmatchedOrderLines": self.unmatched_order,
            "notes": self.notes,
            "header": [_field_dict(f) for f in self.header],
            "lines": [
                {
                    "cartIndex": line.cart_index,
                    "orderIndex": line.order_index,
                    "matchedBy": line.matched_by,
                    "ambiguous": line.ambiguous,
                    "fields": [_field_dict(f) for f in line.fields],
                }
                for line in self.lines
            ],
        }


def _field_dict(f: FieldDiff) -> dict:
    return {
        "field": f.field,
        "outcome": f.outcome,
        "sent": f.sent,
        "returned": f.returned,
        "severity": f.severity,
        "diagnosis": f.diagnosis,
        "explanation": f.explanation,
    }


# --------------------------------------------------------------------------- #
# Extraction — one canonical line shape from either document
# --------------------------------------------------------------------------- #
@dataclass
class Line:
    """A cart line or a PO line, reduced to the fields worth comparing.

    `ItemIn` (PunchOutOrderMessage) and `ItemOut` (OrderRequest) carry the same
    `ItemDetail` structure, which is exactly why the spec can demand they
    round-trip. One shape serves both."""

    index: int
    line_number: Optional[str] = None
    supplier_part_id: Optional[str] = None
    supplier_part_auxiliary_id: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    currency: Optional[str] = None
    unit_of_measure: Optional[str] = None
    description: Optional[str] = None
    classification: Optional[str] = None      # "DOMAIN:code" joined, sorted
    manufacturer_part_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    lead_time: Optional[str] = None


def _text(node) -> Optional[str]:
    """Full text of an element including descendants, whitespace-collapsed.

    `Description` may contain a `ShortName` child, so `.text` alone would
    silently drop half the content — and a differ that loses data while
    checking for data loss is worse than none."""
    if node is None:
        return None
    parts = node.itertext() if hasattr(node, "itertext") else [node.text or ""]
    joined = " ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


def _extract_line(item, index: int) -> Line:
    item_id = item.find("ItemID")
    detail = item.find("ItemDetail")
    money = item.find(".//UnitPrice/Money") if detail is not None else None

    classifications = []
    if detail is not None:
        for c in detail.findall("Classification"):
            domain = (c.get("domain") or "").strip()
            code = (_text(c) or c.get("code") or "").strip()
            if domain or code:
                classifications.append(f"{domain}:{code}")

    return Line(
        index=index,
        line_number=item.get("lineNumber"),
        supplier_part_id=_text(item_id.find("SupplierPartID")) if item_id is not None else None,
        supplier_part_auxiliary_id=(
            _text(item_id.find("SupplierPartAuxiliaryID")) if item_id is not None else None
        ),
        quantity=item.get("quantity"),
        unit_price=_text(money),
        currency=money.get("currency") if money is not None else None,
        unit_of_measure=_text(detail.find("UnitOfMeasure")) if detail is not None else None,
        description=_text(detail.find("Description")) if detail is not None else None,
        classification=";".join(sorted(classifications)) or None,
        manufacturer_part_id=_text(detail.find("ManufacturerPartID")) if detail is not None else None,
        manufacturer_name=_text(detail.find("ManufacturerName")) if detail is not None else None,
        lead_time=_text(detail.find("LeadTime")) if detail is not None else None,
    )


def extract_lines(doc: SafeDocument) -> list[Line]:
    """Pull comparable lines from a PunchOutOrderMessage or an OrderRequest.

    Searches for `ItemIn` then `ItemOut` rather than branching on document
    type, so a caller can hand over either without first classifying it —
    and so a document that somehow contains both is handled rather than
    half-read."""
    items = doc.tree.findall(".//ItemIn") + doc.tree.findall(".//ItemOut")
    return [_extract_line(item, i) for i, item in enumerate(items)]


def extract_total(doc: SafeDocument) -> tuple[Optional[str], Optional[str]]:
    money = doc.tree.find(".//Total/Money")
    if money is None:
        return None, None
    return (_text(money), money.get("currency"))


# --------------------------------------------------------------------------- #
# Diagnosis — naming the mechanism behind a corruption
# --------------------------------------------------------------------------- #
def _is_truncation(sent: str, returned: str) -> bool:
    return len(returned) < len(sent) and sent.startswith(returned)


def _has_lone_surrogate_or_replacement(value: str) -> bool:
    """A returned string containing U+FFFD, or ending mid-combining-sequence,
    is the signature of a byte-level truncation through a multi-byte
    character — the failure mode the Ariba byte-limit docs warn about."""
    if "�" in value:
        return True
    return bool(value) and unicodedata.combining(value[-1]) != 0


# Fields whose values are numbers. These MUST be diagnosed numerically before
# any string test runs, because decimal strings are full of accidental prefix
# relationships: "10.2345" starts with "10.23", and "10.00" starts with "10.0".
# A string-first order reports both as truncation, which is wrong twice over —
# the first is rounding and the second is not a change at all.
_NUMERIC_FIELDS = frozenset({"unit_price", "quantity", "lead_time", "total"})


def _strip_token_zeros(value: str) -> str:
    """Strip leading zeros from each token of a composite value.

    `classification` is carried as `DOMAIN:code` (joined with `;` when there
    are several), so a naive `lstrip("0")` on the whole string sees `UNSPSC:…`
    and finds nothing to strip — silently missing the leading-zero loss it was
    written to catch. UNSPSC codes carry significant leading zeros, so this is
    exactly the field where it matters most."""
    return ";".join(
        ":".join(part.lstrip("0") or "0" for part in token.split(":"))
        for token in value.split(";")
    )


def _diagnose(field_name: str, sent: str, returned: str) -> tuple[Optional[str], Optional[str]]:
    """Return `(slug, explanation)` naming the mechanism, or `(None, None)`.

    Order matters, and it is not the obvious order. Numeric fields are
    diagnosed numerically FIRST (see `_NUMERIC_FIELDS`); only then do the
    string-shape tests run, most-specific first. Reporting the generic answer
    when a specific one applies is the whole failure this function exists to
    avoid."""
    if field_name in _NUMERIC_FIELDS:
        try:
            a, b = Decimal(sent), Decimal(returned)
        except InvalidOperation:
            pass  # not actually numeric — fall through to the string tests
        else:
            if a == b:
                return (
                    "numeric-reformatted",
                    "Numerically identical, differently formatted (trailing zeros "
                    "or decimal places). Harmless in itself, but it means the "
                    "buyer is re-serialising rather than echoing.",
                )
            if a != 0 and abs(a - b) / abs(a) < Decimal("0.005"):
                return (
                    "rounded",
                    "The value moved by less than half a percent — rounding. "
                    "JAGGAER accepts 4 decimal places and rounds beyond that, and "
                    "multiplies before rounding, so a supplier who rounds per unit "
                    "first will diverge.",
                )
            return (
                "value-changed",
                "The value changed by more than rounding can explain.",
            )

    if _has_lone_surrogate_or_replacement(returned):
        return (
            "truncated-mid-character",
            "The returned value was cut through a multi-byte character. Buyer "
            "field limits are often counted in BYTES, not characters — Ariba "
            "documents exactly this, where a 2000-byte limit allows only ~666 "
            "CJK characters.",
        )
    if _is_truncation(sent, returned):
        return (
            "truncated",
            f"The value came back cut to {len(returned)} characters. This is a "
            "buyer field-length limit, and it is silent — documented limits "
            "include 100 (JAGGAER part IDs), 255 (Ariba aux ID), 256 "
            "(JAGGAER description), 40 (OCI description), 30 (Dell ship-to).",
        )
    if sent.strip() == returned.strip() and sent != returned:
        return (
            "whitespace-trimmed",
            "Only surrounding whitespace changed. Usually harmless — but "
            "Amazon's own sample aux ID contains a trailing space and must be "
            "returned verbatim, so over-eager trimming can break the match.",
        )
    if sent.lower() == returned.lower():
        return (
            "case-folded",
            "Case changed. Ariba treats part IDs as case-insensitive, but "
            "JAGGAER's invoice matching is case-SENSITIVE — so a case change "
            "here can surface much later as an unmatchable invoice.",
        )
    if _strip_token_zeros(sent) == _strip_token_zeros(returned) and sent != returned:
        return (
            "leading-zeros-stripped",
            "Leading zeros were lost, the classic signature of a value being "
            "cast through an integer. UNSPSC codes and DUNS numbers both carry "
            "significant leading zeros.",
        )
    stripped = re.sub(r"[-./\s]", "", sent)
    if stripped == re.sub(r"[-./\s]", "", returned) and sent != returned:
        return (
            "delimiters-changed",
            "Only punctuation differs. JAGGAER can be configured to strip "
            "delimiters on the PO while still matching invoices WITH them — "
            "enabling one without the other guarantees invoice rejection.",
        )
    if field_name == "unit_of_measure" and returned.upper() == "EA" and sent.upper() != "EA":
        return (
            "uom-defaulted-to-EA",
            "The unit of measure was replaced with EA. JAGGAER documents "
            "silently defaulting any unrecognised UOM to EA — so a line of "
            "BX (box of 100) becomes 100 individual items, with no error "
            "anywhere. This is the most financially damaging documented "
            "failure in punchout.",
        )
    return (None, None)


def _compare(field_name: str, sent: Optional[str], returned: Optional[str]) -> FieldDiff:
    severity = _FIELD_SEVERITY.get(field_name, INFO)
    if sent is None and returned is None:
        return FieldDiff(field_name, ABSENT, None, None, INFO)
    if sent is None:
        return FieldDiff(
            field_name, ADDED, None, returned, INFO,
            explanation="Not sent by the supplier; added by the buyer. Usually enrichment.",
        )
    if returned is None:
        return FieldDiff(
            field_name, DROPPED, sent, None, severity,
            explanation=(
                "Sent by the supplier and absent from the purchase order. "
                "The cXML spec is explicit that ItemDetail data must not be "
                "removed when converting ItemIn to ItemOut."
            ),
        )
    if sent == returned:
        return FieldDiff(field_name, PRESERVED, sent, returned, INFO)
    slug, explanation = _diagnose(field_name, sent, returned)
    # A purely cosmetic numeric reformat is not corruption. Downgrading it
    # keeps the critical list trustworthy — a report that cries wolf about
    # "10.00" versus "10.0" trains people to skim past the real findings.
    if slug == "numeric-reformatted":
        return FieldDiff(field_name, PRESERVED, sent, returned, INFO, slug, explanation)
    return FieldDiff(
        field_name, CORRUPTED, sent, returned, severity, slug,
        explanation or "The value changed, and the mechanism is not recognisable.",
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _loose(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _match_lines(cart: list[Line], order: list[Line]) -> tuple[list[tuple[Optional[Line], Optional[Line], Optional[str], bool]], list[int], list[int]]:
    """Pair cart lines with PO lines. See the module docstring for why this
    degrades through tiers and records which one succeeded."""
    pairs: list[tuple[Optional[Line], Optional[Line], Optional[str], bool]] = []
    remaining = list(order)

    def take(predicate):
        for candidate in remaining:
            if predicate(candidate):
                remaining.remove(candidate)
                return candidate
        return None

    for line in cart:
        matched = None
        tier = None
        ambiguous = False

        # Tier 1 — the buyer's own line number, when both carry one.
        if line.line_number:
            matched = take(lambda c: c.line_number == line.line_number)
            tier = "lineNumber" if matched is not None else None

        # Tier 2 — part ID plus aux ID, exact.
        if matched is None and line.supplier_part_id:
            matched = take(
                lambda c: c.supplier_part_id == line.supplier_part_id
                and c.supplier_part_auxiliary_id == line.supplier_part_auxiliary_id
            )
            tier = "partID+auxID" if matched is not None else None

        # Tier 3 — part ID plus a LOOSE aux ID comparison. This is what pairs
        # lines whose aux ID was truncated or case-folded, so the field diff
        # can then report the truncation instead of the pairing simply failing.
        if matched is None and line.supplier_part_id:
            sent_aux = _loose(line.supplier_part_auxiliary_id)
            matched = take(
                lambda c: c.supplier_part_id == line.supplier_part_id
                and sent_aux
                and (_loose(c.supplier_part_auxiliary_id)
                     and sent_aux.startswith(_loose(c.supplier_part_auxiliary_id)))
            )
            tier = "partID+auxID(loose)" if matched is not None else None

        # Tier 4 — part ID alone. Ambiguous when the cart itself contains more
        # than one line with this part ID, which is precisely the case the
        # spec invented the aux ID to disambiguate.
        if matched is None and line.supplier_part_id:
            same_part = [l for l in cart if l.supplier_part_id == line.supplier_part_id]
            matched = take(lambda c: c.supplier_part_id == line.supplier_part_id)
            if matched is not None:
                tier = "partID"
                ambiguous = len(same_part) > 1

        if matched is None:
            pairs.append((line, None, None, False))
        else:
            pairs.append((line, matched, tier, ambiguous))

    unmatched_cart = [p[0].index for p in pairs if p[1] is None and p[0] is not None]
    unmatched_order = [l.index for l in remaining]
    return pairs, unmatched_cart, unmatched_order


_COMPARED_FIELDS = (
    "supplier_part_id",
    "supplier_part_auxiliary_id",
    "quantity",
    "unit_price",
    "currency",
    "unit_of_measure",
    "description",
    "classification",
    "manufacturer_part_id",
    "manufacturer_name",
    "lead_time",
)


def diff(cart_doc: SafeDocument, order_doc: SafeDocument) -> DiffReport:
    """Diff a returned cart against the purchase order the buyer produced
    from it.

    `cart_doc` is the `PunchOutOrderMessage` the supplier sent; `order_doc` is
    the `OrderRequest` that came back. Neither is trusted — both have already
    been through `xml_safe.parse`."""
    report = DiffReport()
    cart = extract_lines(cart_doc)
    order = extract_lines(order_doc)

    if not cart:
        report.notes.append("The cart document contains no line items — nothing to compare.")
        return report

    pairs, unmatched_cart, unmatched_order = _match_lines(cart, order)
    report.unmatched_cart = unmatched_cart
    report.unmatched_order = unmatched_order

    for cart_line, order_line, tier, ambiguous in pairs:
        if order_line is None:
            report.lines.append(
                LineDiff(cart_index=cart_line.index, order_index=None, matched_by=None)
            )
            continue
        line_diff = LineDiff(
            cart_index=cart_line.index,
            order_index=order_line.index,
            matched_by=tier,
            ambiguous=ambiguous,
        )
        for name in _COMPARED_FIELDS:
            line_diff.fields.append(
                _compare(name, getattr(cart_line, name), getattr(order_line, name))
            )
        report.lines.append(line_diff)

    # Header total. Compared separately because the spec defines it as
    # EXCLUDING tax and shipping, so a mismatch here has a legitimate
    # explanation that a line-level mismatch does not.
    cart_total, cart_ccy = extract_total(cart_doc)
    order_total, order_ccy = extract_total(order_doc)
    if cart_total is not None or order_total is not None:
        total_diff = _compare("unit_price", cart_total, order_total)
        report.header.append(
            FieldDiff(
                "total", total_diff.outcome, cart_total, order_total,
                CRITICAL if total_diff.outcome == CORRUPTED else INFO,
                total_diff.diagnosis,
                (
                    "The header Total changed. JAGGAER documents discarding the "
                    "supplier's Total entirely and recomputing it from unit "
                    "price x quantity — so a difference here may mean the buyer "
                    "never read your total at all. Note the spec defines Total "
                    "as EXCLUDING tax and shipping."
                ) if total_diff.outcome == CORRUPTED else total_diff.explanation,
            )
        )
        report.header.append(_compare("currency", cart_ccy, order_ccy))

    _add_notes(report)
    return report


def _add_notes(report: DiffReport) -> None:
    """Observations about the diff as a whole, rather than any one field."""
    weak = [l for l in report.lines if l.matched_by == "partID"]
    if weak:
        report.notes.append(
            f"{len(weak)} line(s) could only be matched on SupplierPartID alone, "
            "because the auxiliary ID did not survive the round trip."
        )
    if any(l.ambiguous for l in report.lines):
        report.notes.append(
            "At least one match was AMBIGUOUS: the cart contained several lines "
            "sharing a SupplierPartID, distinguished only by their auxiliary ID "
            "— which the buyer did not return. This is the exact case the cXML "
            "spec introduced SupplierPartAuxiliaryID to solve (same part, "
            "different price for EA vs BOX), so the pairing below is a guess."
        )
    loose = [l for l in report.lines if l.matched_by == "partID+auxID(loose)"]
    if loose:
        report.notes.append(
            f"{len(loose)} line(s) matched only after relaxing the auxiliary ID "
            "comparison, which means the ID came back altered rather than intact."
        )
    if report.unmatched_order:
        report.notes.append(
            f"{len(report.unmatched_order)} purchase-order line(s) correspond to "
            "nothing in the cart. A buyer adding lines to a punchout requisition "
            "is legitimate, but it is worth confirming it was deliberate."
        )
