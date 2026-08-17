"""How many decimal places a currency actually has.

=============================================================================
WHY THIS EXISTS
=============================================================================
Everything here quantized to two decimal places, because sterling, the euro
and the dollar have two and they are what got tested. So a yen invoice came
out as `JPY 1000.00`.

The yen has **no minor unit**. There is no such thing as 0.01 JPY, and a
document claiming one is wrong in the same quiet way this whole service exists
to catch — it validates against the DTD, because the DTD only knows the field
is a number, and then a buyer platform either rejects it or silently reads it
as something else.

ISO 4217 assigns each currency an exponent. Nearly all are 2; a handful are 0
and a handful are 3. Getting it wrong in either direction is a real error:
`JPY 1000.00` overstates the precision, and `BHD 1.500` truncated to `1.50`
loses a fils.

=============================================================================
WHAT THIS DELIBERATELY IS NOT
=============================================================================
Not a currency library, and not a source of exchange rates — nothing here
converts anything. It answers exactly one question, which is the only one an
invoice generator needs: how many decimal places may this amount carry.

Unknown codes get 2 rather than raising. An invoice in a currency we have not
enumerated is far better emitted with the ordinary assumption than refused,
and the caller is not in a position to do anything about it either way.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: ISO 4217 exponent 0 — no minor unit at all.
_NO_MINOR_UNIT = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF",
    "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
})

#: ISO 4217 exponent 3 — thousandths. Mostly Gulf dinars.
_THREE_PLACES = frozenset({
    "BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND",
})


def minor_units(currency: str) -> int:
    """Decimal places for a currency code. Two unless it is one of the few
    that is not."""
    code = (currency or "").strip().upper()
    if code in _NO_MINOR_UNIT:
        return 0
    if code in _THREE_PLACES:
        return 3
    return 2


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round to the currency's own precision.

    ROUND_HALF_UP rather than Python's default ROUND_HALF_EVEN. Banker's
    rounding is the better statistical choice and the wrong invoicing choice:
    every tax authority's published worked examples round halves up, and a
    supplier whose totals differ from the buyer's by a penny on half the lines
    gets the invoice queried."""
    places = minor_units(currency)
    exponent = Decimal(1).scaleb(-places) if places else Decimal(1)
    return amount.quantize(exponent, rounding=ROUND_HALF_UP)
