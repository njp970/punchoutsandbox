"""cXML `PunchOutOrderMessage` — the cart going back to the buyer.

*Spec: `docs/reference/fulfilment-documents.md`. This is the document the whole
sandbox exists to emit correctly.*

=============================================================================
THE RETURN IS A BROWSER FORM POST, NOT A SERVER CALL
=============================================================================
This is the fact that surprises people building a buyer integration, and it
shapes everything below. The supplier does not POST the cart to the buyer.
The supplier renders an HTML form, and **the user's browser submits it**.

Consequences that are not optional:

- The hidden field must be named `cxml-urlencoded` or `cxml-base64`
  (case-insensitive). Nothing else is recognised.
- **For `cxml-urlencoded` the document must be us-ascii**, whatever the XML
  declaration says, because the receiving parser cannot assume a charset and
  the spec directs it to IGNORE the declared encoding — the browser may have
  changed it. Any non-ASCII character must become a numeric entity. This is
  the root cause of mojibake in real requisitions, and `_to_ascii` below is
  where we get it right.
- The supplier must NEVER url-encode the `cxml-urlencoded` value; the browser
  does that. Double-encoding is the classic mistake.
- The spec's own recommendation is unambiguous: *"Best choice: Base64 encode
  the value. Don't have to worry about what the browser interprets."*

=============================================================================
`Total` EXCLUDES TAX AND SHIPPING
=============================================================================
Verbatim: *"the overall cost of the items being added to the requisition,
excluding tax and shipping charges."* Buyers that read it as tax-inclusive
show a requisition value that does not match the PO, and approval thresholds
trip on the wrong number. `build_punchout_order_message` therefore takes the
line subtotal and will not accept a gross figure.

=============================================================================
THE THREE WAYS A SESSION ENDS, AND WHY THEY ARE SEPARATE FUNCTIONS
=============================================================================
Returning a cart, returning nothing, and cancelling are three different
documents with three different meanings, and on an `edit` operation two of
them are DESTRUCTIVE in opposite directions:

  cart returned      -> replace the requisition lines with these
  empty item list    -> DELETE the existing lines
  Status 204         -> change nothing; the user cancelled

A supplier whose "user closed the tab" path emits an empty cart instead of a
204 silently wipes the buyer's requisition. They are separate functions here
so that choosing between them is a decision at the call site rather than an
accident of whether a list happened to be empty.

=============================================================================
CREDENTIALS ON THE RETURN LEG
=============================================================================
The spec says plainly: *"Do not use authentication elements in documents sent
through one-way communication. One-way transport routes through users'
browsers, so users would be able to see the document source, including
Credential elements."*

The return leg IS one-way through the browser. And yet Ariba's own canonical
sample includes `<SharedSecret>` in it. That is a genuine spec-versus-vendor
contradiction, so `shared_secret` is optional and defaults to OMITTED — the
spec-conformant behaviour — while remaining settable for anyone who has to
reproduce what a real buyer expects. The sandbox reports which one it did.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal as D
from typing import Optional

_DOCTYPE = (
    '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
)

#: The only values the DTD permits, and they gate what the buyer may do next.
#: "create" disallows any later session for these items; "inspect" allows
#: viewing only; "edit" allows both. Ignoring this is what cXML status 412
#: exists to signal.
OPERATIONS_ALLOWED = ("create", "inspect", "edit")


def _esc(value: str) -> str:
    """`&` FIRST — escaping `<`/`>` before `&` would re-escape the ampersand
    just introduced and double-encode the document."""
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _attr(value: str) -> str:
    return _esc(value).replace('"', "&quot;").replace("'", "&apos;")


def _stamp(value: datetime, *, who: str) -> str:
    """ISO 8601 with a numeric offset. The `Z` designator is NOT allowed by
    cXML, and `datetime.utcnow().isoformat() + "Z"` is probably the single
    most violated rule in the standard."""
    if value.tzinfo is None:
        raise ValueError(f"{who} must be timezone-aware — cXML forbids 'Z' and "
                         "requires a numeric UTC offset")
    return value.isoformat()


@dataclass(frozen=True)
class CartItem:
    supplier_part_id: str
    quantity: D
    unit_price: D
    description: str
    unit_of_measure: str
    classification: str                       # UNSPSC, unpunctuated
    currency: str = "GBP"
    supplier_part_auxiliary_id: Optional[str] = None
    manufacturer_part_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    lead_time_days: Optional[int] = None
    #: Short name for buyers that truncate. If absent, the buyer truncates the
    #: description itself — which is where mid-multibyte-character corruption
    #: comes from.
    short_name: Optional[str] = None
    classification_domain: str = "UNSPSC"

    @property
    def subtotal(self) -> D:
        return (self.quantity * self.unit_price).quantize(D("0.01"))


def _item(item: CartItem) -> str:
    aux = (
        f"<SupplierPartAuxiliaryID>{_esc(item.supplier_part_auxiliary_id)}"
        "</SupplierPartAuxiliaryID>"
        if item.supplier_part_auxiliary_id else ""
    )
    short = (f"<ShortName>{_esc(item.short_name)}</ShortName>"
             if item.short_name else "")
    # ManufacturerPartID and ManufacturerName are an all-or-nothing pair.
    manufacturer = ""
    if item.manufacturer_part_id and item.manufacturer_name:
        manufacturer = (
            f"<ManufacturerPartID>{_esc(item.manufacturer_part_id)}</ManufacturerPartID>"
            f'<ManufacturerName xml:lang="en">{_esc(item.manufacturer_name)}'
            "</ManufacturerName>"
        )
    lead_time = (f"<LeadTime>{item.lead_time_days}</LeadTime>"
                 if item.lead_time_days is not None else "")

    return (
        f'<ItemIn quantity="{item.quantity}">'
        "<ItemID>"
        f"<SupplierPartID>{_esc(item.supplier_part_id)}</SupplierPartID>"
        + aux
        + "</ItemID>"
        "<ItemDetail>"
        f'<UnitPrice><Money currency="{_attr(item.currency)}">{item.unit_price}'
        "</Money></UnitPrice>"
        # Description is repeatable and xml:lang is #REQUIRED on it. ShortName
        # is a CHILD of Description, not a sibling.
        f'<Description xml:lang="en">{short}{_esc(item.description)}</Description>'
        f"<UnitOfMeasure>{_esc(item.unit_of_measure)}</UnitOfMeasure>"
        # Classification+ — one or more, and REQUIRED. A cart with no
        # classification is not valid cXML, however many buyers tolerate it.
        f'<Classification domain="{_attr(item.classification_domain)}">'
        f"{_esc(item.classification)}</Classification>"
        + manufacturer
        + lead_time
        + "</ItemDetail>"
        "</ItemIn>"
    )


def _header(*, from_identity: str, to_identity: str, sender_identity: str,
            shared_secret: Optional[str], user_agent: str) -> str:
    secret = (f"<SharedSecret>{_esc(shared_secret)}</SharedSecret>"
              if shared_secret else "")
    return (
        "<Header>"
        f'<From><Credential domain="NetworkID"><Identity>{_esc(from_identity)}'
        "</Identity></Credential></From>"
        f'<To><Credential domain="NetworkID"><Identity>{_esc(to_identity)}'
        "</Identity></Credential></To>"
        f'<Sender><Credential domain="NetworkID"><Identity>{_esc(sender_identity)}'
        f"</Identity>{secret}</Credential>"
        f"<UserAgent>{_esc(user_agent)}</UserAgent></Sender>"
        "</Header>"
    )


def _envelope(*, payload_id: str, timestamp: str, header: str, body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + _DOCTYPE
        + f'<cXML payloadID="{_attr(payload_id)}" timestamp="{_attr(timestamp)}">'
        + header + body + "</cXML>"
    ).encode("utf-8")


def build_punchout_order_message(
    items: list[CartItem],
    *,
    buyer_cookie: str,
    payload_id: str,
    timestamp: datetime,
    from_identity: str,
    to_identity: str,
    sender_identity: str,
    operation_allowed: str = "edit",
    currency: str = "GBP",
    shared_secret: Optional[str] = None,
    user_agent: str = "PunchOut Sandbox",
    quote_status: Optional[str] = None,
) -> bytes:
    """Build the cart document.

    `buyer_cookie` is echoed **unchanged**. The spec is explicit that the
    supplier must return what it received and must not alter it, and that a
    supplier must not use it to track its own sessions because it changes
    between create, edit and inspect.

    `Total` is the sum of line subtotals, EXCLUDING tax and shipping — see the
    module docstring."""
    if operation_allowed not in OPERATIONS_ALLOWED:
        raise ValueError(
            f"operationAllowed must be one of {OPERATIONS_ALLOWED}, "
            f"not {operation_allowed!r} — the DTD enumerates this one"
        )
    ts = _stamp(timestamp, who="timestamp")
    total = sum((item.subtotal for item in items), D("0.00"))

    quote = f' quoteStatus="{_attr(quote_status)}"' if quote_status else ""
    body = (
        "<Message>"
        "<PunchOutOrderMessage>"
        f"<BuyerCookie>{_esc(buyer_cookie)}</BuyerCookie>"
        f'<PunchOutOrderMessageHeader operationAllowed="{_attr(operation_allowed)}"{quote}>'
        f'<Total><Money currency="{_attr(currency)}">{total}</Money></Total>'
        "</PunchOutOrderMessageHeader>"
        + "".join(_item(item) for item in items)
        + "</PunchOutOrderMessage>"
        "</Message>"
    )
    return _envelope(
        payload_id=payload_id, timestamp=ts,
        header=_header(from_identity=from_identity, to_identity=to_identity,
                       sender_identity=sender_identity,
                       shared_secret=shared_secret, user_agent=user_agent),
        body=body,
    )


def build_empty_cart(**kwargs) -> bytes:
    """A cart with NO items.

    On `create` this means the user cancelled and nothing is added. **On
    `edit` it instructs the buyer to DELETE the existing requisition lines.**
    That is destructive and is the reason this is its own function rather than
    `build_punchout_order_message([])` — passing an empty list by accident
    should not be able to wipe someone's requisition."""
    return build_punchout_order_message([], **kwargs)


def build_cancel(
    *,
    buyer_cookie: str,
    payload_id: str,
    timestamp: datetime,
    from_identity: str,
    to_identity: str,
    sender_identity: str,
    operation_allowed: str = "edit",
    currency: str = "GBP",
    shared_secret: Optional[str] = None,
    user_agent: str = "PunchOut Sandbox",
) -> bytes:
    """A `204 No Content` cart — "the session ended with no change".

    The difference from `build_empty_cart` is the whole point: on an `edit`
    operation this leaves the buyer's requisition alone, where an empty item
    list deletes it. Two wire forms, opposite meanings."""
    ts = _stamp(timestamp, who="timestamp")
    body = (
        "<Message>"
        '<Status code="204" text="No Content"/>'
        "<PunchOutOrderMessage>"
        f"<BuyerCookie>{_esc(buyer_cookie)}</BuyerCookie>"
        f'<PunchOutOrderMessageHeader operationAllowed="{_attr(operation_allowed)}">'
        f'<Total><Money currency="{_attr(currency)}">0.00</Money></Total>'
        "</PunchOutOrderMessageHeader>"
        "</PunchOutOrderMessage>"
        "</Message>"
    )
    return _envelope(
        payload_id=payload_id, timestamp=ts,
        header=_header(from_identity=from_identity, to_identity=to_identity,
                       sender_identity=sender_identity,
                       shared_secret=shared_secret, user_agent=user_agent),
        body=body,
    )


# --------------------------------------------------------------------------- #
# The browser form
# --------------------------------------------------------------------------- #
def _urlencoded_value(document: bytes) -> str:
    """Prepare a document for the `cxml-urlencoded` hidden field's value.

    Two transformations, and **the order between them is the whole trick**:

    1. **HTML-escape first.** The value sits inside `value="..."` in an HTML
       document, so an XML `&amp;` must become `&amp;amp;` for the browser to
       hand `&amp;` back to the buyer, who then XML-decodes it to `&`. That
       looks like double-encoding and is in fact two correct layers — the spec
       directs escaping "all ampersands that appear in contexts significant to
       HTML", and converting the quote delimiter to `&#34;`.
    2. **Then force us-ascii**, turning anything non-ASCII into a numeric
       character reference. `cxml-urlencoded` must be us-ascii whatever the
       XML declaration says, because the receiving parser cannot assume a
       charset and is directed to IGNORE the declared encoding. Without this a
       description of "Citron Dégraissant" arrives as mojibake.

    Doing these the other way round corrupts the output: `xmlcharrefreplace`
    emits `&#233;`, and a subsequent `&` escape turns that into `&amp;#233;`,
    which the buyer renders literally. That is precisely the double-encoding
    bug this sandbox exists to catch, and the first version of this function
    had it."""
    text = document.decode("utf-8")
    text = text.replace("&", "&amp;").replace('"', "&#34;")
    return text.encode("ascii", "xmlcharrefreplace").decode("ascii")


def render_return_form(
    document: bytes,
    *,
    browser_form_post_url: str,
    encoding: str = "cxml-base64",
    auto_submit: bool = True,
) -> str:
    """Render the HTML form whose submission returns the cart.

    `encoding` is `cxml-base64` by default because the spec recommends it
    outright — base64 sidesteps every question about what the browser did to
    the bytes. `cxml-urlencoded` is offered because plenty of buyers require
    it, and choosing it is exactly how a user discovers the us-ascii rule."""
    if encoding not in ("cxml-base64", "cxml-urlencoded"):
        raise ValueError(
            "encoding must be cxml-base64 or cxml-urlencoded; those two field "
            "names are the only ones the protocol recognises"
        )

    if encoding == "cxml-base64":
        value = base64.b64encode(document).decode("ascii")
    else:
        # NOT url-encoded here — the browser does that on submit, and doing it
        # ourselves is the classic double-encoding bug the spec warns about.
        value = _urlencoded_value(document)

    submit = (
        '<script>document.getElementById("punchout").submit()</script>'
        if auto_submit else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Returning your cart&hellip;</title></head>"
        "<body style='font:15px system-ui;padding:40px;text-align:center'>"
        "<p>Returning your cart to your procurement system&hellip;</p>"
        f'<form id="punchout" method="POST" '
        f'action="{_attr(browser_form_post_url)}">'
        f'<input type="hidden" name="{encoding}" value="{value}">'
        '<input type="submit" value="Continue">'
        "</form>" + submit + "</body></html>"
    )
