"""OCI call-up — SAP sending a user into the catalogue.

*Spec: `docs/reference/oci-and-oracle.md` §4.*

=============================================================================
THERE IS NO HANDSHAKE
=============================================================================
cXML opens a session server-to-server: the buyer POSTs a
`PunchOutSetupRequest`, we answer with a `StartPage` URL, and only then does a
browser appear. OCI has none of that. **The user's browser simply arrives**,
carrying the return address and whatever credentials in query parameters or
form fields.

Two consequences worth stating, because they surprise people coming from cXML:

1. **The credentials travel in the clear on every punchout**, and if the buyer
   configured GET rather than POST they land in every proxy log and browser
   history along the path. Nothing we can do about it from this side; worth
   telling the user, which `observations()` does.

2. **`HOOK_URL` is the entire session.** There is no BuyerCookie, no session
   id, no payload id. The only thing tying a returned cart to the originating
   SRM session is that URL, which already embeds SAP's own session identity.
   So it is captured on first sight and never regenerated.

=============================================================================
PARAMETERS ARRIVE BY GET OR POST, AND BOTH MUST WORK
=============================================================================
SRM Customizing decides, and the standard value is POST — but plenty of older
configurations use GET, and SAP's own worked example checks both. A catalogue
that reads only one is broken for half its customers, so `parse_callup` merges
query and form and is deliberately case-insensitive on names: the `~` control
fields are spelled inconsistently across SAP's own documentation
(`~OkCode` / `~okcode`, `~TARGET` / `~target`, `~xmlType` / `~xml_type`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: OCI's default, and NOT UTF-8 — the reverse of cXML. SRM tells us which to
#: use via `http_content_charset`, and the spec requires the catalogue to echo
#: that charset into its own meta tag.
DEFAULT_CHARSET = "iso-8859-1"


@dataclass
class OciCallup:
    hook_url: str
    oci_version: Optional[str] = None
    return_target: str = "_top"
    charset: str = DEFAULT_CHARSET
    #: DETAIL | VALIDATE | SOURCING | BACKGROUND_SEARCH — absent for a normal
    #: interactive punchout.
    function: Optional[str] = None
    product_id: Optional[str] = None
    quantity: Optional[str] = None
    search_string: Optional[str] = None
    username: Optional[str] = None
    #: Everything we did not recognise, kept so the console can show the user
    #: exactly what their system sent.
    extras: dict[str, str] = field(default_factory=dict)
    #: True when credentials arrived via the query string rather than a POST
    #: body — worth flagging, see the module docstring.
    credentials_in_url: bool = False


def parse_callup(*, query: dict[str, str], form: dict[str, str],
                 method: str) -> OciCallup:
    """Build an `OciCallup` from whichever transport SRM used.

    Form values win over query values on a collision: a POST body is the
    explicitly-configured channel, and a stale query parameter on the same
    request is more likely to be a leftover than an intention."""
    merged: dict[str, str] = {}
    lower: dict[str, str] = {}
    for source in (query, form):
        for key, value in source.items():
            merged[key] = value
            lower[key.lower()] = value

    def take(*names: str) -> Optional[str]:
        for name in names:
            if name.lower() in lower:
                return lower[name.lower()]
        return None

    hook_url = take("HOOK_URL") or ""
    known = {
        "hook_url", "oci_version", "opi_version", "returntarget",
        "http_content_charset", "function", "productid", "quantity",
        "searchstring", "vendor", "username", "password", "~okcode",
        "~target", "~caller", "~xmltype", "~xml_type", "~xmldocument",
    }

    return OciCallup(
        hook_url=hook_url,
        oci_version=take("OCI_VERSION"),
        # `returntarget` (OCI 4.0+) and `~target` (the OCI 3.0 ITS parameter)
        # are different things that end up in the same place. Prefer the
        # modern one; fall back rather than defaulting blindly, because the
        # spec says a catalogue must supply `_top` if neither is present.
        return_target=take("returntarget", "~target") or "_top",
        charset=take("http_content_charset") or DEFAULT_CHARSET,
        function=(take("FUNCTION") or "").upper() or None,
        product_id=take("PRODUCTID"),
        quantity=take("QUANTITY"),
        search_string=take("SEARCHSTRING"),
        username=take("USERNAME"),
        extras={k: v for k, v in merged.items() if k.lower() not in known},
        credentials_in_url=bool(
            method == "GET" and ("password" in lower or "username" in lower)),
    )


def observations(callup: OciCallup) -> list[str]:
    """Things worth telling the user about their own call-up.

    OCI has no status mechanism, so this is the only feedback they will get —
    SRM certainly will not tell them."""
    notes: list[str] = []

    if not callup.hook_url:
        notes.append(
            "No HOOK_URL. OCI has no other session mechanism, so there is "
            "nowhere to return a cart to — this is the one parameter that is "
            "not optional.")
    elif not callup.hook_url.lower().startswith("https://"):
        if callup.hook_url.lower().startswith("sapevent:"):
            notes.append(
                "HOOK_URL is a SAPEVENT: pseudo-URL, which means an embedded "
                "SAP GUI browser is intercepting the submit rather than a real "
                "HTTP POST. This sandbox cannot complete that round trip.")
        else:
            notes.append(
                "HOOK_URL is not HTTPS. If your catalogue is served over "
                "HTTPS the browser will block the mixed-content POST and the "
                "cart will silently fail to return.")

    if callup.credentials_in_url:
        notes.append(
            "USERNAME/PASSWORD arrived in the query string over GET, so they "
            "are now in every proxy log, browser history and referer header "
            "along the path. SRM's standard configuration is POST.")

    if callup.oci_version is None:
        notes.append(
            "No OCI_VERSION. It is informational only, so nothing breaks — "
            "but a catalogue that gates behaviour on it would see nothing.")

    if callup.charset.lower() not in ("utf-8", "utf8"):
        notes.append(
            f"http_content_charset is '{callup.charset}'. The spec requires "
            "the catalogue to emit that charset in its meta tag, and OCI's "
            "default is ISO-8859-1 rather than UTF-8 — any character outside "
            "that codepage becomes '?' on the way back.")

    if callup.function == "SOURCING":
        notes.append(
            "FUNCTION=SOURCING is in the spec but SAP's own documentation has "
            "said 'not yet implemented in the SRM Server' unchanged since "
            "2003.")
    elif callup.function in ("DETAIL", "VALIDATE") and not callup.product_id:
        notes.append(
            f"FUNCTION={callup.function} requires PRODUCTID, which is missing. "
            "It must match an EXT_PRODUCT_ID the catalogue sent originally.")

    return notes
