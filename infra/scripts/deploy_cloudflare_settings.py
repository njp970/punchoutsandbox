#!/usr/bin/env python3
"""Zone-level Cloudflare settings that the DNS and Worker scripts do not own.

Run after `deploy_cloudflare_dns.py` and `deploy_edge_worker.py`. Idempotent.

Credentials: identical scheme to the other two scripts — `CLOUDFLARE_API_TOKEN`,
or `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY`. Read from the environment only.

=============================================================================
1. ALWAYS USE HTTPS
=============================================================================
Without it Cloudflare happily serves the site over plaintext http. This site
issues shared secrets and sets a session cookie, so a request that arrives over
http is one that can be read on the wire. QA found it serving 200s on http.

=============================================================================
2. HSTS
=============================================================================
"Always Use HTTPS" fixes the second request; HSTS fixes the first. `preload` is
deliberately left OFF — preloading is effectively irreversible (removal takes
months to propagate through browser releases) and is not a commitment worth
making for a free sandbox.

=============================================================================
3. BROWSER INTEGRITY CHECK — OFF FOR THE MACHINE ENDPOINTS
=============================================================================
This is the one that matters, and it was a genuine product defect.

Cloudflare's Browser Integrity Check answers **403 (error 1010)** to requests
whose User-Agent it dislikes. QA confirmed it blocks, among others:

    Python-urllib/*      the Python standard library
    libwww-perl/*        legacy middleware, still in the wild
    (no User-Agent)      common in enterprise integration middleware

Those are not bots. They are exactly the clients this product exists to serve:
a buyer system POSTing a `PunchOutSetupRequest` from inside a procurement
platform. A blocked one gets a Cloudflare HTML error page instead of a cXML
`Status`, with no way to distinguish it from an outage — the worst possible
failure for a tool whose entire job is to tell you why your integration is
failing.

BIC is therefore disabled for the machine endpoints and LEFT ON everywhere
else, so the browser-facing pages keep the protection. Scoping it rather than
turning it off zone-wide is the whole reason this uses a Configuration Rule.

If the zone's plan does not offer Configuration Rules, the script says so and
tells you what to do rather than silently leaving the endpoints broken.
"""
import argparse
import json
import os
import sys

import requests

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"

#: The paths a machine posts to. Everything else keeps Browser Integrity Check.
MACHINE_PATHS = ["/punchout/setup", "/oci/setup", "/order"]


def _auth_headers() -> tuple[dict[str, str], str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}, "scoped API token"
    email = os.environ.get("CLOUDFLARE_EMAIL")
    key = os.environ.get("CLOUDFLARE_API_KEY")
    if email and key:
        return ({"X-Auth-Email": email, "X-Auth-Key": key},
                f"legacy Global API Key ({email})")
    raise SystemExit(
        "No Cloudflare credentials. Set CLOUDFLARE_API_TOKEN, or "
        "CLOUDFLARE_EMAIL + CLOUDFLARE_API_KEY, from the gitignored infra/.env."
    )


def _cf(session, method, path, *, allow_failure=False, **kw) -> dict:
    """Cloudflare answers HTTP 200 with `success: false` for many logical
    failures, so the envelope is checked here rather than the status code."""
    resp = session.request(method, f"{CF_API}{path}", timeout=30, **kw)
    try:
        body = resp.json()
    except ValueError:
        raise SystemExit(f"non-JSON from {path}: {resp.text[:200]}")
    if not body.get("success"):
        if allow_failure:
            return body
        raise SystemExit(f"Cloudflare {method} {path} failed: "
                         f"{body.get('errors') or resp.text[:400]}")
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    headers, mode = _auth_headers()
    print(f"auth    : {mode}")
    session = requests.Session()
    session.headers.update(headers)

    zones = _cf(session, "GET", "/zones", params={"name": ZONE_NAME})["result"]
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not found")
    zone_id = zones[0]["id"]
    print(f"zone    : {ZONE_NAME} ({zone_id})  plan={zones[0]['plan']['name']}")

    expression = " or ".join(
        f'http.request.uri.path eq "{p}"' for p in MACHINE_PATHS)

    if args.dry_run:
        print("\n-- dry run --")
        print("  always_use_https = on")
        print("  HSTS max-age 31536000, includeSubDomains, preload OFF")
        print(f"  browser integrity check OFF where: {expression}")
        return 0

    _cf(session, "PATCH", f"/zones/{zone_id}/settings/always_use_https",
        json={"value": "on"})
    print("https   : Always Use HTTPS on")

    _cf(session, "PATCH", f"/zones/{zone_id}/settings/security_header",
        json={"value": {"strict_transport_security": {
            "enabled": True,
            "max_age": 31536000,
            "include_subdomains": True,
            # Not preloaded. Getting off the preload list takes months.
            "preload": False,
            "nosniff": True,
        }}})
    print("hsts    : enabled, 1 year, includeSubDomains, preload off")

    # PUT on a phase entrypoint takes `rules` and `description` only — `kind`
    # and `phase` are implied by the URL and are rejected outright. It also
    # REPLACES the whole ruleset, which is what we want: this is the only
    # config rule the zone should have, and merging would leave four copies
    # after four deploys.
    ruleset = {
        "description": ("Browser Integrity Check blocks Python-urllib, "
                        "libwww-perl and UA-less clients with a 403. Those are "
                        "the buyer systems this product serves."),
        "rules": [{
            "action": "set_config",
            "action_parameters": {"bic": False},
            "expression": expression,
            "description": "No browser integrity check on the cXML/OCI inboxes",
            "enabled": True,
        }],
    }
    result = _cf(session, "PUT",
                 f"/zones/{zone_id}/rulesets/phases/http_config_settings/entrypoint",
                 json=ruleset, allow_failure=True)
    if result.get("success"):
        print(f"bic     : disabled for {', '.join(MACHINE_PATHS)}")
    else:
        print(f"bic     : COULD NOT SCOPE — {json.dumps(result.get('errors'))[:200]}")
        print()
        print("  Configuration Rules are unavailable on this zone. The machine")
        print("  endpoints are still blocking some HTTP clients. Either enable")
        print("  them, or turn Browser Integrity Check off zone-wide:")
        print()
        print("    Cloudflare dashboard -> Security -> Settings ->")
        print("    Browser Integrity Check -> Off")
        print()
        print("  Zone-wide is a real loss (it is a useful heuristic on the")
        print("  browser pages) but a smaller one than refusing a buyer system.")
        return 1

    print("\nVerify with:  .venv/bin/python tests/qa_live.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
