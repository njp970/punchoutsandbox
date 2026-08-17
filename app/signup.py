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
  /validate    see below
  /contact     a gate in front of "this tool is broken" would mean only
               people who already signed up could report that signing up
               is broken.
  /reference   the published field-limit reference. Gating it would gate the
               only content anybody searches for, which is the entire reason
               a stranger ever arrives here.
  /robots.txt  and /sitemap.xml. Both used to fall through to the gate and
  /sitemap.xml answer 200 with an HTML signup form.

Gated:

  the storefront, the cart, and the machine endpoints.

**`/validate` was gated and is now open**, and the reasoning is worth keeping
because it reversed. It is the CPU-bound path — `lxml` against a 400KB DTD —
so it is genuinely the abuse surface, and that argued for a gate.

It argued wrongly. `/validate` is also the single most useful thing here to a
stranger: someone whose document is being rejected and who cannot find out
why. Putting a form in front of that is asking for an email at the exact
moment a person is least willing to give one, and it makes the tool useless
for the drive-by case that is most of its value.

So it is open, and the compute is protected by three layers instead —
reserved Lambda concurrency, Cloudflare rate limiting, and a per-IP daily
counter much smaller than the per-account one (`tenants.ANON_DAILY_QUOTA`).
That gap is the incentive to sign up: a prompt, not a wall.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

from .http import Request, Response, html
from .tenants import Tenant, store, valid_email
from .ui.render import render

#: Paths reachable with no account. Prefix match; see `is_open`.
#: Search-engine ownership proofs. Path -> the exact body that must come back.
#:
#: Defined HERE, next to the gate, rather than beside the route that serves
#: them. THE GATE IS THE TRAP: an unregistered path falls through to the signup
#: form and answers 200 with HTML, so Google reports "we found the file but its
#: content was wrong" — which sends you looking at the file rather than at the
#: gate. /robots.txt broke in exactly that way. Keeping the list adjacent to
#: OPEN_PATHS means one edit opens the path and defines the content together.
#:
#: A map rather than one constant because these accumulate: Google re-issues a
#: file if a property is removed and re-added, and Bing wants its own.
SITE_VERIFICATION = {
    "/google75af1e031b9d820d.html":
        "google-site-verification: google75af1e031b9d820d.html",
}


OPEN_PATHS = ("/docs", "/signup", "/static/", "/favicon.ico", "/validate",
              "/contact", "/reference", "/robots.txt", "/sitemap.xml",
              "/ingest", "/api/", "/samples", *SITE_VERIFICATION)


#: The storefront. Reachable by anyone holding a LIVE PUNCHOUT SESSION even
#: without an account, because the person browsing it is the buyer's employee
#: and their procurement system already authenticated for them. Everything
#: else stays account-scoped. See the gate in `handler.handler`.
STOREFRONT_PATHS = ("/shop", "/product", "/cart")


def storefront_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in STOREFRONT_PATHS)


def is_open(path: str) -> bool:
    if path == "/":
        return True
    return any(path == p.rstrip("/") or path.startswith(p) for p in OPEN_PATHS)


def current_tenant(request: Request) -> Optional[Tenant]:
    token = request.cookies.get("pst")
    return store().get(token) if token else None


def create_tenant(email: str, company: str = "") -> tuple:
    """Get or create the account for an address. Returns `(tenant, is_new)`.

    =========================================================================
    THE SAME EMAIL MUST NOT MINT A SECOND ACCOUNT
    =========================================================================
    It used to. And the natural path through this service walks straight into
    it: take credentials from `/api/signup` for your integration, then open the
    site to look at the resulting orders — signing up again, because that is
    what the site offers. Two accounts, and the orders you just sent are
    invisible to the browser you are looking at them with. The order screen
    then answers 404, which reads as "that order does not exist" rather than
    "it belongs to your other account".

    Returning the existing account means the shared secret is shown again to
    anyone who knows the address. That is a deliberate trade and a small one:
    the account guards nothing but a daily quota over a catalogue of invented
    companies, and the alternative — an account you cannot get back into —
    fails the people this exists for.

    Shared by the HTML form and the JSON API so the two cannot drift."""
    existing = store().by_email(email)
    if existing is not None:
        # A company supplied on a later signup is still worth keeping if the
        # first one had none; overwriting a good value with a blank is not.
        if company.strip() and not existing.company:
            existing.company = company.strip()[:120]
            store().put(existing)
        return existing, False

    tenant = Tenant(tenant_id=secrets.token_urlsafe(18),
                    email=email.strip()[:200], company=company.strip()[:120])
    store().put(tenant)
    return tenant, True


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

    tenant, is_new = create_tenant(email, company)

    return Response(
        status=200,
        body=render("welcome.html", nav="signup", tenant=tenant,
                    returning=not is_new),
        # A year, matching the account TTL. Secure as well as HttpOnly:
        # the comment here used to claim Secure while the string omitted it,
        # which QA caught. It rides the same Cloudflare TLS as everything
        # else, and the site now redirects http to https — but a cookie
        # without Secure is still one a downgrade attempt can collect.
        cookies=[f"pst={tenant.tenant_id}; Path=/; HttpOnly; Secure; "
                 f"SameSite=Lax; Max-Age={365 * 24 * 3600}"],
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
