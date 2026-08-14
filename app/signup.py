"""Signup — the gate, and the credentials it issues.

*See `tenants.py` for why a free tool has a gate at all, and why the machine
endpoints authenticate with issued credentials rather than a browser session.*

=============================================================================
WHAT IS AND IS NOT BEHIND THE GATE
=============================================================================
Open, always:

  /            the landing redirect
  /docs        you must be able to read what this is before handing over an
               email. A gate in front of the explanation would be a gate in
               front of the reason to sign up.
  /signup      obviously
  /static/*    stylesheet

Gated:

  the storefront, cart, /validate, and the machine endpoints.

`/validate` is the debatable one. It is the single most useful thing here for
a stranger, and gating it costs some goodwill. It is gated anyway because it
is the CPU-bound path — `lxml` against a 400KB DTD — and therefore the actual
abuse surface. An ungated validator is a free CPU cycle generator with a nice
UI.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

from .http import Request, Response, html
from .tenants import Tenant, store, valid_email
from .ui.render import render

#: Paths reachable with no account. Prefix match; see `is_open`.
OPEN_PATHS = ("/docs", "/signup", "/static/", "/favicon.ico")


def is_open(path: str) -> bool:
    if path == "/":
        return True
    return any(path == p.rstrip("/") or path.startswith(p) for p in OPEN_PATHS)


def current_tenant(request: Request) -> Optional[Tenant]:
    token = request.cookies.get("pst")
    return store().get(token) if token else None


def view_signup(request: Request) -> Response:
    if request.method == "GET":
        existing = current_tenant(request)
        if existing is not None:
            # Already signed up — show the credentials rather than a form.
            # Someone who lost the tab needs to find their shared secret
            # again, and re-issuing it would break their configured system.
            return html(render("welcome.html", nav="signup", tenant=existing,
                               returning=True))
        return html(render("signup.html", nav="signup", error=None,
                           email="", company=""))

    form = request.form()
    email = (form.get("email") or "").strip()
    company = (form.get("company") or "").strip()[:120]

    if not valid_email(email):
        return html(render("signup.html", nav="signup", email=email,
                           company=company,
                           error="That does not look like an email address."),
                    status=400)

    tenant = Tenant(tenant_id=secrets.token_urlsafe(18), email=email,
                    company=company)
    store().put(tenant)

    return Response(
        status=200,
        body=render("welcome.html", nav="signup", tenant=tenant,
                    returning=False),
        # A year, matching the account TTL. Not HttpOnly-exempt and not
        # Secure-exempt: this rides the same Cloudflare TLS as everything else.
        cookies=[f"pst={tenant.tenant_id}; Path=/; HttpOnly; SameSite=Lax; "
                 f"Max-Age={365 * 24 * 3600}"],
    )


def gate_response(request: Request) -> Response:
    """What an ungated visitor gets.

    A 200 with an explanation rather than a 401 or a redirect. A redirect
    would lose the page they wanted, and a 401 would prompt for HTTP basic
    auth in some browsers — neither is what a person who has simply not
    signed up yet should meet."""
    return html(render("gate.html", nav="signup",
                       wanted=request.path), status=200)


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())
