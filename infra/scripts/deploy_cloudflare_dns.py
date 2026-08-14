#!/usr/bin/env python3
"""Points punchoutsandbox.com at the deployed Lambda Function URL, and closes
the origin bypass described in `sandbox/site_stack.py`.

Run AFTER `cdk deploy`. Idempotent — safe to re-run after every deploy, and
you should, because a Function URL hostname changes if the function is ever
replaced rather than updated.

=============================================================================
CREDENTIALS — READ THIS BEFORE YOU EDIT ANYTHING
=============================================================================
This script reads the SAME two environment variables Xenia's
`infra/scripts/deploy_support_email_worker.py` reads, by design:

  CLOUDFLARE_API_TOKEN     needs, for this script: Zone > DNS > Edit AND
                           Zone > Zone Settings > Edit (the transform rule).
                           Xenia's Worker token has Account > Workers Scripts
                           > Edit, which this script does NOT need and which
                           does NOT grant DNS — so if you reuse that token
                           verbatim, expect a 403 on the DNS step until the
                           two zone scopes above are added to it.
  CLOUDFLARE_ACCOUNT_ID    same value; one Cloudflare account holds both zones.

They are read from the ENVIRONMENT and never from this file. Xenia keeps them
in `infra/.env` (gitignored); do the same here — `.gitignore` already covers
`infra/.env`. Reusing one token across two products is a deliberate, ordinary
trade-off (one thing to rotate, one blast radius); if you would rather they be
independent, mint a second token scoped to this zone only and nothing in this
script changes.

`EDGE_SHARED_SECRET` is generated here if you do not supply one, written to
BOTH sides of the boundary (the Cloudflare transform rule and the Lambda's
environment), and printed once. It is not stored anywhere else. If you lose
it, re-run this script — it will mint and install a fresh one.

=============================================================================
WHAT IT DOES
=============================================================================
1. Reads the Function URL from the deployed CloudFormation stack output.
2. Upserts a PROXIED CNAME for the apex and for `www` pointing at that origin.
   Cloudflare's CNAME flattening is what makes an apex CNAME legal.
3. Upserts a `http_request_late_transform` ruleset injecting
   `X-Edge-Secret: <secret>` on every proxied request.
4. Sets `EDGE_SHARED_SECRET` on the Lambda so `app/http.py` starts enforcing.

Order matters: step 4 comes LAST. Enforcing the header on the Lambda before
the transform rule exists would take the site down for exactly as long as it
takes you to read the traceback.
"""
import argparse
import os
import secrets
import sys

import boto3
import requests

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"
RULESET_PHASE = "http_request_late_transform"
HEADER_NAME = "X-Edge-Secret"


def _cf(session: requests.Session, method: str, path: str, **kw) -> dict:
    """Every Cloudflare call goes through here so that the `success: false`
    envelope is checked exactly once. Cloudflare returns HTTP 200 with
    `success: false` for a good number of logical failures, so
    `raise_for_status()` alone silently accepts them."""
    resp = session.request(method, f"{CF_API}{path}", timeout=30, **kw)
    try:
        body = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise SystemExit(f"Cloudflare returned non-JSON from {path}: {resp.text[:200]}")
    if not body.get("success"):
        raise SystemExit(
            f"Cloudflare {method} {path} failed: "
            f"{body.get('errors') or resp.text[:400]}"
        )
    return body


def function_url_from_stack(stage: str, region: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    stack_name = f"PunchoutSandbox-Site-{stage}"
    try:
        stacks = cfn.describe_stacks(StackName=stack_name)["Stacks"]
    except cfn.exceptions.ClientError as exc:
        raise SystemExit(f"could not read {stack_name} — has `cdk deploy` run? ({exc})")
    for output in stacks[0].get("Outputs", []):
        if output["OutputKey"].startswith("FunctionUrl"):
            return output["OutputValue"]
    raise SystemExit(f"{stack_name} has no FunctionUrl output")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="prod")
    ap.add_argument("--region", default="eu-west-2")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Resolve everything and print the plan without writing.",
    )
    args = ap.parse_args()

    try:
        token = os.environ["CLOUDFLARE_API_TOKEN"]
    except KeyError:
        raise SystemExit(
            "CLOUDFLARE_API_TOKEN is not set. Source it from a gitignored "
            "infra/.env, as Xenia does — do not paste it into this file."
        )

    edge_secret = os.environ.get("EDGE_SHARED_SECRET") or secrets.token_urlsafe(32)

    # The Function URL output is a full https URL with a trailing slash; the
    # DNS record needs the bare hostname.
    url = function_url_from_stack(args.stage, args.region)
    origin_host = url.replace("https://", "").replace("http://", "").rstrip("/")
    print(f"origin  : {origin_host}")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    zones = _cf(session, "GET", "/zones", params={"name": ZONE_NAME})["result"]
    if not zones:
        raise SystemExit(
            f"zone {ZONE_NAME} not found on this Cloudflare account. Add the "
            "domain to Cloudflare and point the registrar at Cloudflare's "
            "nameservers first."
        )
    zone_id = zones[0]["id"]
    print(f"zone    : {ZONE_NAME} ({zone_id})")

    if args.dry_run:
        print("\n-- dry run, nothing written --")
        print(f"  CNAME {ZONE_NAME} -> {origin_host} (proxied)")
        print(f"  CNAME www.{ZONE_NAME} -> {origin_host} (proxied)")
        print(f"  transform rule sets {HEADER_NAME} on every request")
        print(f"  lambda env EDGE_SHARED_SECRET would be set")
        return 0

    # ---- 1. DNS ---------------------------------------------------------
    existing = _cf(session, "GET", f"/zones/{zone_id}/dns_records")["result"]
    by_name = {r["name"]: r for r in existing}

    for name in (ZONE_NAME, f"www.{ZONE_NAME}"):
        record = {
            "type": "CNAME",
            "name": name,
            "content": origin_host,
            "proxied": True,  # orange cloud — the rate limiting is the point
            "ttl": 1,         # 1 == automatic, required when proxied
            "comment": "PunchOut Sandbox -> Lambda Function URL",
        }
        if name in by_name:
            _cf(session, "PATCH", f"/zones/{zone_id}/dns_records/{by_name[name]['id']}", json=record)
            print(f"dns     : updated {name}")
        else:
            _cf(session, "POST", f"/zones/{zone_id}/dns_records", json=record)
            print(f"dns     : created {name}")

    # ---- 2. Transform rule injecting the edge secret --------------------
    # PUT on the phase entrypoint replaces the whole ruleset. That is what we
    # want — this rule is the only late-transform rule this zone should have,
    # and merging into an unknown existing list is how you end up with four
    # copies of it after four deploys.
    _cf(
        session, "PUT",
        f"/zones/{zone_id}/rulesets/phases/{RULESET_PHASE}/entrypoint",
        json={
            "rules": [
                {
                    "action": "rewrite",
                    "action_parameters": {
                        "headers": {HEADER_NAME: {"operation": "set", "value": edge_secret}}
                    },
                    "expression": "true",
                    "description": "PunchOut Sandbox: prove this request came via Cloudflare",
                    "enabled": True,
                }
            ]
        },
    )
    print(f"edge    : transform rule installed ({HEADER_NAME})")

    # ---- 3. Teach the Lambda the same secret, LAST ----------------------
    lam = boto3.client("lambda", region_name=args.region)
    fn_name = f"punchout-sandbox-{args.stage}"
    current = lam.get_function_configuration(FunctionName=fn_name)["Environment"]["Variables"]
    lam.update_function_configuration(
        FunctionName=fn_name,
        Environment={"Variables": {**current, "EDGE_SHARED_SECRET": edge_secret}},
    )
    print(f"lambda  : EDGE_SHARED_SECRET set on {fn_name}")

    print(
        "\nDone. Set Cloudflare SSL/TLS mode to 'Full (strict)' if it is not "
        "already —\nAWS serves a valid certificate for the origin, so strict "
        "verification passes\nwith nothing further to configure."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
