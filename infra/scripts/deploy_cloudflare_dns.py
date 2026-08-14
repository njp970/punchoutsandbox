#!/usr/bin/env python3
"""Points punchoutsandbox.com at the deployed Lambda Function URL, and closes
the origin bypass described in `sandbox/site_stack.py`.

Run AFTER `cdk deploy`. Idempotent — safe to re-run after every deploy, and
you should, because a Function URL hostname changes if the function is ever
replaced rather than updated.

=============================================================================
CREDENTIALS — TWO AUTH MODES, READ THIS BEFORE YOU EDIT ANYTHING
=============================================================================
Cloudflare has two authentication schemes and this script accepts either,
because the account already has both. Everything is read from the ENVIRONMENT
and never from this file. Xenia keeps its values in `infra/.env` (gitignored);
do the same here — `.gitignore` already covers `infra/.env`.

**Mode 1 — scoped API token (preferred).**

  CLOUDFLARE_API_TOKEN     sent as `Authorization: Bearer <token>`.
                           For THIS script it needs Zone > DNS > Edit AND
                           Zone > Zone Settings > Edit (the transform rule).
                           Xenia's Worker token carries Account > Workers
                           Scripts > Edit, which this script does not need and
                           which does NOT grant DNS — reuse it verbatim and
                           you get a 403 on the DNS step.

**Mode 2 — legacy Global API Key.**

  CLOUDFLARE_EMAIL         the account email
  CLOUDFLARE_API_KEY       the Global API Key

  Sent as the `X-Auth-Email` / `X-Auth-Key` header pair. Same API host and
  same paths — only the headers differ, despite the "different endpoint"
  folklore.

  ⚠️ **The Global API Key is not scoped.** It grants full control of every
  zone and every setting on the account, it cannot be restricted, and it is
  the credential an attacker most wants. It works, and it is the faster path
  today, but a token scoped to this one zone is strictly better: it can be
  rotated without touching anything else, and a leak costs you one zone rather
  than the account. Treat Mode 2 as the expedient option, not the destination.

`CLOUDFLARE_ACCOUNT_ID` is not required by this script — every call here is
zone-scoped and the zone is resolved by name.

=============================================================================
WHAT IT DOES — AND WHAT IT DELIBERATELY DOES NOT
=============================================================================
1. Reads the Function URL from the deployed CloudFormation stack output.
2. Upserts a PROXIED CNAME for the apex and for `www` pointing at that origin.
   Cloudflare's CNAME flattening is what makes an apex CNAME legal.

That is all. It does NOT set the Host header and does NOT install the edge
secret — both belong to `deploy_edge_worker.py`, which must be run after this.
See the note in `main()` for the outage that division of labour prevents.
"""
import argparse
import os
import sys

import boto3
import requests

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"



def _auth_headers() -> tuple[dict[str, str], str]:
    """Build Cloudflare auth headers from whichever credentials are present.

    Returns `(headers, description)`. The description is printed so the
    operator can see which mode was used — silently picking one of two
    credentials is how you end up debugging a 403 against the wrong key."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}, "scoped API token"

    email = os.environ.get("CLOUDFLARE_EMAIL")
    key = os.environ.get("CLOUDFLARE_API_KEY")
    if email and key:
        return (
            {"X-Auth-Email": email, "X-Auth-Key": key},
            f"legacy Global API Key ({email})",
        )

    raise SystemExit(
        "No Cloudflare credentials found. Set EITHER:\n"
        "  CLOUDFLARE_API_TOKEN                     (scoped token, preferred)\n"
        "or\n"
        "  CLOUDFLARE_EMAIL + CLOUDFLARE_API_KEY    (legacy Global API Key)\n\n"
        "Source them from a gitignored infra/.env, as Xenia does — do not "
        "paste them into this file."
    )


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

    headers, auth_mode = _auth_headers()
    print(f"auth    : {auth_mode}")

    # The Function URL output is a full https URL with a trailing slash; the
    # DNS record needs the bare hostname.
    url = function_url_from_stack(args.stage, args.region)
    origin_host = url.replace("https://", "").replace("http://", "").rstrip("/")
    print(f"origin  : {origin_host}")

    session = requests.Session()
    session.headers.update(headers)

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
        print("  (Host header + edge secret are deploy_edge_worker.py's job)")
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

    # ---- 2. The Host header and the edge secret are NOT handled here ----
    #
    # Both are the edge Worker's job (infra/scripts/deploy_edge_worker.py),
    # and there is a scar behind that division of labour.
    #
    # This script used to install a transform rule that injected
    # X-Edge-Secret, generating a fresh secret each run. Once the Worker also
    # began setting that header with its OWN generated secret, the two fought:
    # whichever ran last won, so the site returned an intermittent mix of 200s
    # and 403s that looked exactly like route propagation and was not.
    #
    # ONE MECHANISM OWNS THE SECRET. That is the Worker, because it has to set
    # the Host header anyway (Lambda Function URLs reject a mismatched Host,
    # and Cloudflare's own Host override is a paid feature). Do not reintroduce
    # a transform rule here.
    # PUT on the phase entrypoint replaces the whole ruleset. That is what we
    # want — this rule is the only late-transform rule this zone should have,
    # and merging into an unknown existing list is how you end up with four
    # copies of it after four deploys.

    print("\nDNS done. NOW RUN scripts/deploy_edge_worker.py — until it runs,")
    print("every request through Cloudflare gets a 403 from Lambda, because a")
    print("Function URL rejects any Host but its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
