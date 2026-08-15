"""Lambda entry point and route table.

`CMD ["app.handler.handler"]` in the Dockerfile points here.

Session state lives in DynamoDB — see `app/sessions.py` for why, and for the
in-memory backend that makes local development possible without credentials.

Every route that touches a session follows the same shape: read it, mutate it,
**write it back**. That last step is easy to forget and impossible to notice
locally, because `MemoryStore` hands back the same object every time so
mutations appear to persist by themselves. Against DynamoDB they silently do
not. `_with_session` exists to make the write-back structural rather than
something each handler has to remember.

Running locally:  python -m app.handler
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Optional
from urllib.parse import quote

from . import (api, contact, delivery, differ, inspector, mailer, order_request,
               orders, orderflow, platforms, reference, samples, sessions,
               setup_request, signup, storefront, telemetry, tenants)
from .catalogue.data import BY_SKU, search
from .catalogue.taxonomy import normalise_uom
from .cxml.punchout import (CartItem, build_cancel, build_empty_cart,
                            build_punchout_order_message, render_return_form)
from .oci import inbound as oci_in, outbound as oci_out
from .http import (AUTOSUBMIT_CSP, MethodNotAllowed, Request, Response,
                   Router, html, parse_event, redirect, require_edge)
from .sessions import Session
from .ui.render import render
from .xml_safe import XmlRejected, parse


def _cart_item(sku: str, quantity: int) -> CartItem:
    """Turn a catalogue product plus a quantity into the cart line that
    crosses the wire.

    Two resolutions happen here and nowhere else: the price break is applied
    (cXML carries one UnitPrice and has no tier structure), and the unit of
    measure is normalised. The product's raw `uom` is what a sloppy supplier
    holds; the normalised value is what a conformant one sends — and for the
    deliberately-quirked lines those differ, which is the point."""
    product = BY_SKU[sku]
    uom, _ = normalise_uom(product.uom)
    return CartItem(
        supplier_part_id=product.sku,
        quantity=D(quantity),
        unit_price=product.price_for(quantity),
        description=product.description,
        short_name=product.name[:50],
        unit_of_measure=uom,
        classification=product.unspsc,
        currency=product.currency,
        supplier_part_auxiliary_id=product.aux_token,
        manufacturer_part_id=product.manufacturer_part_id,
        manufacturer_name=product.manufacturer,
        lead_time_days=product.lead_time_days,
    )

router = Router()


#: Marks a cart that exists only so an anonymous browser has somewhere to put
#: things. Never a punchout session: the templates key their punchout chrome
#: off `get_session` returning None, and a browse holder must never light it up.
BROWSE_PROTOCOL = "browse"


def _token(request: Request) -> Optional[str]:
    """Which punchout session this request belongs to.

    =====================================================================
    THE URL WINS. IT USED TO BE THE OTHER WAY ROUND, AND THAT COST AN HOUR
    =====================================================================
    `?session=` is a DELIBERATE ACT: a buyer system minted that token seconds
    ago and put it in the StartPage URL it just handed the shopper. The `pos`
    cookie is RESIDUE — left by some earlier visit, possibly to `/console`,
    possibly to a punchout that finished last Tuesday.

    Preferring the cookie meant a stale one shadowed the fresh token entirely.
    If the stale session had also expired, the fresh one was never even looked
    up: no banner, the cart went to the anonymous holder, and `/cart/return`
    answered "No punchout session" for a session that was alive and well.

    The person guaranteed to be carrying a stale `pos` cookie is the developer
    who has been clicking around the demo console — that is, the one person
    running a punchout in order to prove their integration works. It failed
    precisely for them, and only for them, which is the worst possible
    distribution for a bug."""
    return request.query.get("session") or request.cookies.get("pos")


def _cart_context(request: Request, *, create: bool = False):
    """Resolve who this browser is, for cart purposes. Memoised per request.

    Returns `(punchout_session, holder)`. `holder` is the Session whose `cart`
    dict should be mutated, and may be the punchout session itself, a browse
    holder, or None when there is no cart and none is needed yet.

    =====================================================================
    THIS REPLACED A MODULE-LEVEL DICT, AND THE BUG WAS NOT SUBTLE
    =====================================================================
    Anonymous carts used to live in `_ANONYMOUS_CART`, a dict at module scope.
    In Lambda that dict is shared by every request a warm container handles, so
    two strangers browsing at the same time **saw each other's carts** — and
    which stranger you got depended on which container answered. It is exactly
    the bug `sessions.py` was written to fix, reintroduced one layer up because
    an anonymous cart looked too unimportant to store.

    It is stored now. The cost is one write per cart mutation by a
    non-punchout visitor, which is the correct price for not leaking one
    person's shopping to another.

    `create=False` is what keeps that cheap: a read-only view of an empty cart
    mints nothing, so the ordinary case of somebody reading /docs writes no
    rows at all.
    """
    cached = getattr(request, "_cart_ctx", None)
    if cached is not None and not (create and cached[1] is None):
        return cached

    punchout = None
    token = _token(request)
    if token:
        found = sessions.store().get(token)
        if found is not None and found.protocol != BROWSE_PROTOCOL:
            punchout = found
        elif found is None:
            # A token that resolves to nothing is worth SAYING, whether it
            # came from the URL or from a cookie left by an earlier visit.
            # Falling silently back to anonymous browsing is what turned an
            # expired session into an hour of debugging: everything on screen
            # looked normal until the cart refused to go home.
            where = ("The punchout session in that link"
                     if request.query.get("session")
                     else "Your previous punchout session")
            request._session_notice = (
                f"{where} is no longer valid — it has either expired (they "
                "last an hour) or the cart was already returned. You are "
                "browsing anonymously now, so there is nowhere to send a cart "
                "back to. Start a new punchout from your buyer system.")

    holder = punchout
    if holder is None:
        browse_token = request.cookies.get("pab")
        if browse_token:
            existing = sessions.store().get(browse_token)
            if existing is not None and existing.protocol == BROWSE_PROTOCOL:
                holder = existing
        if holder is None and create:
            holder = Session(
                session_id=secrets.token_urlsafe(18),
                buyer_name="", protocol=BROWSE_PROTOCOL, return_url="")
            sessions.store().put(holder)
            _queue_cookie(
                request,
                f"pab={holder.session_id}; Path=/; HttpOnly; Secure; "
                "SameSite=Lax")

    # A StartPage URL carries ?session=…; every link on the storefront does
    # not. Without this the punchout session survived exactly one page view,
    # the banner vanished on the first click, and the cart return answered
    # "no punchout session" — the core flow, broken for real browsers and
    # invisible to /console, which sets the cookie itself.
    if punchout is not None and request.cookies.get("pos") != punchout.session_id:
        _queue_cookie(
            request,
            f"pos={punchout.session_id}; Path=/; HttpOnly; Secure; SameSite=Lax")

    request._cart_ctx = (punchout, holder)
    return punchout, holder


def _queue_cookie(request: Request, cookie: str) -> None:
    """Stash a cookie for the dispatcher to attach to whatever the route
    returns. Set centrally so a new route cannot forget to carry the session
    forward — the failure mode being fixed here was exactly that."""
    pending = getattr(request, "_pending_cookies", None)
    if pending is None:
        pending = []
        request._pending_cookies = pending
    if cookie not in pending:
        pending.append(cookie)


def session_notice(request: Request) -> str:
    """Anything the visitor should be told about their session state.

    Read after `get_session`, which is what populates it."""
    return getattr(request, "_session_notice", "")


def get_session(request: Request) -> tuple[Optional[Session], dict[str, int]]:
    """Resolve the punchout session, if any, plus the cart to operate on.

    Returns `(session, cart)` where `session` is None for anonymous browsing —
    the templates key their punchout chrome off exactly that. An expired or
    unknown token resolves to anonymous rather than raising: a user whose
    session timed out mid-shop should see the shop, not a stack trace."""
    punchout, holder = _cart_context(request)
    return punchout, (holder.cart if holder is not None else {})


def save_session(session: Session) -> None:
    sessions.store().put(session)


def _with_session(request: Request, mutate) -> Response:
    """Run `mutate(cart)` and persist the result if it belongs to a session.

    This exists because forgetting the write-back is invisible locally —
    `MemoryStore` returns the same object each time, so a mutation appears to
    stick on its own. Against DynamoDB it does not, and the bug looks exactly
    like the one this module was rewritten to fix. Making the write structural
    means no handler has to remember it."""
    _, holder = _cart_context(request, create=True)
    cart = holder.cart if holder is not None else {}
    response = mutate(cart)
    if holder is not None:
        holder.cart = cart
        save_session(holder)
    return response


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/")
def landing(request: Request) -> Response:
    """The one page a search engine sees first.

    It used to be a 303 into /shop, which is GATED — so every crawler's first
    and most important request landed on "Sign up to continue", and that is
    what the site looked like to anyone who had not already found it. A
    redirect into a wall is not a homepage."""
    session, cart = get_session(request)
    return html(render(
        "landing.html", nav="home", session=session, cart_count=len(cart),
        canonical="/", reference_pages=reference.PAGES))


@router.get("/reference")
def reference_index(request: Request) -> Response:
    session, cart = get_session(request)
    return html(render("reference_index.html", nav="reference",
                       session=session, cart_count=len(cart),
                       canonical="/reference", pages=reference.PAGES))


@router.get("/reference/{slug}")
def reference_page(request: Request) -> Response:
    slug = request.params.get("slug", "")
    page = reference.BY_SLUG.get(slug)
    body = reference.body(slug) if page else None
    if page is None or body is None:
        return Response(status=404, body="Not found", content_type="text/plain")
    session, cart = get_session(request)
    return html(render("reference_page.html", nav="reference", page=page,
                       body=body, session=session, cart_count=len(cart),
                       canonical=f"/reference/{slug}"))


@router.get("/samples")
def sample_index(request: Request) -> Response:
    session, cart = get_session(request)
    return html(render(
        "samples.html", nav="samples", session=session, cart_count=len(cart),
        canonical="/samples",
        canonical_samples=samples.CANONICAL,
        adversarial_samples=samples.ADVERSARIAL))


@router.get("/samples/{kind}")
def sample_document(request: Request) -> Response:
    """Served as XML so it opens in a browser and pipes into a file.

    Generated per request rather than cached: they are cheap to build, and a
    cached sample is one more thing that can be stale."""
    body = samples.build(request.params.get("kind", ""))
    if body is None:
        return Response(status=404, body="No such sample. See /samples.",
                        content_type="text/plain")
    return Response(body=body, content_type="text/xml; charset=utf-8")


@router.post("/api/signup")
def api_signup(request: Request) -> Response:
    return api.view_signup(request)


@router.post("/api/validate")
def api_validate(request: Request) -> Response:
    return api.view_validate(request)


@router.post("/api/ingest")
def api_ingest(request: Request) -> Response:
    return api.view_ingest(request)


@router.get("/ingest")
@router.post("/ingest")
def ingest_preview(request: Request) -> Response:
    """Apply each buyer platform's ingestion rules and show what survives.

    Open, and deliberately so. It answers the question `/validate` cannot —
    "my document is valid, so why did the price arrive wrong" — and that is
    the question somebody types into a search engine at eleven at night."""
    session, cart = get_session(request)
    document = ""
    lines: list = []
    rejected = ""

    if request.method == "POST":
        form = request.form()
        if form.get("source") == "cart" and cart:
            # Build the real cart document rather than a shortcut structure:
            # what gets analysed must be what would actually be sent.
            document = build_punchout_order_message(
                [_cart_item(sku, qty) for sku, qty in cart.items()
                 if sku in BY_SKU],
                buyer_cookie=session.buyer_cookie if session else "preview",
                payload_id=f"{secrets.token_hex(8)}@punchoutsandbox.com",
                timestamp=datetime.now(timezone.utc).astimezone(),
                from_identity="meridian-supply", to_identity="buyer",
                sender_identity="meridian-supply",
                operation_allowed="edit",
            ).decode("utf-8")
        else:
            document = (form.get("document") or "").strip()

        if not document:
            rejected = "Nothing to analyse — paste a document first."
        elif len(document.encode("utf-8")) > inspector.MAX_PASTE_BYTES:
            rejected = (f"That document is {len(document.encode()):,} bytes; "
                        f"this page accepts up to {inspector.MAX_PASTE_BYTES:,}.")
        else:
            try:
                lines = differ.extract_lines(parse(document.encode("utf-8")))
            except XmlRejected as exc:
                rejected = str(exc)
            if not lines and not rejected:
                rejected = ("No line items found. This page needs a document "
                            "with ItemIn or ItemOut elements — a "
                            "PunchOutOrderMessage or an OrderRequest.")

    results = [platforms.ingest(lines, profile.key)
               for profile in platforms.PROFILES] if lines else []

    return html(render(
        "ingest.html", nav="ingest", session=session, cart_count=len(cart),
        canonical="/ingest", document=document, results=results,
        profiles=platforms.BY_KEY, rejected=rejected,
        has_cart=bool(cart)))


def _verification_route(body: str):
    def route(request: Request) -> Response:
        # text/plain would satisfy Google too, but the file it asked for ends
        # in .html and serving what was asked for costs nothing.
        return Response(body=body + "\n", content_type="text/html; charset=utf-8")
    return route


# Registered from signup.SITE_VERIFICATION so the route table and the gate's
# open-path list cannot disagree — see the comment beside that dict.
for _path, _body in signup.SITE_VERIFICATION.items():
    router.add("GET", _path, _verification_route(_body))


@router.get("/robots.txt")
def robots(request: Request) -> Response:
    """Both this and /sitemap.xml previously fell through to the signup gate
    and answered 200 with an HTML page — a crawler asking for robots.txt got a
    signup form, which is worse than a clean 404.

    Gated paths are disallowed not to hide them but because a crawler that
    follows them indexes the gate under a dozen different URLs, and a site
    whose search results are all the same signup form looks like nothing."""
    site = os.environ.get("SITE_URL", "https://punchoutsandbox.com")
    lines = [
        "User-agent: *",
        "Allow: /",
        "",
        "# Gated — a crawler only ever sees the signup form behind these.",
        "Disallow: /shop",
        "Disallow: /cart",
        "Disallow: /orders",
        "Disallow: /settings",
        "Disallow: /sessions",
        "Disallow: /console",
        "Disallow: /product",
        "",
        "# Machine endpoints. Nothing to index and a POST is the only verb.",
        "Disallow: /punchout/setup",
        "Disallow: /oci/setup",
        "Disallow: /order",
        "",
        f"Sitemap: {site}/sitemap.xml",
        "",
    ]
    return Response(body="\n".join(lines), content_type="text/plain; charset=utf-8",
                    headers={"cache-control": "public, max-age=3600"})


@router.get("/sitemap.xml")
def sitemap(request: Request) -> Response:
    """Only the pages a crawler can actually read.

    Listing a gated URL here would be asking Google to index the signup form,
    which is the opposite of the point."""
    site = os.environ.get("SITE_URL", "https://punchoutsandbox.com")
    urls = ["/", "/docs", "/validate", "/ingest", "/reference",
            "/samples", "/signup", "/contact"]
    urls += [f"/reference/{page.slug}" for page in reference.PAGES]
    entries = "".join(
        # `priority` is ignored by Google and has been for years; it is
        # omitted rather than fabricated. So is `lastmod`, which would be a
        # guess — a wrong lastmod is worse than none, because it teaches a
        # crawler to distrust the file.
        f"<url><loc>{site}{path}</loc></url>" for path in urls)
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>")
    return Response(body=body, content_type="application/xml; charset=utf-8",
                    headers={"cache-control": "public, max-age=3600"})


@router.get("/shop")
@router.get("/shop/{category}")
def shop(request: Request) -> Response:
    session, cart = get_session(request)
    return storefront.view_shop(request, session=session, cart=cart,
                                notice=session_notice(request))


@router.get("/product/{sku}")
def product(request: Request) -> Response:
    session, cart = get_session(request)
    return storefront.view_product(request, session=session, cart=cart)


@router.get("/cart")
def cart(request: Request) -> Response:
    session, cart_state = get_session(request)
    return storefront.view_cart(request, session=session, cart=cart_state,
                                notice=session_notice(request))


@router.post("/cart/add")
def cart_add(request: Request) -> Response:
    return _with_session(
        request, lambda cart: storefront.add_to_cart(request, cart=cart))


@router.post("/cart/remove")
def cart_remove(request: Request) -> Response:
    return _with_session(
        request, lambda cart: storefront.remove_from_cart(request, cart=cart))


@router.post("/cart/return")
def cart_return(request: Request) -> Response:
    """Return the cart to the buyer as a browser form POST.

    Three outcomes, deliberately distinct because two of them are destructive
    in opposite directions on an `edit` operation — see
    `cxml/punchout.py`'s docstring. `mode` comes from which button the user
    pressed, never inferred from whether the cart happens to be empty."""
    session, cart_state = get_session(request)
    if session is None:
        return html(
            "<h1>No punchout session</h1><p>There is nowhere to return a cart "
            'to. Start one from the <a href="/console">console</a>.</p>',
            status=409,
        )

    form = request.form()
    mode = form.get("mode", "cart")
    encoding = form.get("encoding", "cxml-base64")

    if session.protocol == "OCI":
        # OCI has no cancel document and no status codes at all, so "empty"
        # and "cancel" collapse to the same thing: a form with no NEW_ITEM
        # fields. That is a real expressiveness gap, not an omission here.
        items = ([] if mode in ("empty", "cancel")
                 else [_oci_item(sku, qty) for sku, qty in cart_state.items()
                       if sku in BY_SKU])
        fields, advisories = oci_out.build_fields(items)
        page = oci_out.render_return_form(fields, hook_url=session.return_url)
        cart_state.clear()
        session.cart = {}
        save_session(session)
        # Relaxed CSP: this page auto-submits itself with one inline line.
        return html(page, headers={"content-security-policy": AUTOSUBMIT_CSP})
    common = dict(
        buyer_cookie=session.buyer_cookie,
        payload_id=f"{secrets.token_hex(8)}@punchoutsandbox.com",
        timestamp=datetime.now(timezone.utc).astimezone(),
        from_identity="meridian-supply", to_identity=session.buyer_name or "buyer",
        sender_identity="meridian-supply",
        operation_allowed="edit",
    )

    if mode == "cancel":
        document = build_cancel(**common)
    elif mode == "empty":
        document = build_empty_cart(**common)
    else:
        document = build_punchout_order_message(
            [_cart_item(sku, qty) for sku, qty in cart_state.items()
             if sku in BY_SKU],
            **common,
        )

    # Single-use: clearing AND PERSISTING means a back-button resubmit
    # cannot double the buyer's requisition. Clearing the local dict alone
    # would leave the cleared state unwritten and the cart replayable.
    cart_state.clear()
    session.cart = {}
    save_session(session)
    # Relaxed CSP: the cart return auto-submits itself to the buyer with one
    # inline line. Everything on the page is our own builder's output.
    return html(render_return_form(
        document, browser_form_post_url=session.return_url, encoding=encoding),
        headers={"content-security-policy": AUTOSUBMIT_CSP})


@router.get("/validate")
@router.post("/validate")
def validate_document(request: Request) -> Response:
    """The product's front door — and the only route that exercises `lxml`
    and the vendored DTDs, so it is also the deployment's liveness proof."""
    return inspector.view_validate(request)


def _oci_item(sku: str, quantity: int) -> "oci_out.OciItem":
    """Catalogue product -> OCI cart line.

    Note what has to change versus the cXML path. The unit is an ISO code
    capped at 3 characters, the description is capped at 40 and the full text
    moves to LONGTEXT, and PRICEUNIT is emitted explicitly. None of those
    limits exist in cXML, which is why one cart model cannot serve both
    protocols unchanged."""
    product = BY_SKU[sku]
    uom, _ = normalise_uom(product.uom)
    return oci_out.OciItem(
        description=product.name,
        quantity=D(quantity),
        unit=uom[:3],
        price=product.price_for(quantity),
        currency=product.currency,
        # The catalogue's pack_size IS an OCI price unit: a box of 100 priced
        # per box is PRICE=<box price>, PRICEUNIT=1 for the box as a unit —
        # but where a price is quoted per pack of N, PRICEUNIT carries N.
        price_unit=1,
        vendor_mat=product.sku,
        manufacturer_code=product.manufacturer[:10],
        manufacturer_mat=product.manufacturer_part_id,
        lead_time_days=product.lead_time_days,
        long_text=product.description,
        ext_product_id=product.sku,
        ext_category_id=product.unspsc,
        ext_schema_type="UNSPSC",
    )


def _oci_validate(callup) -> Response:
    """Answer FUNCTION=VALIDATE — SRM re-checking a line it already holds.

    The price is resolved AT THE SUPPLIED QUANTITY, which is the whole reason
    QUANTITY is passed: SAP added it in OCI 3.0 "so that the catalog can
    determine the correct price from a scale". A catalogue that ignores it and
    returns list price is why a requisition built from a template silently
    loses its volume discount."""
    if not callup.hook_url:
        return html(
            "<h1>No HOOK_URL</h1><p>VALIDATE returns product data, so it "
            "needs somewhere to return it to.</p>", status=400)

    product = BY_SKU.get(callup.product_id or "")
    if product is None:
        # The spec: "If the product no longer exists, the catalog is not
        # expected to return any data." Discontinuation is the case this
        # exists to express, and it is expressed by SILENCE.
        page, _ = oci_out.render_validate_response(
            None, hook_url=callup.hook_url,
            return_target=callup.return_target, charset=callup.charset)
        return html(page, headers={"content-security-policy": AUTOSUBMIT_CSP})

    try:
        quantity = int(float(callup.quantity)) if callup.quantity else 1
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)

    item = _oci_item(product.sku, quantity)
    page, _ = oci_out.render_validate_response(
        item, hook_url=callup.hook_url,
        return_target=callup.return_target, charset=callup.charset)
    return html(page, headers={"content-security-policy": AUTOSUBMIT_CSP})


def _authenticate_machine(request: Request):
    """Authenticate a buyer system by its issued credentials.

    cXML carries them in the header Credential blocks; OCI carries them as
    plain USERNAME/PASSWORD parameters. Both are checked against the identity
    the account was issued, and the secret is compared in constant time.

    Returns the Tenant or None. Note this parses the body WITHOUT the hardened
    XML front door for cXML — deliberately, it uses xml_safe.parse via
    setup_request, which is the only thing that touches untrusted XML."""
    if request.path == "/oci/setup":
        params = {**request.query}
        if request.method == "POST":
            params.update(request.form())
        lower = {k.lower(): v for k, v in params.items()}
        identity = lower.get("username", "")
        secret = lower.get("password", "")
    else:
        identity, secret = setup_request.extract_credentials(request.body)

    if not identity:
        return None
    tenant = tenants.store().by_sandbox_id(identity.strip())
    if tenant is None:
        return None
    if not tenants.verify_secret(secret, tenant.shared_secret):
        return None
    return tenant


def _oci_background_search(callup) -> Response:
    """Answer FUNCTION=BACKGROUND_SEARCH — SRM scraping us for a merged list.

    SRM runs this across every catalogue configured for Cross-Catalog Search
    and merges the hits into one list. We are not being browsed; we are being
    read once, mechanically, from the DOM we return."""
    if not callup.hook_url:
        return html(
            "<h1>No HOOK_URL</h1><p>Background search returns transferable "
            "items, so it needs a return address.</p>", status=400)

    term = (callup.search_string or "").strip()
    if not term:
        # An empty SEARCHSTRING is a caller bug, not a request for everything.
        # Returning the whole catalogue into SRM's merged list would be
        # actively hostile to the other catalogues in it.
        page, _ = oci_out.render_search_response(
            [], search_string="", hook_url=callup.hook_url,
            return_target=callup.return_target, charset=callup.charset)
        return html(page, headers={"content-security-policy": AUTOSUBMIT_CSP})

    matches = search(term)
    shown = matches[:oci_out.MAX_SEARCH_RESULTS]
    items = [_oci_item(p.sku, 1) for p in shown]

    page, _ = oci_out.render_search_response(
        items, search_string=term, hook_url=callup.hook_url,
        return_target=callup.return_target, charset=callup.charset,
        total_matches=len(matches))
    return html(page, headers={"content-security-policy": AUTOSUBMIT_CSP})


@router.get("/oci/setup")
@router.post("/oci/setup")
def oci_setup(request: Request) -> Response:
    """OCI call-up. Unlike cXML there is no handshake — the user's browser
    just arrives, so this responds with the storefront rather than a document.

    Accepts GET and POST because SRM Customizing decides which, and SAP's own
    example checks both."""
    callup = oci_in.parse_callup(
        query=request.query, form=request.form() if request.method == "POST" else {},
        method=request.method)

    # FUNCTION calls are answered before the HOOK_URL check, because DETAIL
    # legitimately carries no HOOK_URL: it returns no data at all, so there is
    # nothing to return anywhere.
    if callup.function == "DETAIL":
        # "With this function no data is transferred from the product catalog
        # to the SRM Server" — it is a pure human drill-down, so the only
        # correct response is to show the product page.
        product = BY_SKU.get(callup.product_id or "")
        if product is None:
            return html(
                "<h1>Product not found</h1><p>PRODUCTID "
                f"<code>{callup.product_id or '(missing)'}</code> does not "
                "match any EXT_PRODUCT_ID this catalogue has issued.</p>",
                status=404)
        return redirect(f"/product/{product.sku}")

    if callup.function == "VALIDATE":
        return _oci_validate(callup)

    if callup.function == "BACKGROUND_SEARCH":
        return _oci_background_search(callup)

    if not callup.hook_url:
        return html(
            "<h1>No HOOK_URL</h1><p>OCI has no other session mechanism, so "
            "there is nowhere to return a cart to. Your SRM configuration "
            "should send HOOK_URL as the return-URL parameter.</p>"
            '<p><a href="/docs">How to configure this</a></p>', status=400)

    session = Session(
        session_id=secrets.token_urlsafe(18),
        buyer_name=callup.username or "your SAP system",
        protocol="OCI",
        buyer_cookie="",          # OCI has no equivalent; HOOK_URL is the session
        return_url=callup.hook_url,
        operation="create",
    )
    save_session(session)
    return Response(
        status=303, body="",
        headers={"location": f"/shop?session={session.session_id}"},
        cookies=[f"pos={session.session_id}; Path=/; HttpOnly; Secure; SameSite=Lax"],
    )


@router.post("/punchout/setup")
def punchout_setup(request: Request) -> Response:
    """The machine-facing front door: a buyer system POSTs its
    PunchOutSetupRequest here and gets a StartPage URL back."""
    return setup_request.handle_setup(
        request, site_url=os.environ.get("SITE_URL", "https://punchoutsandbox.com"))


@router.get("/signup")
@router.post("/signup")
def signup_route(request: Request) -> Response:
    return signup.view_signup(request)


@router.get("/contact")
@router.post("/contact")
def contact_route(request: Request) -> Response:
    return contact.view_contact(request)


# --------------------------------------------------------------------------- #
# Orders — the fulfilment flow
# --------------------------------------------------------------------------- #
@router.post("/order")
def order_inbox(request: Request) -> Response:
    """The purchase-order inbox. Authenticated by the gate before it gets
    here, which is why the tenant lookup below cannot fail."""
    tenant = _authenticate_machine(request)
    return order_request.handle_order(
        request, tenant,
        site_url=os.environ.get("SITE_URL", "https://punchoutsandbox.com"))


def _order_or_404(request: Request):
    """Load an order, scoped to the signed-in account.

    Scoping is not decoration: order refs appear in URLs, and a lookup by ref
    alone would let any account read any other account's purchase orders by
    guessing one. The tenant id is part of the partition key precisely so that
    this is structural rather than a check someone can forget."""
    tenant = signup.current_tenant(request)
    if tenant is None:
        return None, None
    record = orders.store().get(tenant.tenant_id, request.params.get("ref", ""))
    return tenant, record


@router.get("/orders")
def order_list(request: Request) -> Response:
    tenant = signup.current_tenant(request)
    recent = orders.store().recent(tenant.tenant_id) if tenant else []
    return html(render("orders.html", nav="orders", orders=recent,
                       tenant=tenant,
                       site_url=os.environ.get("SITE_URL",
                                               "https://punchoutsandbox.com")))


@router.get("/orders/{ref}")
def order_detail(request: Request) -> Response:
    tenant, record = _order_or_404(request)
    if record is None:
        return html(render("order_missing.html", nav="orders"), status=404)
    parsed = orderflow.order_from_record(record)
    return html(render("order.html", nav="orders", order=record,
                       parsed=parsed, tenant=tenant,
                       endpoint=tenant.buyer_endpoint,
                       jurisdictions=orderflow.rates.JURISDICTIONS,
                       header_types=orderflow.HEADER_TYPES_FOR_UI,
                       problems=request.query.get("problems", ""),
                       highlight=request.query.get("doc", "")))


def _generate(request: Request, kind: str) -> Response:
    tenant, record = _order_or_404(request)
    if record is None:
        return html(render("order_missing.html", nav="orders"), status=404)

    form = request.form()
    common = {"shared_secret": tenant.shared_secret,
              "buyer_identity": record.buyer_identity or "buyer"}

    if kind == "confirmation":
        document, problems = orderflow.build_confirmation_document(
            record, header_type=form.get("header_type", "accept"), **common)
    elif kind == "shipnotice":
        document, problems = orderflow.build_ship_notice_document(
            record, carrier_code=form.get("carrier", "UPSN") or "UPSN",
            service_level=form.get("service_level", "Ground") or "Ground",
            **common)
    else:
        document, problems, _ = orderflow.build_invoice_document(
            record, buyer_country=(form.get("country") or "").upper() or None,
            **common)

    if problems:
        # Reported through a redirect rather than rendered inline so that a
        # refresh does not re-submit the generation. The problems are short by
        # construction — they are rule violations, not stack traces.
        return redirect(f"/orders/{record.ref}"
                        f"?problems={quote(' | '.join(problems))}")

    orders.store().add_document(record, document)
    return redirect(f"/orders/{record.ref}?doc={document.doc_id}")


@router.post("/orders/{ref}/confirm")
def order_confirm(request: Request) -> Response:
    return _generate(request, "confirmation")


@router.post("/orders/{ref}/ship")
def order_ship(request: Request) -> Response:
    return _generate(request, "shipnotice")


@router.post("/orders/{ref}/invoice")
def order_invoice(request: Request) -> Response:
    return _generate(request, "invoice")


@router.post("/orders/{ref}/send/{doc_id}")
def order_send(request: Request) -> Response:
    tenant, record = _order_or_404(request)
    if record is None:
        return html(render("order_missing.html", nav="orders"), status=404)
    document = record.document(request.params.get("doc_id", ""))
    if document is None:
        return redirect(f"/orders/{record.ref}")

    endpoint = (request.form().get("endpoint") or tenant.buyer_endpoint).strip()
    if not endpoint:
        return redirect(f"/orders/{record.ref}?problems="
                        + quote("No endpoint configured. Set one in Settings, "
                                "or type one on the send form."))

    orderflow.send(record, document, endpoint)
    return redirect(f"/orders/{record.ref}?doc={document.doc_id}")


@router.get("/orders/{ref}/doc/{doc_id}")
def order_document(request: Request) -> Response:
    _, record = _order_or_404(request)
    if record is None:
        return Response(status=404, body="Not found", content_type="text/plain")
    document = record.document(request.params.get("doc_id", ""))
    if document is None:
        return Response(status=404, body="Not found", content_type="text/plain")
    # Served as XML rather than a download so it opens in the browser — the
    # common next action is copying a fragment of it into a bug report.
    return Response(status=200, body=document.xml,
                    content_type="text/xml; charset=utf-8")


@router.get("/orders/{ref}/source")
def order_source(request: Request) -> Response:
    _, record = _order_or_404(request)
    if record is None:
        return Response(status=404, body="Not found", content_type="text/plain")
    return Response(status=200, body=record.raw,
                    content_type="text/xml; charset=utf-8")


# --------------------------------------------------------------------------- #
# Sessions and settings
# --------------------------------------------------------------------------- #
@router.get("/sessions")
def session_list(request: Request) -> Response:
    """Punchout sessions this sandbox has open.

    The nav has linked here since the first version of the storefront and the
    route did not exist, so it 404ed. Sessions are global rather than
    per-account because a session is created by a machine POST that may well
    authenticate as a different account than the browser is signed in as —
    scoping them would hide exactly the session someone is trying to debug.
    Nothing in a session is private: a buyer name, a cart of invented
    products, and a return URL the buyer themselves published."""
    session, cart = get_session(request)
    return html(render("sessions.html", nav="sessions",
                       sessions=sessions.store().recent(), session=session,
                       cart_count=len(cart)))


@router.get("/settings")
@router.post("/settings")
def settings(request: Request) -> Response:
    tenant = signup.current_tenant(request)
    error = ""
    saved = False

    if request.method == "POST":
        endpoint = (request.form().get("endpoint") or "").strip()
        if not endpoint:
            tenant.buyer_endpoint = ""
            tenants.store().put(tenant)
            saved = True
        else:
            try:
                # Checked NOW rather than at send time, so a URL that this
                # sandbox will refuse is refused while the person who typed it
                # is still looking at the field.
                delivery.vet_url(endpoint)
                tenant.buyer_endpoint = endpoint
                tenants.store().put(tenant)
                saved = True
            except delivery.DeliveryRefused as refusal:
                error = str(refusal)

    return html(render("settings.html", nav="settings", tenant=tenant,
                       error=error, saved=saved,
                       endpoint=tenant.buyer_endpoint,
                       site_url=os.environ.get("SITE_URL",
                                               "https://punchoutsandbox.com")))


@router.get("/docs")
def docs(request: Request) -> Response:
    session, cart = get_session(request)
    return html(render(
        "docs.html", nav="docs", session=session, cart_count=len(cart),
        canonical="/docs",
        site_url=os.environ.get("SITE_URL", "https://punchoutsandbox.com")))


@router.get("/console")
def console(request: Request) -> Response:
    """Mint a demo session so the storefront can be seen in punchout mode
    without standing up a buyer system first."""
    session = Session(
        session_id=secrets.token_urlsafe(16),
        buyer_name="Northgate Industries Ltd",
        protocol="cXML",
        buyer_cookie=secrets.token_urlsafe(24),
        return_url="https://buyer.example.com/punchout/return",
    )
    save_session(session)
    return Response(
        status=303, body="", headers={"location": "/shop"},
        # Lax rather than None: this cookie is only read on same-site
        # navigation within the storefront. The CART RETURN is a cross-site
        # POST and must not depend on it — Chrome 80's SameSite default is a
        # documented cause of punchout carts silently failing to return.
        cookies=[f"pos={session.session_id}; Path=/; HttpOnly; Secure; SameSite=Lax"],
    )


@router.get("/static/{name}")
def static(request: Request) -> Response:
    name = request.params.get("name", "")
    if "/" in name or ".." in name:
        return Response(status=404, body="Not found", content_type="text/plain")
    path = os.path.join(os.path.dirname(__file__), "ui", "static", name)
    if not os.path.exists(path):
        return Response(status=404, body="Not found", content_type="text/plain")
    with open(path, "rb") as handle:
        body = handle.read()
    # An SVG served as application/octet-stream is downloaded rather than
    # rendered, so the favicon never appeared. Mapped explicitly rather than
    # guessed, and anything unrecognised stays a download — a static directory
    # that infers content types from filenames is one upload away from serving
    # something as text/html.
    kind = {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }.get(os.path.splitext(name)[1], "application/octet-stream")
    return Response(body=body, content_type=kind,
                    headers={"cache-control": "public, max-age=300"})


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def handler(event: dict, context=None) -> dict:
    request = parse_event(event)

    refusal = require_edge(request)
    if refusal is not None:
        return refusal.to_lambda()

    # /validate is open but NOT unlimited — see signup.py on why it was
    # un-gated, and tenants.py on the three layers that replaced the gate.
    # A signed-in visitor is metered by their account below instead, so the
    # anonymous counter only ever applies to someone with no account.
    if request.path == "/validate" and request.method == "POST":
        # Credentials count as identity here, not just a browser cookie.
        # Metering a client that holds valid issued credentials as an
        # anonymous stranger is what made the cap look "keyed on address, not
        # identity" — because it was.
        if (signup.current_tenant(request) is None
                and api.authenticate(request) is None):
            allowed, remaining = tenants.anon_check_quota(
                tenants.client_ip(request.headers), today=signup.today())
            if not allowed:
                # The ONLY way we learn that 25/day is the wrong number. It is
                # an admitted guess (tenants.ANON_DAILY_QUOTA), and a limit
                # nobody is told about is a limit nobody can tune — the
                # visitor sees a page and leaves. The log line is the record;
                # the email is so it does not need to be gone looking for.
                ip = tenants.client_ip(request.headers)
                telemetry.event("anon_quota_exhausted",
                                ip=telemetry.ip_tag(ip),
                                limit=tenants.ANON_DAILY_QUOTA)
                if tenants.alert_budget(today=signup.today()):
                    mailer.send(
                        subject="PunchOut Sandbox: anonymous validation limit hit",
                        body=(
                            f"A visitor reached the {tenants.ANON_DAILY_QUOTA}"
                            "/day anonymous limit on /validate.\n\n"
                            f"Source (hashed): {telemetry.ip_tag(ip)}\n"
                            f"Date           : {signup.today()}\n\n"
                            "If this keeps happening to the same tag, it is one "
                            "person doing real work and the number is too low. "
                            "If it is a different tag every time, it is a "
                            "scraper and the number is about right.\n\n"
                            "Change it in app/tenants.py (ANON_DAILY_QUOTA).\n"
                            "At most "
                            f"{tenants.ALERT_DAILY_LIMIT} of these are sent a day.\n"
                        ),
                        kind="quota-alert",
                    )
                return html(render(
                    "gate.html", nav="signup", wanted="/validate",
                    rate_limited=True), status=429).to_lambda()

    # THE GATE. Applied here rather than per-route on purpose: a new route
    # added later is gated by default, and forgetting to gate it is not
    # possible. Opting a path OUT is a deliberate edit to signup.OPEN_PATHS.
    if not signup.is_open(request.path):
        tenant = signup.current_tenant(request)

        # =================================================================
        # A LIVE PUNCHOUT SESSION IS AUTHORISATION. THIS WAS A REAL OUTAGE.
        # =================================================================
        # The shopper who follows a StartPage URL is an employee of the BUYER.
        # They have never seen this site and have no account here — their
        # procurement system authenticated on their behalf, seconds earlier,
        # with issued credentials. Demanding a signup from them put a form in
        # the middle of somebody else's punchout: the product's actual flow,
        # broken for every real end user.
        #
        # It survived a full QA pass because the QA client signs up first and
        # therefore always carries an account cookie. No browser test that
        # begins by signing up can ever see this.
        #
        # Storefront paths only. /orders and /settings stay account-scoped:
        # they show data belonging to an account, and a punchout session is
        # not one.
        if tenant is None and signup.storefront_path(request.path):
            punchout, _ = _cart_context(request)
            if punchout is not None:
                route = router.resolve(request)
                if route is not None:
                    response = route(request)
                    pending = getattr(request, "_pending_cookies", None)
                    if pending:
                        response.cookies = list(response.cookies) + [
                            c for c in pending if c not in response.cookies]
                    return response.to_lambda()

        if tenant is None:
            # The machine endpoints authenticate with issued credentials
            # instead of a cookie — a buyer system cannot fill in a form.
            if request.path in ("/punchout/setup", "/oci/setup", "/order"):
                tenant = _authenticate_machine(request)
            if tenant is None:
                if request.path in ("/punchout/setup", "/order"):
                    return setup_request.unauthorised_response().to_lambda()
                if request.path == "/oci/setup":
                    return html(
                        "<h1>Credentials required</h1><p>Send your sandbox "
                        "identity and secret as OCI <code>USERNAME</code> and "
                        "<code>PASSWORD</code>. Get them free at "
                        '<a href="/signup">/signup</a>.</p>',
                        status=401).to_lambda()
                return signup.gate_response(request).to_lambda()

        allowed, _ = tenant.check_quota(today=signup.today())
        tenants.store().put(tenant)
        if not allowed:
            return html(
                "<h1>Daily limit reached</h1><p>This account has used its "
                f"{tenants.DAILY_QUOTA} operations for today. It resets at "
                "midnight UTC. If you are hitting this legitimately, say so — "
                "it is a number, not a policy.</p>", status=429).to_lambda()

    try:
        route = router.resolve(request)
    except MethodNotAllowed:
        return Response(status=405, body="Method not allowed",
                        content_type="text/plain").to_lambda()

    if route is None:
        return Response(status=404, body="Not found",
                        content_type="text/plain").to_lambda()

    response = route(request)
    # Cookies queued by _cart_context are attached here rather than by each
    # route, so a route added later carries the punchout session forward
    # without its author having to know that it must.
    pending = getattr(request, "_pending_cookies", None)
    if pending:
        response.cookies = list(response.cookies) + [
            c for c in pending if c not in response.cookies]
    return response.to_lambda()


if __name__ == "__main__":  # pragma: no cover - local dev only
    import base64
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    class Dev(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            event = {
                "requestContext": {"http": {"method": method, "path": parsed.path}},
                "queryStringParameters": {
                    k: v[0] for k, v in parse_qs(parsed.query).items()
                },
                "headers": dict(self.headers),
                "cookies": [c.strip() for c in
                            (self.headers.get("cookie") or "").split(";") if c.strip()],
                "body": base64.b64encode(body).decode(),
                "isBase64Encoded": True,
            }
            result = handler(event)
            payload = result["body"]
            raw = (base64.b64decode(payload) if result.get("isBase64Encoded")
                   else payload.encode())
            self.send_response(result["statusCode"])
            for key, value in result.get("headers", {}).items():
                self.send_header(key, value)
            for cookie in result.get("cookies", []):
                self.send_header("set-cookie", cookie)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self): self._serve("GET")
        def do_POST(self): self._serve("POST")
        def log_message(self, *args): pass

    print("PunchOut Sandbox dev server -> http://127.0.0.1:8000/shop")
    HTTPServer(("127.0.0.1", 8000), Dev).serve_forever()
