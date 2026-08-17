"""cXML `InvoiceDetailRequest` generation.

*Spec: `docs/reference/invoice-and-tax.md`. Several widely-believed facts about
this document are wrong; §1 of that file lists them and they are re-stated
inline below where they bite.*

=============================================================================
WHY THIS BUILDS STRINGS RATHER THAN USING ElementTree
=============================================================================
Same reason `xml_safe.py` exists: a repo-wide grep for "who touches raw XML"
should return the hardened parser and nothing else. Importing `ElementTree`
here would add a hit that a reviewer then has to re-derive as harmless, and
zero hits is a cheaper proof than one hit with a comment explaining it away.

Building a document has none of the parser's threat model — there is no
untrusted input on the way out — and cXML's invoice shape is a flat header
plus repeated blocks, so templates plus `_esc` are less code than a tree
builder, not more.

**Everything produced here is validated against the real DTD in the tests.**
That is the point of the whole product; a generator this module cannot itself
prove conformant would be indefensible.

=============================================================================
THE FOUR THINGS EVERYONE GETS WRONG
=============================================================================
1. **The indicator elements are not booleans.** `isTaxInLine` and friends
   accept the literal string `yes` and nothing else; absence means false.
   There is no `no`. Both `InvoiceDetailHeaderIndicator` and
   `InvoiceDetailLineIndicator` are MANDATORY and ORDERED even when empty, so
   the all-false case is `<InvoiceDetailHeaderIndicator/>` followed by
   `<InvoiceDetailLineIndicator/>` — not their omission.

2. **`Description` is `#REQUIRED` inside `Tax`**, and `xml:lang` is required
   on it. It may be empty but the element must exist. This is the single most
   common cXML invoice validation failure.

3. **`UnitOfMeasure` and `UnitPrice` come FIRST in `InvoiceDetailItem`**,
   before `InvoiceDetailItemReference`. That is the reverse of the intuitive
   ordering and of how every other cXML item block reads.

4. **`TaxDetail@category` and `@purpose` are free CDATA, not enumerations.**
   `category` is required. Real traffic carries `vat`, `CA` and
   `Standard Rate`. We emit a sensible value and never validate the field as
   an enum — doing so would reject conformant documents.

`exemptDetail` IS genuinely enumerated: `(zeroRated | exempt)`, nothing else.
It is the only enumerated tax attribute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal as D
from typing import Optional

from ..tax import currency as currency_precision
from ..tax.engine import TaxCalculation, TaxTreatment

_DOCTYPE = (
    '<!DOCTYPE cXML SYSTEM '
    '"http://xml.cxml.org/schemas/cXML/1.2.071/InvoiceDetail.dtd">'
)


def _esc(value: str) -> str:
    """Text escaping. `&` FIRST, always — escaping `<`/`>` before `&` would
    re-escape the ampersand just introduced and double-encode the document."""
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _attr(value: str) -> str:
    return _esc(value).replace('"', "&quot;").replace("'", "&apos;")


def _stamp(value: datetime, *, who: str) -> str:
    """cXML timestamps are ISO 8601 WITH a numeric UTC offset.

    The spec is explicit that the `Z` designator is NOT allowed, and a naive
    `datetime.utcnow().isoformat() + "Z"` is probably the most violated rule
    in the whole standard. A naive datetime is refused outright here rather
    than silently emitted without an offset, which some suppliers accept and
    then misinterpret as local time."""
    if value.tzinfo is None:
        raise ValueError(f"{who} must be timezone-aware — cXML forbids 'Z' and "
                         "requires a numeric offset")
    text = value.isoformat()
    if text.endswith("Z"):  # belt and braces; isoformat() should never do this
        raise ValueError(f"{who} serialised with a 'Z' designator, which cXML forbids")
    return text


def _money(tag: str, amount: D, currency: str) -> str:
    """Every `Money` element in the document goes through here.

    Quantized to the CURRENCY's precision at this single point, rather than
    trusting whatever the caller computed. `JPY 1000.00` validates against the
    DTD — the DTD only knows the field is a number — and is still wrong, since
    the yen has no minor unit. One formatting point means one place to be
    right, instead of every caller having to remember."""
    return (f'<{tag}><Money currency="{_attr(currency)}">'
            f'{currency_precision.quantize(amount, currency)}</Money></{tag}>')


@dataclass(frozen=True)
class Party:
    role: str                # billTo | remitTo | soldTo | from | issuerOfInvoice
    name: str
    street: str
    city: str
    postal_code: str
    country_code: str
    country_name: str
    tax_id: Optional[str] = None
    tax_id_domain: str = "vatID"   # vatID | gstID | federalTaxID | stateTaxID


@dataclass(frozen=True)
class InvoiceLine:
    line_number: int
    quantity: D
    unit_of_measure: str
    unit_price: D
    supplier_part_id: str
    description: str
    po_line_number: int
    supplier_part_auxiliary_id: Optional[str] = None
    classification: Optional[str] = None      # UNSPSC
    manufacturer_part_id: Optional[str] = None
    manufacturer_name: Optional[str] = None

    @property
    def subtotal(self) -> D:
        return (self.quantity * self.unit_price).quantize(D("0.01"))


@dataclass
class Invoice:
    invoice_id: str
    invoice_date: datetime
    order_id: str
    order_payload_id: str
    currency: str
    lines: list[InvoiceLine]
    tax: TaxCalculation
    parties: list[Party] = field(default_factory=list)
    #: standard | creditMemo | debitMemo | lineLevelCreditMemo | lineLevelDebitMemo
    purpose: str = "standard"
    operation: str = "new"          # new | delete
    #: Required for credit memos and for operation="delete" — identifies the
    #: original document by its payloadID.
    original_payload_id: Optional[str] = None
    tax_in_line: bool = False
    shipping_amount: Optional[D] = None

    @property
    def is_credit(self) -> bool:
        return self.purpose in ("creditMemo", "lineLevelCreditMemo")


def _tax_block(calc: TaxCalculation, currency: str, *, indent: str = "") -> str:
    """A `<Tax>` element: total, MANDATORY Description, then one `TaxDetail`
    per band."""
    details = []
    for band in calc.lines:
        attrs = [
            'purpose="tax"',
            # Free CDATA, not an enum. See the module docstring.
            f'category="{_attr(band.category)}"',
            f'percentageRate="{band.rate}"',
        ]
        if band.exempt_detail:
            attrs.append(f'exemptDetail="{band.exempt_detail}"')
        exemption = ""
        if band.vatex:
            # TaxExemption carries a coded reason, independent of and
            # coexisting with exemptDetail. We borrow the UNCL5305/VATEX code
            # so a downstream PEPPOL mapper can round-trip the distinction
            # that cXML itself cannot express — a convention, and labelled as
            # one wherever it is shown to a user.
            exemption = (
                f'<TaxExemption exemptCode="{_attr(band.vatex)}">'
                f'<ExemptReason xml:lang="en">{_esc(band.description)}</ExemptReason>'
                "</TaxExemption>"
            )
        details.append(
            f'<TaxDetail {" ".join(attrs)}>'
            + _money("TaxableAmount", band.taxable_amount, currency)
            + _money("TaxAmount", band.tax_amount, currency)
            + f'<Description xml:lang="en">{_esc(band.description)}</Description>'
            + exemption
            + "</TaxDetail>"
        )
    return (
        "<Tax>"
        f'<Money currency="{_attr(currency)}">{calc.tax_total}</Money>'
        # #REQUIRED. Omitting this is the most common invoice validation
        # failure there is.
        f'<Description xml:lang="en">{_esc(calc.jurisdiction.tax_name)}</Description>'
        + "".join(details)
        + "</Tax>"
    )


def _party(p: Party) -> str:
    id_ref = (
        f'<IdReference domain="{_attr(p.tax_id_domain)}" '
        f'identifier="{_attr(p.tax_id)}"/>'
        if p.tax_id else ""
    )
    return (
        "<InvoicePartner>"
        f'<Contact role="{_attr(p.role)}">'
        f'<Name xml:lang="en">{_esc(p.name)}</Name>'
        "<PostalAddress>"
        f"<Street>{_esc(p.street)}</Street>"
        f"<City>{_esc(p.city)}</City>"
        f"<PostalCode>{_esc(p.postal_code)}</PostalCode>"
        f'<Country isoCountryCode="{_attr(p.country_code)}">'
        f"{_esc(p.country_name)}</Country>"
        "</PostalAddress>"
        "</Contact>"
        + id_ref
        + "</InvoicePartner>"
    )


def _item(line: InvoiceLine, invoice: Invoice) -> str:
    aux = (
        f"<SupplierPartAuxiliaryID>{_esc(line.supplier_part_auxiliary_id)}"
        "</SupplierPartAuxiliaryID>"
        if line.supplier_part_auxiliary_id else ""
    )
    classification = (
        f'<Classification domain="UNSPSC">{_esc(line.classification)}</Classification>'
        if line.classification else ""
    )
    # ManufacturerPartID and ManufacturerName are an ALL-OR-NOTHING pair in the
    # DTD — `(A, B)?`. Emitting one alone is invalid.
    manufacturer = ""
    if line.manufacturer_part_id and line.manufacturer_name:
        manufacturer = (
            f"<ManufacturerPartID>{_esc(line.manufacturer_part_id)}</ManufacturerPartID>"
            f'<ManufacturerName xml:lang="en">{_esc(line.manufacturer_name)}</ManufacturerName>'
        )

    return (
        f'<InvoiceDetailItem invoiceLineNumber="{line.line_number}" '
        f'quantity="{line.quantity}">'
        # UnitOfMeasure and UnitPrice FIRST — before the item reference. The
        # reverse of the intuitive order, and a frequent validation failure.
        f"<UnitOfMeasure>{_esc(line.unit_of_measure)}</UnitOfMeasure>"
        + _money("UnitPrice", line.unit_price, invoice.currency)
        + f'<InvoiceDetailItemReference lineNumber="{line.po_line_number}">'
        "<ItemID>"
        f"<SupplierPartID>{_esc(line.supplier_part_id)}</SupplierPartID>"
        + aux
        + "</ItemID>"
        f'<Description xml:lang="en">{_esc(line.description)}</Description>'
        + classification
        + manufacturer
        + "</InvoiceDetailItemReference>"
        + _money("SubtotalAmount", line.subtotal, invoice.currency)
        + _money("GrossAmount", line.subtotal, invoice.currency)
        + _money("NetAmount", line.subtotal, invoice.currency)
        + "</InvoiceDetailItem>"
    )


def build_invoice(
    invoice: Invoice,
    *,
    payload_id: str,
    timestamp: datetime,
    from_identity: str,
    to_identity: str,
    sender_identity: str,
    shared_secret: str,
) -> bytes:
    """Produce a conformant `InvoiceDetailRequest`.

    Credit memos: `purpose="creditMemo"` requires `isHeaderInvoice="yes"` and
    a NEGATIVE `DueAmount`, and `DocumentReference` becomes mandatory so the
    original invoice can be identified. `DueAmount` is itself optional in the
    DTD, which surprises people given the credit rules are phrased entirely in
    terms of it — so it is emitted unconditionally for credits."""
    ts = _stamp(timestamp, who="timestamp")
    invoice_date = _stamp(invoice.invoice_date, who="invoice.invoice_date")

    header_indicator_attrs = ""
    if invoice.is_credit:
        header_indicator_attrs = ' isHeaderInvoice="yes"'
    line_indicator_attrs = ' isTaxInLine="yes"' if invoice.tax_in_line else ""

    doc_reference = (
        f'<DocumentReference payloadID="{_attr(invoice.original_payload_id)}"/>'
        if invoice.original_payload_id else ""
    )
    if invoice.is_credit and not invoice.original_payload_id:
        raise ValueError(
            "a credit memo must carry DocumentReference identifying the "
            "original invoice — the DTD permits omitting it, the rules do not"
        )

    subtotal = invoice.tax.subtotal
    net = invoice.tax.net_total
    due = -net if invoice.is_credit else net

    summary = (
        "<InvoiceDetailSummary>"
        + _money("SubtotalAmount", subtotal, invoice.currency)
        + _tax_block(invoice.tax, invoice.currency)
        + (_money("ShippingAmount", invoice.shipping_amount, invoice.currency)
           if invoice.shipping_amount is not None else "")
        + _money("GrossAmount", net, invoice.currency)
        + _money("NetAmount", net, invoice.currency)
        + _money("DueAmount", due, invoice.currency)
        + "</InvoiceDetailSummary>"
    )

    body = (
        '<Request deploymentMode="test">'
        "<InvoiceDetailRequest>"
        f'<InvoiceDetailRequestHeader invoiceID="{_attr(invoice.invoice_id)}" '
        f'purpose="{_attr(invoice.purpose)}" operation="{_attr(invoice.operation)}" '
        f'invoiceDate="{_attr(invoice_date)}">'
        # BOTH indicators are mandatory and ordered, even when empty.
        f"<InvoiceDetailHeaderIndicator{header_indicator_attrs}/>"
        f"<InvoiceDetailLineIndicator{line_indicator_attrs}/>"
        + "".join(_party(p) for p in invoice.parties)
        + doc_reference
        + "</InvoiceDetailRequestHeader>"
        "<InvoiceDetailOrder>"
        "<InvoiceDetailOrderInfo>"
        f'<OrderReference orderID="{_attr(invoice.order_id)}">'
        f'<DocumentReference payloadID="{_attr(invoice.order_payload_id)}"/>'
        "</OrderReference>"
        "</InvoiceDetailOrderInfo>"
        + "".join(_item(line, invoice) for line in invoice.lines)
        + "</InvoiceDetailOrder>"
        + summary
        + "</InvoiceDetailRequest>"
        "</Request>"
    )

    header = (
        "<Header>"
        f'<From><Credential domain="NetworkID"><Identity>{_esc(from_identity)}'
        "</Identity></Credential></From>"
        f'<To><Credential domain="NetworkID"><Identity>{_esc(to_identity)}'
        "</Identity></Credential></To>"
        f'<Sender><Credential domain="NetworkID"><Identity>{_esc(sender_identity)}'
        f"</Identity><SharedSecret>{_esc(shared_secret)}</SharedSecret></Credential>"
        "<UserAgent>PunchOut Sandbox</UserAgent></Sender>"
        "</Header>"
    )

    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + _DOCTYPE
        + f'<cXML payloadID="{_attr(payload_id)}" timestamp="{_attr(ts)}">'
        + header
        + body
        + "</cXML>"
    )
    return document.encode("utf-8")
