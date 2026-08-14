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

from . import inspector, sessions, setup_request, storefront
from .catalogue.data import BY_SKU, search
from .catalogue.taxonomy import normalise_uom
from .cxml.punchout import (CartItem, build_cancel, build_empty_cart,
                            build_punchout_order_message, render_return_form)
from .oci import inbound as oci_in, outbound as oci_out
from .http import (MethodNotAllowed, Request, Response, Router, html,
                   parse_event, redirect, require_edge)
from .sessions import Session
from .ui.render import render


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


#: Browsing without a punchout handshake still needs somewhere to put a cart.
#: Anonymous carts are per-container and disposable ON PURPOSE — there is
#: nowhere to return them to, so persisting them would cost storage for no
#: benefit. Only real punchout sessions are worth a write.
_ANONYMOUS_CART: dict[str, int] = {}


def _token(request: Request) -> Optional[str]:
    return request.cookies.get("pos") or request.query.get("session")


def get_session(request: Request) -> tuple[Optional[Session], dict[str, int]]:
    """Resolve the punchout session, if any, plus the cart to operate on.

    Returns `(session, cart)` where `session` is None for anonymous browsing —
    the templates key their punchout chrome off exactly that. An expired or
    unknown token resolves to anonymous rather than raising: a user whose
    session timed out mid-shop should see the shop, not a stack trace."""
    token = _token(request)
    if token:
        found = sessions.store().get(token)
        if found is not None:
            return found, found.cart
    return None, _ANONYMOUS_CART


def save_session(session: Session) -> None:
    sessions.store().put(session)


def _with_session(request: Request, mutate) -> Response:
    """Run `mutate(cart)` and persist the result if it belongs to a session.

    This exists because forgetting the write-back is invisible locally —
    `MemoryStore` returns the same object each time, so a mutation appears to
    stick on its own. Against DynamoDB it does not, and the bug looks exactly
    like the one this module was rewritten to fix. Making the write structural
    means no handler has to remember it."""
    session, cart = get_session(request)
    response = mutate(cart)
    if session is not None:
        session.cart = cart
        save_session(session)
    return response


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/")
def home(request: Request) -> Response:
    return redirect("/shop")


@router.get("/shop")
@router.get("/shop/{category}")
def shop(request: Request) -> Response:
    session, cart = get_session(request)
    return storefront.view_shop(request, session=session, cart=cart)


@router.get("/product/{sku}")
def product(request: Request) -> Response:
    session, cart = get_session(request)
    return storefront.view_product(request, session=session, cart=cart)


@router.get("/cart")
def cart(request: Request) -> Response:
    session, cart_state = get_session(request)
    return storefront.view_cart(request, session=session, cart=cart_state)


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
        return html(page)
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
    return html(render_return_form(
        document, browser_form_post_url=session.return_url, encoding=encoding))


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
        return html(page)

    try:
        quantity = int(float(callup.quantity)) if callup.quantity else 1
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)

    item = _oci_item(product.sku, quantity)
    page, _ = oci_out.render_validate_response(
        item, hook_url=callup.hook_url,
        return_target=callup.return_target, charset=callup.charset)
    return html(page)


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
        return html(page)

    matches = search(term)
    shown = matches[:oci_out.MAX_SEARCH_RESULTS]
    items = [_oci_item(p.sku, 1) for p in shown]

    page, _ = oci_out.render_search_response(
        items, search_string=term, hook_url=callup.hook_url,
        return_target=callup.return_target, charset=callup.charset,
        total_matches=len(matches))
    return html(page)


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
        cookies=[f"pos={session.session_id}; Path=/; HttpOnly; SameSite=Lax"],
    )


@router.post("/punchout/setup")
def punchout_setup(request: Request) -> Response:
    """The machine-facing front door: a buyer system POSTs its
    PunchOutSetupRequest here and gets a StartPage URL back."""
    return setup_request.handle_setup(
        request, site_url=os.environ.get("SITE_URL", "https://punchoutsandbox.com"))


@router.get("/docs")
def docs(request: Request) -> Response:
    session, cart = get_session(request)
    return html(render(
        "docs.html", nav="docs", session=session, cart_count=len(cart),
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
        cookies=[f"pos={session.session_id}; Path=/; HttpOnly; SameSite=Lax"],
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
    kind = "text/css" if name.endswith(".css") else "application/octet-stream"
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

    try:
        route = router.resolve(request)
    except MethodNotAllowed:
        return Response(status=405, body="Method not allowed",
                        content_type="text/plain").to_lambda()

    if route is None:
        return Response(status=404, body="Not found",
                        content_type="text/plain").to_lambda()

    return route(request).to_lambda()


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
