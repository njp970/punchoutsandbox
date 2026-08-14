"""OCI return — the cart going back to SAP as HTML form fields.

*Spec: `docs/reference/oci-and-oracle.md`. Read it before editing; several of
the rules below look arbitrary and are not.*

=============================================================================
OCI IS NOT cXML WITH DIFFERENT SPELLING
=============================================================================
There is no XML, no envelope, no credentials in the payload, and — the part
that matters most for a sandbox — **no status mechanism at all**. cXML can say
`<Status code="406">`; OCI cannot say anything. If SRM dislikes a line it
drops it and shows the user "Incomplete items in catalog, only complete items
were transferred", with the detail buried in transaction SLG1 under log object
`BBP_OCI` where the buyer's developer will never look.

That asymmetry is why this sandbox matters more for OCI than for cXML: a buyer
integration can be losing lines on every single cart and never see an error.
Every field this module truncates or normalises is therefore reported back to
the user as an advisory, because SRM will not.

=============================================================================
THE FIELD LENGTHS ARE BRUTAL AND SILENT
=============================================================================
Every OCI field is CHAR with a hard length, and SRM truncates without warning.
`DESCRIPTION` is **40 characters** — the tightest description limit of any
platform, against JAGGAER's 256 and Ariba's 2000 bytes. Any realistic product
name overflows it.

`LONGTEXT` is the escape hatch and is the one field with no length limit, but
it has a bizarre index syntax (see `_longtext_field`) that is wrong in most
implementations, and getting it wrong silently appends every item's long text
to the first item.

=============================================================================
`PRICEUNIT` IS A DIVISOR, AND OMITTING IT IS AN ORDER-OF-MAGNITUDE BUG
=============================================================================
`PRICE` is the price **per PRICEUNIT units**, and an empty PRICEUNIT means 1.
SAP's own documentation ships the trap in its example: `PRICE=50.00` with
`PRICEUNIT=5` means 10.00 each. A consumer that ignores PRICEUNIT books 50.00
per unit — a 5x overcharge with no error anywhere.

There is **no cXML equivalent**, so a supplier maintaining one cart model for
both protocols either loses the divisor or applies it twice. We always emit an
explicit PRICEUNIT rather than relying on the default, because an explicit 1
is unambiguous and an absent field is an invitation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from html import escape
from typing import Optional

#: OCI field lengths, from the SAP OCI 4.0/5.0 specification. Every one is
#: CHAR and every overflow is a silent truncation in SRM.
FIELD_LIMITS: dict[str, int] = {
    "DESCRIPTION": 40,
    "MATNR": 40,
    "QUANTITY": 15,
    "UNIT": 3,
    "PRICE": 15,
    "CURRENCY": 5,
    "PRICEUNIT": 5,
    "LEADTIME": 5,
    "VENDOR": 10,
    "VENDORMAT": 40,
    "MANUFACTCODE": 10,
    "MANUFACTMAT": 40,
    "MATGROUP": 10,
    "SERVICE": 1,
    "CONTRACT": 10,
    "CONTRACT_ITEM": 5,
    "EXT_QUOTE_ID": 35,
    "EXT_QUOTE_ITEM": 10,
    "EXT_PRODUCT_ID": 40,
    "EXT_SCHEMA_TYPE": 10,
    "EXT_CATEGORY_ID": 60,
    "EXT_CATEGORY": 40,
    "CUST_FIELD1": 10,
    "CUST_FIELD2": 10,
    "CUST_FIELD3": 10,
    "CUST_FIELD4": 20,
    "CUST_FIELD5": 50,
}


@dataclass
class OciItem:
    description: str
    quantity: D
    unit: str                        # ISO code, 3 chars — NOT the SAP internal code
    price: D
    currency: str = "GBP"
    #: The number of units `price` covers. Emitted explicitly, always.
    price_unit: int = 1
    vendor_mat: Optional[str] = None      # the SUPPLIER's part number
    manufacturer_code: Optional[str] = None
    manufacturer_mat: Optional[str] = None
    lead_time_days: Optional[int] = None
    long_text: Optional[str] = None
    ext_product_id: Optional[str] = None  # catalog DB key; needed for DETAIL/VALIDATE
    ext_category_id: Optional[str] = None # e.g. UNSPSC
    ext_schema_type: Optional[str] = None # required if ext_category_id is set
    service: bool = False


@dataclass
class OciAdvisory:
    field: str
    line: int
    message: str


def _truncate(name: str, value: str, line: int,
              advisories: list[OciAdvisory]) -> str:
    """Truncate to the OCI field limit, and SAY SO.

    SRM does this silently. Reporting it is most of what this sandbox is
    for — a buyer whose product names are being cut to 40 characters has no
    other way to find out."""
    limit = FIELD_LIMITS.get(name)
    if limit is None or len(value) <= limit:
        return value
    advisories.append(OciAdvisory(
        name, line,
        f"{name} is {len(value)} characters and OCI allows {limit}. SRM "
        f"truncates silently — you would see '{value[:limit]}' with no error. "
        + ("Use LONGTEXT for the full text; it is the one unlimited field."
           if name == "DESCRIPTION" else "")
    ))
    return value[:limit]


def _longtext_field(index: int) -> str:
    """`NEW_ITEM-LONGTEXT_n:132[]` — the one field that breaks the convention.

    The index moves OUT of the brackets and into the name, followed by `:132`,
    and the brackets stay but empty. `132` is the SAPscript line width
    (`TLINE-TDLINE` is CHAR 132) and must not be varied.

    Writing it the intuitive way — `NEW_ITEM-LONGTEXT[1]` — is a documented,
    frequently-hit bug: every item's long text gets appended to the first
    item."""
    return f"NEW_ITEM-LONGTEXT_{index}:132[]"


def build_fields(items: list[OciItem]) -> tuple[dict[str, str], list[OciAdvisory]]:
    """Flatten a cart into OCI form fields.

    Returns `(fields, advisories)`. **Indices start at 1, not 0** — the OCI
    spec is explicit, and a zero-based cart is silently ignored or truncated
    at the gap."""
    fields: dict[str, str] = {}
    advisories: list[OciAdvisory] = []

    for offset, item in enumerate(items):
        n = offset + 1                      # 1-based, deliberately

        def put(name: str, value) -> None:
            if value is None or value == "":
                return
            fields[f"NEW_ITEM-{name}[{n}]"] = _truncate(
                name, str(value), n, advisories)

        put("DESCRIPTION", item.description)
        # 11 digits before the point, 3 after, and NO thousands separators.
        # SAP is explicit that a comma decimal or a thousands comma breaks the
        # transfer, so quantities are formatted rather than str()'d.
        put("QUANTITY", f"{item.quantity:.3f}")
        put("UNIT", item.unit)
        put("PRICE", f"{item.price:.2f}")
        put("CURRENCY", item.currency)
        # Always explicit. An absent PRICEUNIT defaults to 1, which is usually
        # right and occasionally an order-of-magnitude error nobody notices.
        put("PRICEUNIT", item.price_unit)
        put("VENDORMAT", item.vendor_mat)
        put("MANUFACTCODE", item.manufacturer_code)
        put("MANUFACTMAT", item.manufacturer_mat)
        put("LEADTIME", item.lead_time_days)
        put("EXT_PRODUCT_ID", item.ext_product_id)
        if item.ext_category_id:
            put("EXT_CATEGORY_ID", item.ext_category_id)
            # Conditionally REQUIRED: EXT_SCHEMA_TYPE must accompany a
            # category, or SRM rejects the line.
            put("EXT_SCHEMA_TYPE", item.ext_schema_type or "UNSPSC")
        if item.service:
            put("SERVICE", "X")

        if item.long_text:
            # Not routed through put(): LONGTEXT has no length limit and its
            # own field-name syntax.
            fields[_longtext_field(n)] = item.long_text

        if item.unit and len(item.unit) > 3:
            advisories.append(OciAdvisory(
                "UNIT", n,
                f"'{item.unit}' is {len(item.unit)} characters; OCI UNIT is "
                "CHAR-3 and must be an ISO code. 'EACH' overflows to 'EAC'."))

    return fields, advisories


def render_return_form(
    fields: dict[str, str],
    *,
    hook_url: str,
    return_target: str = "_top",
    ok_code: str = "ADDI",
    caller: str = "CTLG",
    charset: str = "utf-8",
    auto_submit: bool = True,
) -> str:
    """Render the HTML form that returns the cart to SRM.

    THE HOOK_URL MUST BE SPLIT. This is the single most-missed requirement in
    OCI, quoted from the spec:

        It usually contains other parameters that must first be extracted and
        placed in separate input fields (of type hidden) of the form... The
        URL WITHOUT these parameters must be placed into the action attribute.

    A supplier who reuses cXML return code and POSTs to the whole HOOK_URL
    including its query string loses or duplicates parameters on the SRM side —
    and since OCI has no status mechanism, the failure is silent.

    `returntarget` goes into the form's `target`. Get it wrong inside an
    iframe-hosted SRM and the buyer's cart renders inside your catalogue
    frame, which is the classic visible symptom."""
    base, _, query = hook_url.partition("?")

    inputs: list[str] = []

    # 1. The HOOK_URL's own query parameters, promoted to hidden fields.
    for pair in query.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        from urllib.parse import unquote_plus
        inputs.append(
            f'<input type="hidden" name="{escape(unquote_plus(name), quote=True)}" '
            f'value="{escape(unquote_plus(value), quote=True)}">')

    # 2. The ITS control fields. Case is inconsistent across SAP's own
    #    documentation (~OkCode / ~okcode, ~TARGET / ~target), so we echo the
    #    spelling SAP's worked example uses and accept any case inbound.
    inputs.append(f'<input type="hidden" name="~OkCode" value="{escape(ok_code, quote=True)}">')
    inputs.append(f'<input type="hidden" name="~target" value="{escape(return_target, quote=True)}">')
    inputs.append(f'<input type="hidden" name="~CALLER" value="{escape(caller, quote=True)}">')

    # 3. The cart itself.
    for name, value in fields.items():
        inputs.append(
            f'<input type="hidden" name="{escape(name, quote=True)}" '
            f'value="{escape(value, quote=True)}">')

    submit = ('<script>document.getElementById("oci").submit()</script>'
              if auto_submit else "")

    return (
        f"<!doctype html><html><head>"
        # The spec requires the catalogue to emit the charset SRM handed it in
        # `http_content_charset` — and OCI's default is ISO-8859-1, not UTF-8.
        f'<meta http-equiv="Content-Type" content="text/html; charset={escape(charset, quote=True)}">'
        "<title>Returning your cart&hellip;</title></head>"
        "<body style='font:15px system-ui;padding:40px;text-align:center'>"
        "<p>Returning your cart to SAP&hellip;</p>"
        # POST, never GET: the spec warns GET "can lead to browser-dependent
        # length restrictions", and ~30 fields per line exhausts any URL
        # budget within about twenty items.
        f'<form id="oci" method="POST" action="{escape(base, quote=True)}" '
        f'target="{escape(return_target, quote=True)}">'
        + "".join(inputs)
        + '<input type="submit" value="Continue">'
        "</form>" + submit + "</body></html>"
    )
