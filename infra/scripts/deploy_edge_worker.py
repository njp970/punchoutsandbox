#!/usr/bin/env python3
"""Deploys the Cloudflare edge-proxy Worker and binds it to the domain.

Run AFTER `deploy_cloudflare_dns.py` (which creates the DNS records) and after
`cdk deploy` (which creates the Function URL this proxies to).

=============================================================================
WHY A WORKER RATHER THAN CLOUDFLARE'S OWN HOST OVERRIDE
=============================================================================
A Lambda Function URL validates the Host header and answers 403 to anything
addressed to a different hostname. Cloudflare's built-in fix is the Origin
Rules "Host Header Override" — which is a PAID feature; the API replies
"not entitled to use the HostHeader override" on the free plan.

So the Worker does it, for free, on the same 100k-requests/day allowance that
would comfortably serve this service for years (RESEARCH.md §D).

Credentials: same two modes as `deploy_cloudflare_dns.py` — a scoped token, or
the legacy Global API Key. For THIS script a scoped token needs
Account > Workers Scripts > Edit AND Zone > Workers Routes > Edit.
"""
import argparse
import os
import secrets
import sys

import boto3
import requests

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"
SCRIPT_NAME = "punchout-sandbox-edge"
WORKER_JS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "cloudflare", "edge-proxy", "worker.js",
)


def _auth_headers() -> tuple[dict, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}, "scoped API token"
    email, key = (os.environ.get("CLOUDFLARE_EMAIL"),
                  os.environ.get("CLOUDFLARE_API_KEY"))
    if email and key:
        return ({"X-Auth-Email": email, "X-Auth-Key": key},
                f"legacy Global API Key ({email})")
    raise SystemExit(
        "No Cloudflare credentials. Set CLOUDFLARE_API_TOKEN, or "
        "CLOUDFLARE_EMAIL + CLOUDFLARE_API_KEY, from a gitignored infra/.env."
    )


def _cf(session, method, path, **kw) -> dict:
    resp = session.request(method, f"{CF_API}{path}", timeout=45, **kw)
    try:
        body = resp.json()
    except ValueError:
        raise SystemExit(f"non-JSON from {path}: {resp.text[:200]}")
    if not body.get("success"):
        raise SystemExit(f"{method} {path} failed: {body.get('errors')}")
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="prod")
    ap.add_argument("--region", default="eu-west-2")
    args = ap.parse_args()

    headers, mode = _auth_headers()
    print(f"auth    : {mode}")

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")

    cfn = boto3.client("cloudformation", region_name=args.region)
    stacks = cfn.describe_stacks(StackName=f"PunchoutSandbox-Site-{args.stage}")["Stacks"]
    origin_url = next(o["OutputValue"] for o in stacks[0]["Outputs"]
                      if o["OutputKey"].startswith("FunctionUrl"))
    print(f"origin  : {origin_url}")

    # NO SILENT GENERATION. This line used to fall back to a fresh random
    # secret whenever the variable was unset, and that is how the site once
    # served an intermittent mix of 200s and 403s: the Worker and a transform
    # rule each minted their own value and whichever ran last won. One value,
    # stored in infra/.env, read by both this script and the CDK app.
    edge_secret = os.environ.get("EDGE_SHARED_SECRET")
    if not edge_secret:
        suggestion = secrets.token_urlsafe(32)
        raise SystemExit(
            "EDGE_SHARED_SECRET is not set.\n\n"
            "  This script will not invent one: the Lambda has to be given the "
            "SAME value, and a secret that changes on every run guarantees the "
            "two disagree.\n\n"
            "  Add this to the gitignored infra/.env, then re-run, then "
            "`cdk deploy`:\n\n"
            f"    EDGE_SHARED_SECRET={suggestion}\n")

    session = requests.Session()
    session.headers.update(headers)

    zones = _cf(session, "GET", "/zones", params={"name": ZONE_NAME})["result"]
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not found")
    zone_id = zones[0]["id"]
    if not account_id:
        account_id = zones[0]["account"]["id"]
    print(f"zone    : {ZONE_NAME} ({zone_id})")

    with open(WORKER_JS, "rb") as handle:
        code = handle.read()

    metadata = {
        "main_module": "worker.js",
        "compatibility_date": "2026-01-01",
        "bindings": [
            {"type": "plain_text", "name": "ORIGIN_URL", "text": origin_url},
            {"type": "secret_text", "name": "EDGE_SHARED_SECRET", "text": edge_secret},
        ],
    }
    _cf(
        session, "PUT",
        f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}",
        files={
            "metadata": (None, __import__("json").dumps(metadata), "application/json"),
            "worker.js": ("worker.js", code, "application/javascript+module"),
        },
    )
    print(f"worker  : {SCRIPT_NAME} uploaded")

    # Routes are additive and Cloudflare does not dedupe them, so existing
    # routes for the same pattern are removed first — otherwise re-running this
    # script accumulates duplicates that are awkward to reason about later.
    existing = _cf(session, "GET", f"/zones/{zone_id}/workers/routes")["result"]
    for route in existing:
        # `startswith` missed `www.punchoutsandbox.com/*`, so the cleanup
        # skipped it and the re-create then collided with itself — the script
        # was not idempotent for exactly one of its two routes.
        if ZONE_NAME in route.get("pattern", ""):
            _cf(session, "DELETE", f"/zones/{zone_id}/workers/routes/{route['id']}")

    for pattern in (f"{ZONE_NAME}/*", f"www.{ZONE_NAME}/*"):
        _cf(session, "POST", f"/zones/{zone_id}/workers/routes",
            json={"pattern": pattern, "script": SCRIPT_NAME})
        print(f"route   : {pattern} -> {SCRIPT_NAME}")

    # The Lambda learns the secret LAST. Enforcing before the Worker is live
    # would take the site down for exactly as long as it takes to read the
    # traceback.
    lam = boto3.client("lambda", region_name=args.region)
    fn = f"punchout-sandbox-{args.stage}"
    current = lam.get_function_configuration(FunctionName=fn)["Environment"]["Variables"]
    lam.update_function_configuration(
        FunctionName=fn,
        Environment={"Variables": {**current, "EDGE_SHARED_SECRET": edge_secret}},
    )
    print(f"lambda  : EDGE_SHARED_SECRET set on {fn}")
    print("          (the CDK stack also sets it from the same environment "
          "variable, so a later `cdk deploy` restores it rather than "
          "wiping it)")
    print("\nDone. The Worker rewrites Host to the origin, which is the whole")
    print("reason this exists — see its header comment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
