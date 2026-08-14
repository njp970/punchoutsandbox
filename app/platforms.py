"""Platform ingestion profiles — what a real buyer system does to your cart.

*Source: `docs/reference/platform-conformance.md`, published at
`/reference/platform-conformance`. Every rule here traces to a line in that
file; nothing is invented, and anything not established is marked
`verified=False` and says so on the page.*

=============================================================================
THE DIRECTION THAT MATTERS
=============================================================================
A punchout catalogue sends a cart. The buyer platform ingests it. Between
those two events the platform silently truncates, strips, rounds and defaults
according to rules that **none of them publish**, and the supplier finds out
weeks later when a purchase order arrives with the wrong price on it.

`validation.py` answers "is this document conformant". This module answers a
different and more useful question: **"my document is perfectly valid — what
will actually survive?"** They are not the same question, and the gap between
them is where real integrations break.

=============================================================================
THE FOUR OUTCOMES, AND WHY ONLY ONE OF THEM IS FRIGHTENING
=============================================================================
Taken from `platform-conformance.md` §6a:

    reject loudly       the buyer refused and said why           — good
    reject vaguely      refused with an unhelpful message        — poor, safe
    accept and corrupt  took it and changed it, silently         — DANGEROUS
    accept and preserve nothing happened                         — correct

The third is the entire reason this module exists. A rejection is a bad
afternoon; a silent corruption is a wrong price on a purchase order that
nobody notices until reconciliation.

=============================================================================
WHY THIS REUSES THE DIFFER RATHER THAN REPORTING FOR ITSELF
=============================================================================
Applying a profile produces a second set of lines: what the platform would
hold after ingestion. Comparing those to what you sent is exactly what
`differ.py` already does, including naming the mechanism — `truncated`,
`rounded`, `uom-defaulted-to-EA`, `leading-zeros-stripped`. Re-implementing
that diagnosis here would mean two things that must agree and eventually
would not.

So: apply the profile, then hand both line lists to `differ.diff_lines`.

=============================================================================
HONESTY ABOUT THE LIMITS THEMSELVES
=============================================================================
These numbers come from vendor documentation, support notes and observed
behaviour, gathered because nobody publishes a consolidated table. They are
the best available, not gospel. Each rule carries its source, `verified` is
False where the evidence is weaker than we would like, and the UI shows both.
The same posture as `tax/rates.py`: a confident wrong answer is worse than an
uncertain one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from .differ import DiffReport, Line, diff_lines

# The four outcomes, in increasing order of how much they should worry you.
REJECT_LOUD = "reject-loud"
REJECT_VAGUE = "reject-vague"
CORRUPT = "accept-and-corrupt"
PRESERVE = "accept-and-preserve"


@dataclass
class Effect:
    """One thing a platform did to one field of one line."""
    line_index: int
    field: str
    outcome: str
    detail: str
    #: False when the underlying rule is inferred rather than documented.
    verified: bool = True


@dataclass
class Ingestion:
    """The result of pushing a cart through a platform profile."""
    platform: str
    lines: list[Line] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    #: Set when the platform would refuse the cart outright. The lines are
    #: then what it WOULD have held, shown for context, but nothing arrives.
    rejected: bool = False
    report: Optional[DiffReport] = None

    @property
    def corruptions(self) -> list[Effect]:
        return [e for e in self.effects if e.outcome == CORRUPT]

    @property
    def rejections(self) -> list[Effect]:
        return [e for e in self.effects
                if e.outcome in (REJECT_LOUD, REJECT_VAGUE)]

    @property
    def verdict(self) -> str:
        if self.rejections:
            return "rejected"
        if self.corruptions:
            return "corrupted"
        return "clean"


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    summary: str
    #: Applied in order to a copy of each line. Each returns effects.
    rules: tuple = ()
    caveat: str = ""


# --------------------------------------------------------------------------- #
# Rule helpers
# --------------------------------------------------------------------------- #
def _cap(field_name: str, limit: int, *, source: str, unit: str = "characters",
         verified: bool = True) -> Callable:
    """Silently truncate past `limit`. The commonest and most dangerous rule."""
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = getattr(line, field_name) or ""
        length = len(value.encode("utf-8")) if unit == "bytes" else len(value)
        if length <= limit:
            return line
        if unit == "bytes":
            cut = value.encode("utf-8")[:limit].decode("utf-8", "ignore")
        else:
            cut = value[:limit]
        effects.append(Effect(
            line.index, field_name, CORRUPT,
            f"Truncated from {length} to {limit} {unit} ({source}). No error is "
            f"raised — the purchase order simply carries the shorter value.",
            verified))
        return replace(line, **{field_name: cut})
    return rule


def _forbid(field_name: str, chars: str, *, source: str,
            outcome: str = REJECT_LOUD, verified: bool = True) -> Callable:
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = getattr(line, field_name) or ""
        found = sorted({c for c in value if c in chars})
        if found:
            effects.append(Effect(
                line.index, field_name, outcome,
                f"Contains {' '.join(repr(c) for c in found)}, which {source} "
                "rejects. The cart return fails; the shopper is bounced back "
                "to their requisition with nothing in it.", verified))
        return line
    return rule


def _uom_default(known: frozenset, *, source: str, verified: bool = True) -> Callable:
    """JAGGAER's silent `EA` default — the rule behind the most expensive
    documented failure in the whole reference."""
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = (line.unit_of_measure or "").strip()
        if not value or value.upper() in known:
            return line
        effects.append(Effect(
            line.index, "unit_of_measure", CORRUPT,
            f"'{value}' is not a unit {source} recognises, so it is silently "
            "replaced with 'EA'. A box of 30 at £9.99 becomes 30 each at "
            "£9.99 — the documented case turns £9.99 into £299.70.", verified))
        return replace(line, unit_of_measure="EA")
    return rule


def _uom_must_exist(known: frozenset, *, source: str, verified: bool = True) -> Callable:
    """Coupa's version of the same problem, failing loudly instead."""
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = (line.unit_of_measure or "").strip()
        if not value or value.upper() in known:
            return line
        effects.append(Effect(
            line.index, "unit_of_measure", REJECT_LOUD,
            f"{source} fails the cart import outright when the unit does not "
            f"already exist in the buyer's configuration. '{value}' does not.",
            verified))
        return line
    return rule


def _uom_rename(mapping: dict, *, source: str, verified: bool = False) -> Callable:
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = (line.unit_of_measure or "").strip().upper()
        if value in mapping:
            effects.append(Effect(
                line.index, "unit_of_measure", CORRUPT,
                f"{source} expects '{mapping[value]}' rather than '{value}'.",
                verified))
            return replace(line, unit_of_measure=mapping[value])
        return line
    return rule


def _round_price(places: int, *, source: str, verified: bool = True) -> Callable:
    def rule(line: Line, effects: list[Effect]) -> Line:
        raw = (line.unit_price or "").strip()
        if not raw:
            return line
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return line
        exponent = -value.as_tuple().exponent
        if exponent <= places:
            return line
        rounded = value.quantize(Decimal(1).scaleb(-places))
        effects.append(Effect(
            line.index, "unit_price", CORRUPT,
            f"{source} keeps {places} decimal places, so {raw} becomes "
            f"{rounded}. On a large quantity the difference is a price "
            "mismatch the buyer will query.", verified))
        return replace(line, unit_price=str(rounded))
    return rule


def _unspsc_digits(*, source: str, verified: bool = True) -> Callable:
    """UNSPSC must be 8 digits with no punctuation or prefix."""
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = line.classification or ""
        if not value:
            return line
        cleaned = []
        changed = False
        for token in value.split(";"):
            domain, _, code = token.partition(":")
            if domain.strip().upper() != "UNSPSC":
                cleaned.append(token)
                continue
            digits = re.sub(r"\D", "", code)
            if digits != code.strip():
                changed = True
            cleaned.append(f"{domain}:{digits}")
        if changed:
            effects.append(Effect(
                line.index, "classification", CORRUPT,
                f"{source} strips punctuation from the UNSPSC code. A code "
                "that arrives malformed is simply not categorised, so the "
                "line lands in the buyer's catch-all spend category.", verified))
            return replace(line, classification=";".join(cleaned))
        return line
    return rule


def _non_ascii(*, source: str, verified: bool = True) -> Callable:
    """The us-ascii rule for `cXML-urlencoded` — §1.6 of the reference."""
    def rule(line: Line, effects: list[Effect]) -> Line:
        value = line.description or ""
        offenders = sorted({c for c in value if ord(c) > 127})
        if offenders:
            effects.append(Effect(
                line.index, "description", CORRUPT,
                f"{' '.join(offenders[:6])} is outside us-ascii. Returned via "
                "cXML-urlencoded the receiving parser must ignore the declared "
                "encoding, so these become '?' or mojibake. Use cXML-base64, "
                "or numeric character references.", verified))
            return replace(line, description="".join(
                c if ord(c) < 128 else "?" for c in value))
        return line
    return rule


# --------------------------------------------------------------------------- #
# The profiles
# --------------------------------------------------------------------------- #
#: Units JAGGAER/Coupa configurations commonly hold. Deliberately short: the
#: point of the rule is that anything outside a buyer's OWN configured list
#: gets defaulted or rejected, and no supplier can know that list. A generous
#: guess here would hide the failure this exists to show.
COMMON_UOM = frozenset({"EA", "BX", "CS", "PK", "DZ", "KG", "GR", "LB", "MTR",
                        "CM", "LTR", "ML", "SET", "PR", "RL", "TU", "BG"})

PROFILES: tuple[Profile, ...] = (
    Profile(
        key="strict",
        name="Safe target (tightest of all platforms)",
        summary=("Every limit set to the tightest value any platform imposes. "
                 "A cart that survives this survives all of them."),
        rules=(
            _cap("supplier_part_id", 100, source="JAGGAER"),
            _cap("supplier_part_auxiliary_id", 50,
                 source="Cisco-style verbatim echo"),
            _cap("description", 255, source="Ariba display / JAGGAER 256"),
            _cap("manufacturer_part_id", 100, source="JAGGAER"),
            _cap("manufacturer_name", 100, source="JAGGAER"),
            _forbid("supplier_part_id", "?{}", source="Ariba"),
            _forbid("supplier_part_auxiliary_id", "?{}", source="Ariba"),
            _round_price(4, source="JAGGAER"),
            _unspsc_digits(source="JAGGAER"),
            _uom_default(COMMON_UOM, source="JAGGAER"),
            _non_ascii(source="the cXML-urlencoded us-ascii rule"),
        ),
    ),
    Profile(
        key="ariba",
        name="SAP Ariba",
        summary=("Accepts a long description and then shows only the first 255 "
                 "characters on the requisition and the purchase order. Limits "
                 "are counted in BYTES."),
        caveat=("Ariba's own CIF table declares 255 for Supplier Part ID while "
                "the application supports 128 — transport and application "
                "limits differ, and which one bites depends on the path."),
        rules=(
            _forbid("supplier_part_id", "?{}", source="Ariba"),
            _forbid("supplier_part_auxiliary_id", "?{}", source="Ariba"),
            _cap("supplier_part_id", 128, source="Ariba application limit"),
            # Bytes, not characters: extended-ASCII costs 2 and CJK 3, so a
            # Japanese description runs out at roughly 666 characters.
            _cap("description", 255, source="Ariba requisition/PO display",
                 unit="bytes"),
            _non_ascii(source="the cXML-urlencoded us-ascii rule"),
        ),
    ),
    Profile(
        key="jaggaer",
        name="JAGGAER",
        summary=("The silent defaulter. An unrecognised unit of measure becomes "
                 "'EA' with no error, which is the single most expensive "
                 "documented punchout failure."),
        rules=(
            _cap("supplier_part_id", 100, source="JAGGAER"),
            _cap("supplier_part_auxiliary_id", 100, source="JAGGAER"),
            _cap("description", 256, source="JAGGAER"),
            _cap("manufacturer_part_id", 100, source="JAGGAER"),
            _cap("manufacturer_name", 100, source="JAGGAER"),
            _uom_default(COMMON_UOM, source="JAGGAER"),
            _round_price(4, source="JAGGAER"),
            _unspsc_digits(source="JAGGAER"),
        ),
    ),
    Profile(
        key="coupa",
        name="Coupa",
        summary=("Fails the cart import when a unit of measure does not already "
                 "exist in the buyer's configuration. Loud rather than silent — "
                 "which is the better failure to have."),
        rules=(
            _uom_must_exist(COMMON_UOM, source="Coupa"),
            _cap("description", 255, source="the safe cross-platform target",
                 verified=False),
        ),
    ),
    Profile(
        key="oracle",
        name="Oracle Procurement",
        summary="Commonly configured to require 'EACH' where others accept 'EA'.",
        caveat=("Configuration-dependent rather than a platform constant, so "
                "this is a likely failure rather than a certain one."),
        rules=(
            _uom_rename({"EA": "EACH"}, source="many Oracle configurations",
                        verified=False),
            _cap("description", 255, source="the safe cross-platform target",
                 verified=False),
        ),
    ),
)

BY_KEY = {profile.key: profile for profile in PROFILES}


def ingest(lines: list[Line], profile_key: str) -> Ingestion:
    """Push a cart through a platform and report what survives."""
    profile = BY_KEY.get(profile_key)
    if profile is None:
        raise KeyError(profile_key)

    effects: list[Effect] = []
    out: list[Line] = []
    for line in lines:
        current = line
        for rule in profile.rules:
            current = rule(current, effects)
        out.append(current)

    result = Ingestion(platform=profile.key, lines=out, effects=effects)
    result.rejected = bool(result.rejections)
    # The differ names the mechanism — truncated, rounded, uom-defaulted-to-EA
    # — so the two views agree by construction rather than by maintenance.
    result.report = diff_lines(lines, out)
    return result
