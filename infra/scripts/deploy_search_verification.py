#!/usr/bin/env python3
"""Publishes search-engine ownership TXT records for punchoutsandbox.com.

    .venv/bin/python scripts/deploy_search_verification.py \
        --google "google-site-verification=XXXXXXXXXXXXXXXXXXXXXXXX"

Idempotent, and safe to run alongside the SES and DMARC records already in
this zone.

=============================================================================
WHY DNS AS WELL AS THE HTML FILE
=============================================================================
`/google<token>.html` is served by the application (`signup.SITE_VERIFICATION`)
and Google re-checks it periodically. That makes the property's survival depend
on a route inside a Lambda: change how paths dispatch, or put a gate in front
of one path too many, and the property is lost silently.

A TXT record depends on nothing but the zone. Google holds several verification
methods at once and needs only ONE to keep working, so publishing both means
the property survives either failing.

It also unlocks a **Domain property**, which a URL-prefix property cannot be:
DNS is the only method Google accepts for one, and a Domain property covers
every subdomain and both protocols in a single view.

=============================================================================
THE TRAP: TXT RECORDS AT A NAME ARE A SET, NOT A VALUE
=============================================================================
One name can hold many TXT records and they are independent. SPF, DMARC, SES
and a verification token routinely share a name.

So this matches on **name AND content prefix**, never on name alone. Upserting
by `(name, type)` — the obvious first draft — would silently replace somebody
else's TXT the first time two shared a name. Losing an SPF record that way is a
mail outage you discover from a bounce.

CREDENTIALS: as with the other scripts — `CLOUDFLARE_API_TOKEN`, or
`CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY`. Read from the environment only.
"""
import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloudflare_credentials as credentials

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"

#: Each provider's TXT value starts with a known prefix. Matching on it is what
#: makes this idempotent without touching another record at the same name.
PREFIXES = {"google": "google-site-verification=", "bing": "MS="}




def _cf(session, method, path, **kw) -> dict:
    resp = session.request(method, f"{CF_API}{path}", timeout=30, **kw)
    try:
        body = resp.json()
    except ValueError:
        raise SystemExit(f"non-JSON from {path}: {resp.text[:200]}")
    if not body.get("success"):
        errors = body.get("errors") or resp.text[:300]
        if resp.status_code in (401, 403):
            raise SystemExit(
                f"Cloudflare refused the credentials: {errors}\n\n"
                "  A Global API Key is 37 HEX characters. A scoped token is\n"
                "  longer and mixed-case, and must go in CLOUDFLARE_API_TOKEN\n"
                "  rather than CLOUDFLARE_API_KEY — they are sent as different\n"
                "  headers, so the right value in the wrong variable fails\n"
                "  exactly like a revoked one.")
        raise SystemExit(f"Cloudflare {method} {path} failed: {errors}")
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--google", help='The whole TXT value Search Console shows, '
                                     'including the "google-site-verification=" prefix')
    ap.add_argument("--bing", help='The whole TXT value Bing shows, e.g. "MS=ms12345678"')
    ap.add_argument("--name", default=ZONE_NAME, help="Record name; the apex normally.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wanted = {k: v for k, v in (("google", args.google), ("bing", args.bing)) if v}
    if not wanted:
        raise SystemExit(
            "Nothing to publish. Pass --google and/or --bing.\n\n"
            "  Google: Search Console -> Settings -> Ownership verification ->\n"
            "          Domain name provider -> Any DNS provider. Copy the WHOLE\n"
            "          string, prefix included.\n"
            "  Prefer a Domain property over a URL-prefix one: DNS is its only\n"
            "  verification method, and it covers every subdomain at once.")

    for provider, value in wanted.items():
        prefix = PREFIXES[provider]
        if not value.startswith(prefix):
            raise SystemExit(
                f"The --{provider} value should start with '{prefix}'; got "
                f"'{value[:32]}…'. Paste the whole string the console shows, "
                "not just the token part.")

    session = credentials.session()

    zones = _cf(session, "GET", "/zones", params={"name": ZONE_NAME})["result"]
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not found on this account")
    zone_id = zones[0]["id"]
    print(f"zone    : {ZONE_NAME} ({zone_id})")

    existing = _cf(session, "GET", f"/zones/{zone_id}/dns_records",
                   params={"type": "TXT", "per_page": 200})["result"]
    at_name = [r for r in existing if r["name"] == args.name]
    print(f"existing: {len(at_name)} TXT record(s) at {args.name}")
    for record in at_name:
        print(f"          {record['content'][:64]}")

    for provider, value in wanted.items():
        prefix = PREFIXES[provider]
        mine = [r for r in at_name if r["content"].startswith(prefix)]
        payload = {"type": "TXT", "name": args.name, "content": value, "ttl": 1,
                   "comment": f"{provider} site verification"}
        if args.dry_run:
            print(f"dry-run : would {'update' if mine else 'create'} "
                  f"{provider} -> {value[:48]}")
            continue
        if mine:
            _cf(session, "PATCH",
                f"/zones/{zone_id}/dns_records/{mine[0]['id']}", json=payload)
            print(f"txt     : updated {provider}")
            for extra in mine[1:]:
                # Two tokens from one provider is stale state, not redundancy:
                # verification reads whichever it happens to find.
                _cf(session, "DELETE", f"/zones/{zone_id}/dns_records/{extra['id']}")
                print(f"txt     : removed a duplicate {provider} record")
        else:
            _cf(session, "POST", f"/zones/{zone_id}/dns_records", json=payload)
            print(f"txt     : created {provider}")

    if args.dry_run:
        return 0

    print("\nPublished. Cloudflare serves it immediately; Google usually sees it")
    print("within minutes. Confirm before pressing Verify:")
    print(f"  curl -s 'https://dns.google/resolve?name={args.name}&type=TXT'")
    print()
    print("  DoH rather than `dig`, on purpose. Some networks intercept port 53,")
    print("  and an intercepted `dig` returns an empty answer even when you name")
    print("  the authoritative server directly — the tell is `flags: qr rd ra`")
    print("  with no `aa`. That looks exactly like the record not existing. This")
    print("  both dodges the interception and asks the resolver that matters.")
    print("\nThe HTML file stays in place. Google holds both methods and needs")
    print("only one of them to keep working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
