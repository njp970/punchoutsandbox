"""The catalogue data model for Meridian Supply Co., the sandbox's supplier.

*Meridian Supply Co. is INVENTED, as is every manufacturer brand in
`data.py`. BRIEF.md §6 is the constraint: synthetic catalogues and documents
are fine for invented companies, and generating realistic documents carrying a
real company's name would be trademark infringement and, outside the sandbox,
a functioning forgery. Nothing here may ever be renamed to a real supplier.*

=============================================================================
THE ONE MODELLING DECISION THAT MAKES THIS READ AS REAL
=============================================================================
Merchandising categories and UNSPSC codes are SEPARATE, and the relationship
between them is many-to-many.

The tempting shortcut is to make UNSPSC the navigation tree — it is already
hierarchical, so why build a second one? Because no real catalogue works that
way, and a procurement person spots it immediately. Real suppliers maintain:

- a **merchandising hierarchy**, ~3 levels, built for a human browsing; and
- a **flat UNSPSC mapping** per SKU, built for spend analytics.

They are deliberately decoupled. One UNSPSC code appears under several leaf
categories (`43211503` Notebook computers sits under both "Laptops" and, for
a rugged model, "Field Equipment"), and one leaf category spans several UNSPSC
codes ("Toner & Ink" covers laser toner, inkjet, drums and ribbons — four
codes). Modelling that many-to-many relationship rather than collapsing it is
the single detail that separates a convincing mock from an obvious one.

=============================================================================
WHY PRICE BREAKS LIVE HERE AND NOT IN THE cXML
=============================================================================
`ItemIn` carries exactly one `UnitPrice`. cXML has no tier structure at all.

So a real punchout works like this: the storefront shows a 1/10/50/250 break
table, the user picks a quantity, and the cart posts the **resolved** unit
price for that quantity. The tiers never cross the wire. `price_for()` is that
resolution, and keeping it on this side of the boundary is what makes the
sandbox behave like a supplier rather than like a data dump.

The related trap is pack pricing. "£24.99 per box of 100" has a correct cXML
expression — `PriceBasisQuantity` with `quantity` and `conversionFactor` — and
a wrong one that is endemic: writing `<UnitOfMeasure>100/BX</UnitOfMeasure>`.
JAGGAER explicitly documents the second as "Not Recommended" because shoppers
read it as 100 boxes. `pack_size` here is the honest field; `quirk` is how we
deliberately reproduce the dishonest one.

=============================================================================
`quirk` — WHY THE DATA IS DELIBERATELY IMPERFECT
=============================================================================
A catalogue whose data is cleaner than reality trains people for a world that
does not exist. Real feeds carry `EACH` where `EA` was meant, pack sizes
smuggled into the unit of measure, descriptions past every buyer's limit, and
part numbers with delimiters that some platforms strip and others match on.

`Quirk` marks products that carry one of those defects on purpose. They are
not bugs and must not be "fixed": they are the inputs that exercise
`taxonomy.normalise_uom`, the differ's diagnosis paths, and the advisory layer
in `validation.py`. Every one is a real, documented failure from
`docs/reference/`, not an invented one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Quirk(str, Enum):
    """A deliberate imperfection. The value is the slug the UI shows."""

    #: UOM written as free text (`EACH`) rather than a code. JAGGAER silently
    #: coerces unrecognised units to EA; Coupa fails the cart import outright.
    SLOPPY_UOM = "sloppy-uom"
    #: Pack size smuggled into the unit of measure (`100/BX`). Explicitly
    #: called "Not Recommended" by JAGGAER — shoppers read it as 100 boxes.
    PACK_IN_UOM = "pack-size-in-uom"
    #: Description past 256 characters. Silently truncated by JAGGAER, and
    #: cut to 255 on Ariba requisitions and POs even though 2000 are accepted.
    LONG_DESCRIPTION = "over-length-description"
    #: Part number carrying dashes. Some platforms strip delimiters on the PO
    #: while still matching invoices WITH them — guaranteed invoice rejection.
    DELIMITED_PART_ID = "delimiters-in-part-id"
    #: Non-ASCII characters in the description. Spec-violating over
    #: `cXML-urlencoded`, which must be us-ascii — the mojibake root cause.
    NON_ASCII = "non-ascii-description"
    #: Aux ID long enough to breach the JAGGAER 100-character limit, which is
    #: a hard cart-return failure rather than a truncation.
    LONG_AUX_ID = "over-length-aux-id"
    #: Unit price carrying more than 4 decimal places, which JAGGAER rounds.
    SUB_PENNY_PRICE = "sub-penny-price"
    #: UNSPSC written with the punctuation humans use (`44.12.17.04`). Every
    #: platform requires it unpunctuated.
    PUNCTUATED_UNSPSC = "punctuated-unspsc"


@dataclass(frozen=True)
class PriceBreak:
    min_qty: int
    unit_price: Decimal


@dataclass(frozen=True)
class Category:
    """A node in the merchandising tree. `parent` of None marks a top level."""

    id: str
    name: str
    parent: Optional[str] = None


@dataclass(frozen=True)
class Product:
    sku: str                       # -> SupplierPartID
    name: str                      # -> Description/ShortName (<=50 chars)
    description: str               # -> Description
    category: str                  # leaf Category.id
    unspsc: str                    # -> Classification domain="UNSPSC"
    uom: str                       # as the storefront holds it, pre-normalisation
    unit_price: Decimal
    manufacturer: str              # -> ManufacturerName (INVENTED brands only)
    manufacturer_part_id: str      # -> ManufacturerPartID
    lead_time_days: int            # -> LeadTime
    currency: str = "GBP"
    #: Units contained in one `uom`. 1 for a true each. Drives PriceBasisQuantity.
    pack_size: int = 1
    min_order_qty: int = 1
    order_increment: int = 1
    country_of_origin: str = "GB"  # ISO 3166-1 alpha-2; Extrinsic, no cXML element
    hazardous: bool = False
    price_breaks: tuple[PriceBreak, ...] = ()
    #: Contract/configuration token echoed in SupplierPartAuxiliaryID. This is
    #: the field buyers are documented to drop, truncate and mangle, so most
    #: products carry one — that is what makes the differ worth running.
    aux_token: Optional[str] = None
    quirks: tuple[Quirk, ...] = ()

    def price_for(self, quantity: int) -> Decimal:
        """The unit price a cart line would actually carry at this quantity.

        Walks the breaks and takes the best qualifying tier. Returns
        `unit_price` when no tier applies, so a product with no breaks needs no
        special-casing at the call site."""
        best = self.unit_price
        for tier in sorted(self.price_breaks, key=lambda t: t.min_qty):
            if quantity >= tier.min_qty:
                best = tier.unit_price
        return best

    def has(self, quirk: Quirk) -> bool:
        return quirk in self.quirks

    @property
    def is_pack(self) -> bool:
        """True when the price is quoted against a multi-unit pack, which is
        when `PriceBasisQuantity` becomes the correct thing to emit."""
        return self.pack_size > 1
