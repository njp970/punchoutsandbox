"""PunchOut Sandbox — the independent judge.

*This module is the product. Everything else here is a plausible-looking shop
that exists so that this module has something to judge.*

=============================================================================
WHY THIS EXISTS AT ALL
=============================================================================
BRIEF.md §2 states the problem it solves, and it is worth restating because
every design decision below follows from it:

    A buyer-side integration can only be tested against itself. Xenia's own
    cXML tests round-trip build.py -> extract.py, which proves the two halves
    agree with each other — NOT that either conforms to the spec. If both
    share a misreading, every test passes and the first real supplier rejects
    every document.

The missing ingredient is an judge with no stake in the buyer's
interpretation. That is what a DTD is: a description of the format written by
someone who has never seen your code. RESEARCH.md §A confirmed no vendor or
network anywhere issues one — Ariba, Coupa, Jaggaer, TradeCentric, Greenwing
all do bilateral, buyer-coordinated sign-off instead.

=============================================================================
TWO KINDS OF FINDING, NEVER CONFLATED
=============================================================================
`ConformanceReport` separates them, and the separation is the honest part:

**ERRORS** come from the DTD. They are mechanical and not a matter of
opinion — the document either matches the grammar or it does not, and we can
point at the rule. If we report an error, the user's document is wrong.

**ADVISORIES** come from `_ADVISORY_CHECKS` below: things a DTD cannot
express, which nonetheless break real integrations (RESEARCH.md's list of
awkward cases — dropped SupplierPartAuxiliaryID, currency mismatch, rounding
disagreement, UOM variants). These are judgement, and are labelled as such.
An advisory is us saying "this is legal cXML and it will still cause you
trouble on Tuesday".

Never promote an advisory to an error to make a report look more decisive.
The value of this tool is that its errors are trustworthy; the moment we
start calling opinions errors, they are not.

=============================================================================
THE DECLARED DTD IS AN OBSERVATION, NOT AN INSTRUCTION
=============================================================================
Documents declare their DTD in the DOCTYPE. We do not fetch it — see
`xml_safe.py`'s docstring on why a validation service fetching attacker-chosen
URLs is server-side request forgery with extra steps. We validate against the
LOCAL vendored copy chosen by document type, and report the declared version
separately so a user can see they are writing 1.2.014 against our 1.2.071.

A version mismatch is an ADVISORY, not an error. cXML is heavily
backward-compatible and buyers legitimately pin old versions; telling someone
their perfectly functional 1.2.014 document is "invalid" because we hold a
newer DTD would be exactly the kind of false authority that makes a
conformance tool worthless.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from lxml import etree

from .xml_safe import SafeDocument

_DTD_DIR = os.path.join(os.path.dirname(__file__), "cxml", "dtd")

# Which vendored DTD validates which document type. The modules are
# self-contained rather than layered (see app/cxml/dtd/README.md) — each
# embeds the whole common definition — so exactly one applies per document,
# and picking the wrong one produces confident nonsense.
_DTD_FOR_DOCUMENT: dict[str, str] = {
    "PunchOutSetupRequest": "cXML.dtd",
    "PunchOutSetupResponse": "cXML.dtd",
    "PunchOutOrderMessage": "cXML.dtd",
    "OrderRequest": "cXML.dtd",
    "OrderResponse": "cXML.dtd",
    "ConfirmationRequest": "Fulfill.dtd",
    "ShipNoticeRequest": "Fulfill.dtd",
    "InvoiceDetailRequest": "InvoiceDetail.dtd",
    "CatalogUploadRequest": "Catalog.dtd",
    "QuoteRequest": "Quote.dtd",
    "QuoteMessage": "Quote.dtd",
    "PaymentRemittanceRequest": "PaymentRemittance.dtd",
    "StatusUpdateRequest": "cXML.dtd",
}

# Parsing a 400KB DTD costs real milliseconds. Cached at module scope so a
# warm Lambda pays once per DTD for the life of the execution environment,
# and a cold one pays only for the DTDs it actually touches — loading all
# nine eagerly at import would add roughly a second to every cold start to
# prepare eight validators the request will not use.
_dtd_cache: dict[str, etree.DTD] = {}


def _load_dtd(filename: str) -> etree.DTD:
    if filename not in _dtd_cache:
        path = os.path.join(_DTD_DIR, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"vendored DTD {filename} is missing from {_DTD_DIR} — "
                "run scripts/fetch_dtds.sh"
            )
        with open(path, "rb") as handle:
            _dtd_cache[filename] = etree.DTD(handle)
    return _dtd_cache[filename]


@dataclass(frozen=True)
class Finding:
    """One thing we noticed. `line` is 1-indexed into the submitted document
    and may be None for findings that are about the document as a whole."""

    severity: str          # "error" | "advisory"
    code: str              # stable slug, e.g. "dtd-invalid", "aux-id-dropped"
    message: str
    line: Optional[int] = None
    element: Optional[str] = None
    hint: Optional[str] = None   # what to actually do about it


@dataclass
class ConformanceReport:
    document_type: Optional[str]
    dtd_used: Optional[str]
    declared_dtd: Optional[str]
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def advisories(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "advisory"]

    @property
    def conformant(self) -> bool:
        """Conformance is about ERRORS only. A document with fifteen
        advisories and no errors is conformant cXML, and saying otherwise
        would be the false authority the module docstring warns about."""
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "documentType": self.document_type,
            "dtdUsed": self.dtd_used,
            "declaredDtd": self.declared_dtd,
            "conformant": self.conformant,
            "errorCount": len(self.errors),
            "advisoryCount": len(self.advisories),
            "findings": [
                {
                    "severity": f.severity,
                    "code": f.code,
                    "message": f.message,
                    "line": f.line,
                    "element": f.element,
                    "hint": f.hint,
                }
                for f in self.findings
            ],
        }


def detect_document_type(doc: SafeDocument) -> Optional[str]:
    """cXML wraps every payload in `<cXML><Request|Response|Message>` and the
    real document type is the first element inside that wrapper. Returns None
    for anything that is not recognisably cXML, which the caller reports as a
    finding rather than treating as an exception — "this isn't cXML at all" is
    a legitimate thing for a user to submit by accident and deserves a
    readable answer, not a stack trace."""
    root = doc.element
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag != "cXML":
        return None
    for wrapper in root:
        wtag = wrapper.tag.split("}")[-1] if "}" in wrapper.tag else wrapper.tag
        if wtag in ("Request", "Response", "Message"):
            for child in wrapper:
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                return ctag
    return None


def validate(doc: SafeDocument, *, expected_type: Optional[str] = None) -> ConformanceReport:
    """Judge one already-parsed, already-vetted document.

    `expected_type` lets a caller say "I asked for a PunchOutOrderMessage" so
    that receiving a well-formed, perfectly valid OrderRequest instead is
    reported as the mismatch it is, rather than passing silently."""
    doc_type = detect_document_type(doc)
    report = ConformanceReport(
        document_type=doc_type,
        dtd_used=None,
        declared_dtd=doc.declared_dtd,
    )

    if doc_type is None:
        report.findings.append(
            Finding(
                severity="error",
                code="not-cxml",
                message=(
                    "The document does not look like cXML: expected a <cXML> root "
                    "containing <Request>, <Response> or <Message>."
                ),
                hint="Check you are posting the cXML envelope and not just the payload element.",
            )
        )
        return report

    if expected_type and doc_type != expected_type:
        report.findings.append(
            Finding(
                severity="error",
                code="unexpected-document-type",
                message=f"Expected a {expected_type} but this is a {doc_type}.",
                element=doc_type,
                hint=f"This endpoint only accepts {expected_type}.",
            )
        )

    dtd_name = _DTD_FOR_DOCUMENT.get(doc_type)
    if dtd_name is None:
        report.findings.append(
            Finding(
                severity="advisory",
                code="unknown-document-type",
                message=(
                    f"{doc_type} is a cXML document type this sandbox does not "
                    "have a DTD mapping for, so it was not validated."
                ),
                element=doc_type,
                hint="Structural validation was skipped — treat a pass here as meaning nothing.",
            )
        )
        return report

    report.dtd_used = dtd_name
    dtd = _load_dtd(dtd_name)

    if not dtd.validate(doc.tree):
        for entry in dtd.error_log:
            report.findings.append(
                Finding(
                    severity="error",
                    code="dtd-invalid",
                    message=_humanise_dtd_error(entry.message),
                    line=entry.line or None,
                    hint=_hint_for_dtd_error(entry.message),
                )
            )

    _check_declared_version(doc, report)
    for check in _ADVISORY_CHECKS:
        check(doc, report)

    return report


# =========================================================================== #
# Making libxml2's errors readable
# =========================================================================== #
# libxml2 phrases DTD errors for people who already know DTDs. The users of
# this sandbox are, by definition, people who are still learning the format —
# so the raw text is rewritten where the rewrite is unambiguous, and left
# alone where it is not. The original is never discarded silently: anything
# not matched here passes through verbatim rather than being approximated.
_ERROR_REWRITES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"No declaration for element (\S+)"),
        r"<\1> is not a valid element here — the DTD has no declaration for it.",
    ),
    (
        re.compile(r"Element (\S+) content does not follow the DTD, expecting \((.+?)\), got \((.*?)\)"),
        r"<\1> has the wrong children. Expected: \2. Found: \3.",
    ),
    (
        re.compile(r"Element (\S+) does not carry attribute (\S+)"),
        r"<\1> is missing the required attribute '\2'.",
    ),
    (
        re.compile(r"No declaration for attribute (\S+) of element (\S+)"),
        r"<\2> has an attribute '\1' that the DTD does not define.",
    ),
    (
        re.compile(r"Value \"(.*?)\" for attribute (\S+) of (\S+) is not among the enumerated set"),
        r"'\1' is not a permitted value for \3's '\2' attribute.",
    ),
]


def _humanise_dtd_error(message: str) -> str:
    text = message.strip()
    for pattern, replacement in _ERROR_REWRITES:
        if pattern.search(text):
            return pattern.sub(replacement, text)
    return text


def _hint_for_dtd_error(message: str) -> Optional[str]:
    """Actionable next step where the error implies one. Returns None rather
    than inventing generic advice — "check the DTD" helps nobody and clutters
    a report whose value is that everything in it is worth reading."""
    if "content does not follow the DTD" in message:
        return (
            "cXML child elements are ORDER-SENSITIVE. This is the single most "
            "common cause of a rejected document, and reordering to match the "
            "expected sequence usually fixes it outright."
        )
    if "does not carry attribute" in message:
        return "Add the attribute. Most cXML required attributes have no default."
    if "not among the enumerated set" in message:
        return "The DTD fixes this attribute to a closed list of values; check spelling and case."
    return None


# =========================================================================== #
# Advisory checks — things a DTD cannot express
# =========================================================================== #
def _check_declared_version(doc: SafeDocument, report: ConformanceReport) -> None:
    """Compare the DOCTYPE's declared version against the DTD we validated
    with. Advisory only — see the module docstring."""
    if not doc.declared_dtd:
        report.findings.append(
            Finding(
                severity="advisory",
                code="no-doctype",
                message="The document has no DOCTYPE declaration.",
                hint=(
                    "Valid, and many parsers accept it — but some buyer systems "
                    "reject a cXML document with no DOCTYPE outright."
                ),
            )
        )
        return
    match = re.search(r"/cXML/(\d+\.\d+\.\d+)/", doc.declared_dtd)
    if not match:
        return
    declared = match.group(1)
    if declared != _VENDORED_VERSION:
        report.findings.append(
            Finding(
                severity="advisory",
                code="dtd-version-mismatch",
                message=(
                    f"The document declares cXML {declared}; it was validated "
                    f"against {_VENDORED_VERSION}."
                ),
                hint=(
                    "cXML is strongly backward-compatible, so this is usually "
                    "fine. It matters if you are relying on an element added "
                    "after your declared version."
                ),
            )
        )


_VENDORED_VERSION = "1.2.071"


def _check_aux_id_present(doc: SafeDocument, report: ConformanceReport) -> None:
    """`SupplierPartAuxiliaryID` is optional in the DTD and load-bearing in
    practice: it is how a supplier ties a returned line back to a contract,
    a configuration, or a specific price. RESEARCH.md lists dropping it as a
    top cause of a PO being rejected or silently re-priced, and Xenia's own
    `cxml/build.py` carries a comment about exactly this.

    Reported per document rather than per line — a cart where SOME lines
    carry it and others do not is the interesting signal, and fifty identical
    findings would bury it."""
    items = doc.tree.findall(".//ItemID")
    if not items:
        return
    without = [i for i in items if i.find("SupplierPartAuxiliaryID") is None]
    if not without:
        return
    if len(without) == len(items):
        report.findings.append(
            Finding(
                severity="advisory",
                code="aux-id-absent",
                message=(
                    f"No <SupplierPartAuxiliaryID> on any of the {len(items)} line "
                    "items."
                ),
                element="ItemID",
                hint=(
                    "Legal cXML. But if a supplier sent you one at punchout and "
                    "your PO omits it, expect the PO to be rejected or re-priced. "
                    "Echo it back verbatim."
                ),
            )
        )
    else:
        report.findings.append(
            Finding(
                severity="advisory",
                code="aux-id-inconsistent",
                message=(
                    f"{len(without)} of {len(items)} line items are missing "
                    "<SupplierPartAuxiliaryID> while the others carry it."
                ),
                element="ItemID",
                hint=(
                    "Inconsistency within one document usually means the field is "
                    "being dropped somewhere in your mapping rather than genuinely "
                    "absent upstream."
                ),
            )
        )


def _check_currency_consistency(doc: SafeDocument, report: ConformanceReport) -> None:
    """Every `<Money>` in one document should carry the same currency.
    The DTD requires the attribute but says nothing about agreement, and a
    document mixing GBP lines with a EUR total is both perfectly valid and
    completely wrong."""
    currencies = {
        m.get("currency") for m in doc.tree.findall(".//Money") if m.get("currency")
    }
    if len(currencies) > 1:
        report.findings.append(
            Finding(
                severity="advisory",
                code="mixed-currency",
                message=f"This document mixes {len(currencies)} currencies: {', '.join(sorted(currencies))}.",
                element="Money",
                hint=(
                    "Valid cXML, and almost always a mapping bug. Most buyer "
                    "systems assume one currency per document and will convert "
                    "or reject unpredictably."
                ),
            )
        )


def _check_total_arithmetic(doc: SafeDocument, report: ConformanceReport) -> None:
    """Does the header Total actually equal the sum of the lines?

    A DTD cannot check arithmetic, and rounding disagreement between a buyer
    and a supplier is one of the failure modes RESEARCH.md flags. We compare
    with `Decimal` — never float, which cannot represent 0.1 and would make us
    report rounding errors we invented ourselves."""
    total_el = doc.tree.find(".//Total/Money")
    if total_el is None or not (total_el.text or "").strip():
        return
    try:
        stated = Decimal((total_el.text or "").strip())
    except InvalidOperation:
        return

    computed = Decimal("0")
    saw_line = False
    for item in doc.tree.findall(".//ItemIn") + doc.tree.findall(".//ItemOut"):
        qty_raw = item.get("quantity")
        price_el = item.find(".//UnitPrice/Money")
        if qty_raw is None or price_el is None or not (price_el.text or "").strip():
            continue
        try:
            computed += Decimal(qty_raw) * Decimal((price_el.text or "").strip())
            saw_line = True
        except InvalidOperation:
            return
    if not saw_line:
        return

    difference = abs(computed - stated)
    # A penny either way is rounding; more than that is a disagreement worth
    # naming. Deliberately not zero-tolerance — legitimate documents round
    # per-line and the sum genuinely lands a fraction out.
    if difference > Decimal("0.01"):
        report.findings.append(
            Finding(
                severity="advisory",
                code="total-mismatch",
                message=(
                    f"Header <Total> is {stated}, but quantity x unit price across "
                    f"the line items sums to {computed} (a difference of {difference})."
                ),
                element="Total",
                hint=(
                    "This ignores tax and shipping, so a difference is expected if "
                    "those are carried at header level. If they are not, one side "
                    "of your calculation is wrong."
                ),
            )
        )


def _check_uom_variants(doc: SafeDocument, report: ConformanceReport) -> None:
    """`EA` vs `PCE` vs `each` is the classic unit-of-measure disagreement.
    UnitOfMeasure is free text in the DTD, so anything parses; only some of it
    is understood by the system at the other end."""
    seen = {
        (u.text or "").strip()
        for u in doc.tree.findall(".//UnitOfMeasure")
        if (u.text or "").strip()
    }
    suspicious = {u for u in seen if u.lower() in {"each", "ea.", "pc", "pcs", "piece", "unit", "units"}}
    if suspicious:
        report.findings.append(
            Finding(
                severity="advisory",
                code="uom-non-standard",
                message=(
                    "Non-standard unit-of-measure code(s): "
                    + ", ".join(sorted(repr(u) for u in suspicious))
                ),
                element="UnitOfMeasure",
                hint=(
                    "cXML expects UN/CEFACT Recommendation 20 codes — 'EA' for each, "
                    "'PCE' for piece. Free text here is a common source of silent "
                    "quantity errors downstream."
                ),
            )
        )


_ADVISORY_CHECKS = (
    _check_aux_id_present,
    _check_currency_consistency,
    _check_total_arithmetic,
    _check_uom_variants,
)
