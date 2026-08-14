"""Request/response plumbing for the Lambda Function URL.

*No web framework. The whole surface is a dozen routes over a Function URL
payload, and Flask or FastAPI would add tens of megabytes to the image and a
routing DSL to learn for something a dict and a regex cover. See
`infra/sandbox/site_stack.py` for why this runs as a container at all.*

=============================================================================
THE EDGE SECRET, AND ITS HONEST LIMITS
=============================================================================
A Lambda Function URL with `auth_type=NONE` is reachable by anyone who learns
its `.on.aws` hostname, which would route straight past every Cloudflare
control in front of it — the rate limiting that is our entire abuse defence
(BRIEF.md §3).

`EDGE_SHARED_SECRET` closes that: a Cloudflare Transform Rule injects
`X-Edge-Secret` on every proxied request and `require_edge()` refuses anything
without it.

Two properties of this worth stating plainly rather than discovering later:

1. **Unset means OFF.** A freshly deployed stack has no Cloudflare rule in
   front of it yet, so enforcing would take the site down before it ever came
   up. `deploy_cloudflare_dns.py` sets the rule first and the Lambda variable
   last, in that order, for exactly this reason.
2. **It is a bearer header over TLS, not a signature.** It stops casual direct
   hits on the origin. It does not survive anyone who has already seen a
   legitimate request's headers. For a free sandbox serving synthetic data
   about invented companies that is the right amount of security — and it is
   emphatically not a pattern to carry into Xenia.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    cookies: dict[str, str] = field(default_factory=dict)
    #: Values captured from the route pattern, e.g. {"sku": "MSC-1001"}.
    params: dict[str, str] = field(default_factory=dict)

    def form(self) -> dict[str, str]:
        """Parse an `application/x-www-form-urlencoded` body.

        `keep_blank_values` matters: a cleared quantity field posts as empty
        and the handler needs to see it rather than have it vanish."""
        parsed = parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}


@dataclass
class Response:
    status: int = 200
    body: str | bytes = ""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)

    def to_lambda(self) -> dict:
        is_binary = isinstance(self.body, bytes)
        body = (base64.b64encode(self.body).decode() if is_binary else self.body)
        headers = {"content-type": self.content_type}
        # Defaults first so an explicit header on the Response wins — that is
        # how the auto-submit pages relax their own CSP without a special case
        # here.
        for key, value in SECURITY_HEADERS.items():
            headers.setdefault(key, value)
        headers.update({k.lower(): v for k, v in self.headers.items()})
        out = {
            "statusCode": self.status,
            "headers": headers,
            "body": body,
            "isBase64Encoded": is_binary,
        }
        if self.cookies:
            out["cookies"] = self.cookies
        return out


#: Applied to every response that does not set them itself.
#:
#: =========================================================================
#: THE TWO HEADERS DELIBERATELY NOT HERE
#: =========================================================================
#: **`X-Frame-Options` / `frame-ancestors`.** Blocking framing is the reflex,
#: and it would break the product: some buyer platforms open a punchout
#: catalogue in an IFRAME rather than a new window, and a supplier that
#: refuses to be framed simply does not work for those buyers. There is
#: nothing here worth clickjacking — a cart of invented products — so the
#: trade is not close.
#:
#: **A restrictive `form-action`.** The whole point of the cart return is a
#: cross-origin POST to whatever URL the buyer published, so `form-action`
#: must permit any https target. It still forbids http, which is worth having.
#:
#: `Referrer-Policy: no-referrer` rather than the usual
#: `strict-origin-when-cross-origin`, because a StartPage URL carries the
#: session token in its query string and the storefront links to third-party
#: sites. Origin-only would still be safe, but no-referrer costs nothing here.
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'none'; "
        # Inline style attributes are used throughout the templates; inline
        # SCRIPT is not, anywhere, which is what makes `script-src 'none'`
        # affordable. The two auto-submit bounce pages override this header
        # explicitly — see `handler.py`.
        "script-src 'none'; "
        "form-action 'self' https:; "
        "base-uri 'none'"
    ),
}

#: For the two pages that must run a one-line auto-submit: the cXML cart
#: return and the OCI VALIDATE/BACKGROUND_SEARCH responses. Their entire
#: content is generated by our own builders from our own catalogue, so the
#: inline script is ours by construction — but it is still a relaxation, and
#: it is confined to the pages that need it rather than granted globally.
AUTOSUBMIT_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; script-src 'unsafe-inline'; "
    "form-action https:; base-uri 'none'"
)


def html(body: str, status: int = 200, **kw) -> Response:
    return Response(status=status, body=body, **kw)


def redirect(location: str, *, cookies: Optional[list[str]] = None) -> Response:
    return Response(status=303, body="", headers={"location": location},
                    cookies=cookies or [])


def json_response(payload: dict, status: int = 200) -> Response:
    return Response(status=status, body=json.dumps(payload, indent=2),
                    content_type="application/json; charset=utf-8")


class Router:
    """Regex routing. Patterns use `{name}` for a path segment."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern, Callable]] = []

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        regex = re.compile(
            "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "/?$"
        )
        self._routes.append((method.upper(), regex, handler))

    def get(self, pattern):
        return lambda fn: (self.add("GET", pattern, fn), fn)[1]

    def post(self, pattern):
        return lambda fn: (self.add("POST", pattern, fn), fn)[1]

    def resolve(self, request: Request) -> Optional[Callable]:
        matched_path = False
        for method, regex, handler in self._routes:
            match = regex.match(request.path)
            if not match:
                continue
            matched_path = True
            if method == request.method:
                request.params = {k: unquote(v) for k, v in match.groupdict().items()}
                return handler
        # A path that exists under a different verb is a 405, not a 404. The
        # distinction matters here because the punchout return endpoint is
        # POST-only and a supplier debugging it with a browser GET should be
        # told that, not told the endpoint does not exist.
        if matched_path:
            raise MethodNotAllowed(request.path)
        return None


class MethodNotAllowed(Exception):
    pass


def require_edge(request: Request) -> Optional[Response]:
    """Refuse requests that did not arrive through Cloudflare.

    Returns None to allow, or a Response to refuse. Unset secret means the
    check is off — see the module docstring."""
    expected = os.environ.get("EDGE_SHARED_SECRET")
    if not expected:
        return None
    presented = request.headers.get("x-edge-secret")
    if presented and hmac.compare_digest(presented, expected):
        return None
    # Deliberately terse and deliberately not 403-with-explanation: someone
    # probing the origin directly learns nothing about why they were refused.
    return Response(status=404, body="Not found", content_type="text/plain")


def parse_event(event: dict) -> Request:
    """Normalise a Lambda Function URL v2 event into a `Request`."""
    ctx = event.get("requestContext", {}).get("http", {})
    raw_body = event.get("body") or ""
    body = (base64.b64decode(raw_body) if event.get("isBase64Encoded")
            else raw_body.encode("utf-8"))

    cookies: dict[str, str] = {}
    for entry in event.get("cookies", []) or []:
        name, _, value = entry.partition("=")
        cookies[name.strip()] = value

    return Request(
        method=ctx.get("method", "GET").upper(),
        path=ctx.get("path", "/") or "/",
        query={k: v for k, v in (event.get("queryStringParameters") or {}).items()},
        # Header names arrive lowercased from the Function URL, but not from
        # every local test harness, so normalise rather than assume.
        headers={k.lower(): v for k, v in (event.get("headers") or {}).items()},
        body=body,
        cookies=cookies,
    )
