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

from . import inspector, sessions, storefront
from .catalogue.data import BY_SKU
from .catalogue.taxonomy import normalise_uom
from .cxml.punchout import (CartItem, build_cancel, build_empty_cart,
                            build_punchout_order_message, render_return_form)
from .http import (MethodNotAllowed, Request, Response, Router, html,
                   parse_event, redirect, require_edge)
from .sessions import Session


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
