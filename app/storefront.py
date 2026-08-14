"""The storefront — browse, product detail, and cart.

*This is the shop a punchout session lands in. Its job is to look and behave
enough like a real supplier's site that a buyer-side integration exercises the
same code paths it would in production, and then to be completely honest about
what it is doing differently.*

=============================================================================
THE STOREFRONT LABELS ITS OWN DEFECTS
=============================================================================
`catalogue/data.py` deliberately seeds imperfect products — free-text units,
over-length descriptions, aux IDs past the JAGGAER limit. The instinct is to
hide that, because a shop showing "this product is broken" looks unfinished.

The opposite is right. A user needs to know which lines will misbehave and
why, so they can *choose* to add one and watch what their buyer system does.
Hidden defects make the sandbox a puzzle; labelled ones make it an
instrument. Hence the `quirk` pill on the product card and the full
explanation on the detail page.

=============================================================================
PRICE RESOLUTION HAPPENS HERE, NOT IN THE cXML
=============================================================================
`ItemIn` carries exactly one `UnitPrice` and cXML has no tier structure. So
the break table is shown to the human, the tier is resolved at add-to-cart,
and only the resolved price crosses the wire. `_line_of` records which tier
fired so the cart can show it — that visibility is the whole reason a user
can tell "the price changed" from "the price was always going to be this at
this quantity".
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Optional

from .catalogue.data import (
    BY_SKU, CATEGORIES, PRODUCTS, SUPPLIER_NAME, ancestry, children_of,
    products_in_tree, search,
)
from .catalogue.models import Product, Quirk
from .catalogue.taxonomy import normalise_uom
from .http import Request, Response, html, redirect
from .tax.engine import Rounding, TaxTreatment, TaxableLine, calculate
from .ui.render import render

#: Purely decorative, and chosen per top-level category rather than per
#: product: a real catalogue has photographs, and inventing a distinct glyph
#: for every SKU would look more fake, not less.
_GLYPHS = {
    "office": "✎", "print": "⎙", "it": "⌨", "furn": "☖",
    "fac": "⚘", "ppe": "⛑", "cater": "☕", "pack": "▤",
    "mro": "⚒", "lab": "⚗",
}

_QUIRK_DETAIL = {
    Quirk.SLOPPY_UOM: (
        "The unit of measure is free text rather than a UN/CEFACT code. "
        "JAGGAER silently maps anything it does not recognise to EA, so a box "
        "of 100 becomes 100 individual items with no error anywhere. Coupa "
        "fails the cart import instead."
    ),
    Quirk.PACK_IN_UOM: (
        "The pack size is smuggled into the unit of measure. JAGGAER "
        "explicitly documents this as 'Not Recommended' because shoppers read "
        "'100/BX' as a hundred boxes. Pack size belongs in the description."
    ),
    Quirk.LONG_DESCRIPTION: (
        "The description exceeds 256 characters. JAGGAER truncates silently, "
        "and Ariba accepts 2000 bytes but shows only the first 255 on the "
        "requisition and the purchase order — so it passes validation, looks "
        "fine in search, and is cut on the document that matters."
    ),
    Quirk.DELIMITED_PART_ID: (
        "The part number carries delimiters. Some platforms can be configured "
        "to strip them on the PO while still matching invoices WITH them — "
        "enabling one without the other guarantees invoice rejection later."
    ),
    Quirk.NON_ASCII: (
        "The description contains non-ASCII characters. Over "
        "cxml-urlencoded that is spec-violating regardless of the declared "
        "encoding: the field must be us-ascii, so these have to become "
        "numeric entities or travel as cxml-base64. This is the root cause of "
        "mojibake in real requisitions."
    ),
    Quirk.LONG_AUX_ID: (
        "The auxiliary ID exceeds 100 characters. On JAGGAER that is a hard "
        "cart-return failure rather than a truncation; Ariba allows 255 and "
        "Coupa 765, so the same line succeeds or fails depending purely on "
        "which buyer receives it."
    ),
    Quirk.SUB_PENNY_PRICE: (
        "The unit price carries more than four decimal places. JAGGAER "
        "accepts four and rounds beyond that, and multiplies before rounding "
        "— so a supplier who rounds per unit first will disagree with the "
        "buyer's extended price."
    ),
    Quirk.PUNCTUATED_UNSPSC: (
        "The UNSPSC code is written the way humans write it, with "
        "punctuation. Every platform requires it unpunctuated; the code is "
        "either rejected or silently not categorised."
    ),
}


def _glyph(product: Product) -> str:
    return _GLYPHS.get(product.category.split(".")[0], "□")


def _fmt(amount: D, currency: str = "GBP") -> str:
    symbol = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, "")
    return f"{symbol}{amount:.2f}"


def _card(product: Product) -> dict:
    best = min((t.unit_price for t in product.price_breaks),
               default=product.unit_price)
    return {
        "sku": product.sku,
        "name": product.name,
        "unspsc": product.unspsc,
        "price": _fmt(product.unit_price),
        "best_price": _fmt(best),
        "has_breaks": bool(product.price_breaks),
        "glyph": _glyph(product),
        "quirks": [q.value for q in product.quirks],
        "quirk_detail": " ".join(_QUIRK_DETAIL[q] for q in product.quirks),
    }


# --------------------------------------------------------------------------- #
# Cart state
# --------------------------------------------------------------------------- #
# The cart lives in the session record, not in a cookie: a punchout cart can
# hold a few hundred lines and cookies cap at 4KB. `Session` is supplied by the
# caller so this module stays free of storage concerns.
def _line_of(product: Product, quantity: int) -> dict:
    unit = product.price_for(quantity)
    tier = None
    for candidate in sorted(product.price_breaks, key=lambda t: t.min_qty):
        if quantity >= candidate.min_qty:
            tier = candidate.min_qty
    uom, _ = normalise_uom(product.uom)
    return {
        "sku": product.sku,
        "name": product.name,
        "uom": uom,
        "quantity": quantity,
        "unit_price": _fmt(unit),
        "unit_price_raw": unit,
        "total": _fmt(unit * quantity),
        "tier_applied": tier,
        "aux": product.aux_token,
        "aux_short": (product.aux_token[:28] + "…"
                      if product.aux_token and len(product.aux_token) > 28
                      else product.aux_token),
        "quirks": [q.value for q in product.quirks],
        "quirk_detail": " ".join(_QUIRK_DETAIL[q] for q in product.quirks),
    }


def cart_lines(cart: dict[str, int]) -> list[dict]:
    return [_line_of(BY_SKU[sku], qty) for sku, qty in cart.items() if sku in BY_SKU]


def cart_totals(lines: list[dict], *, country: str = "GB") -> dict:
    taxable = [
        TaxableLine(i + 1, (l["unit_price_raw"] * l["quantity"]).quantize(D("0.01")))
        for i, l in enumerate(lines)
    ]
    calc = calculate(taxable, jurisdiction_code=country,
                     treatment=TaxTreatment.STANDARD, rounding=Rounding.PER_LINE)
    return {
        "subtotal": _fmt(calc.subtotal),
        "net": _fmt(calc.net_total),
        "tax_bands": [
            {"label": f"{calc.jurisdiction.tax_name} @ {band.rate}%",
             "amount": _fmt(band.tax_amount)}
            for band in calc.lines
        ],
        "notes": calc.notes,
        "calc": calc,
    }


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def view_shop(request: Request, *, session=None, cart: dict[str, int]) -> Response:
    query = (request.query.get("q") or "").strip()
    category_id = request.params.get("category")

    if query:
        products = search(query)
        return html(render(
            "shop.html", nav="shop", session=session, cart_count=len(cart),
            heading="Search results", query=query, supplier=SUPPLIER_NAME,
            breadcrumb=[], subcategories=[],
            products=[_card(p) for p in products],
            total_products=len(PRODUCTS), total_categories=len(CATEGORIES),
        ))

    if category_id:
        children = children_of(category_id)
        return html(render(
            "shop.html", nav="shop", session=session, cart_count=len(cart),
            heading=next((c.name for c in CATEGORIES if c.id == category_id),
                         "Catalogue"),
            breadcrumb=ancestry(category_id), supplier=SUPPLIER_NAME,
            subcategories=[
                {"id": c.id, "name": c.name, "count": len(products_in_tree(c.id))}
                for c in children
            ],
            # A branch node lists its children; only a leaf lists products.
            # Showing both would double-count and make the counts lie.
            products=[] if children else [
                _card(p) for p in products_in_tree(category_id)
            ],
            total_products=len(PRODUCTS), total_categories=len(CATEGORIES),
        ))

    return html(render(
        "shop.html", nav="shop", session=session, cart_count=len(cart),
        heading="Meridian Supply Co.", supplier=SUPPLIER_NAME, breadcrumb=[],
        subcategories=[
            {"id": c.id, "name": c.name, "count": len(products_in_tree(c.id))}
            for c in children_of(None)
        ],
        products=[], total_products=len(PRODUCTS), total_categories=len(CATEGORIES),
    ))


def view_product(request: Request, *, session=None, cart: dict[str, int]) -> Response:
    product = BY_SKU.get(request.params.get("sku", ""))
    if product is None:
        return html("<h1>No such product</h1>", status=404)

    uom, advisory = normalise_uom(product.uom)
    return html(render(
        "product.html", nav="shop", session=session, cart_count=len(cart),
        breadcrumb=ancestry(product.category),
        p={
            "sku": product.sku, "name": product.name,
            "description": product.description, "unspsc": product.unspsc,
            "uom": product.uom, "uom_normalised": uom, "uom_advisory": advisory,
            "price": _fmt(product.unit_price),
            "manufacturer": product.manufacturer,
            "manufacturer_part_id": product.manufacturer_part_id,
            "lead_time_days": product.lead_time_days,
            "country_of_origin": product.country_of_origin,
            "pack_size": product.pack_size,
            "min_order_qty": product.min_order_qty,
            "order_increment": product.order_increment,
            "aux_token": product.aux_token,
            "hazardous": product.hazardous,
            "price_breaks": [
                {"min_qty": t.min_qty, "price": _fmt(t.unit_price)}
                for t in sorted(product.price_breaks, key=lambda t: t.min_qty)
            ],
            "quirk_notes": [
                {"slug": q.value, "detail": _QUIRK_DETAIL[q]} for q in product.quirks
            ],
        },
    ))


def view_cart(request: Request, *, session=None, cart: dict[str, int]) -> Response:
    lines = cart_lines(cart)
    return html(render(
        "cart.html", nav="cart", session=session, cart_count=len(cart),
        lines=lines, totals=cart_totals(lines) if lines else None,
    ))


def add_to_cart(request: Request, *, cart: dict[str, int]) -> Response:
    form = request.form()
    sku = form.get("sku", "")
    product = BY_SKU.get(sku)
    if product is None:
        return html("<h1>No such product</h1>", status=404)
    try:
        quantity = int(form.get("quantity") or product.min_order_qty)
    except ValueError:
        quantity = product.min_order_qty
    # Clamp rather than reject. A real storefront enforces its own minimum
    # and increment before the cart is ever posted — that enforcement is
    # exactly what a buyer integration never sees and therefore never tests.
    quantity = max(quantity, product.min_order_qty)
    if product.order_increment > 1:
        remainder = (quantity - product.min_order_qty) % product.order_increment
        if remainder:
            quantity += product.order_increment - remainder
    cart[sku] = cart.get(sku, 0) + quantity
    return redirect("/cart")


def remove_from_cart(request: Request, *, cart: dict[str, int]) -> Response:
    cart.pop(request.form().get("sku", ""), None)
    return redirect("/cart")
