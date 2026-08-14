"""Lambda entry point and route table.

`CMD ["app.handler.handler"]` in the Dockerfile points here.

Session state lives in memory for now (`_SESSIONS`), which is correct for a
single-container dev run and WRONG for Lambda, where every cold start gets a
fresh dict and concurrent invocations do not share one. The DynamoDB table is
already provisioned in `infra/sandbox/data_stack.py` and `_SESSIONS` is
deliberately isolated behind `get_session`/`save_session` so swapping the
backing store is a two-function change rather than a hunt. Until that swap
happens, a punchout session will appear to vanish whenever Lambda scales —
which is a real bug, not a shortcut, and it is recorded as one.

Running locally:  python -m app.handler
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from typing import Optional

from . import storefront
from .http import (MethodNotAllowed, Request, Response, Router, html,
                   parse_event, redirect, require_edge)

router = Router()


@dataclass
class Session:
    """A punchout session. `buyer_cookie` is the capability that binds a cart
    return to the requisition that started it."""

    session_id: str
    buyer_name: str = "your procurement system"
    protocol: str = "cXML"
    buyer_cookie: str = ""
    return_url: str = ""
    operation: str = "create"
    cart: dict[str, int] = field(default_factory=dict)

    @property
    def return_url_display(self) -> str:
        return self.return_url or "(not set)"


_SESSIONS: dict[str, Session] = {}
#: Browsing without a punchout handshake still needs somewhere to put a cart.
_ANONYMOUS = Session(session_id="anonymous", buyer_name="")


def get_session(request: Request) -> tuple[Optional[Session], dict[str, int]]:
    """Resolve the punchout session, if any, plus the cart to operate on.

    Returns `(session, cart)` where `session` is None for anonymous browsing —
    the templates key their punchout chrome off exactly that."""
    token = request.cookies.get("pos") or request.query.get("session")
    if token and token in _SESSIONS:
        found = _SESSIONS[token]
        return found, found.cart
    return None, _ANONYMOUS.cart


def save_session(session: Session) -> None:
    _SESSIONS[session.session_id] = session


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
    _, cart_state = get_session(request)
    return storefront.add_to_cart(request, cart=cart_state)


@router.post("/cart/remove")
def cart_remove(request: Request) -> Response:
    _, cart_state = get_session(request)
    return storefront.remove_from_cart(request, cart=cart_state)


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
