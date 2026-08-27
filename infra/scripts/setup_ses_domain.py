#!/usr/bin/env python3
"""Verifies punchoutsandbox.com as an SES sending identity, and writes the
DKIM records into Cloudflare.

Run ONCE, before the first deploy that needs outbound mail. Idempotent, so
re-running after a change costs nothing.

=============================================================================
WHY THE SANDBOX GETS ITS OWN SENDING DOMAIN
=============================================================================
This SES account is Xenia's. It already has `onxenia.com` and `xenia-ai.net`
verified, production access granted, and a real transactional sending
reputation attached to it — and reputation in SES is an ACCOUNT-level fact,
not a per-domain one.

Borrowing a Xenia domain to send a sandbox contact form would have put Xenia
branding in a stranger's inbox and spent Xenia's DMARC alignment on it. A
separate verified domain costs three CNAME records and keeps the two things
distinguishable in every bounce report and every recipient's mail client.

What it does NOT separate is the account-level reputation, which is shared no
matter what we do here. That is handled in `app/mailer.py` instead, by never
sending to an address a stranger supplied — so no stranger can ever generate a
bounce or a complaint against this account.

=============================================================================
EASY DKIM, AND WHY THE RECORDS MUST NOT BE PROXIED
=============================================================================
SES issues three tokens; each becomes a CNAME at
`<token>._domainkey.punchoutsandbox.com` pointing into `dkim.amazonses.com`.
SES then resolves them itself to confirm we control the domain, and keeps
resolving them to publish the rotating public keys.

**These records are created grey-cloud (`proxied: False`), unlike the apex and
www records in `deploy_cloudflare_dns.py`.** Proxying replaces the answer with
Cloudflare's own edge addresses, which is exactly what we want for HTTP and
exactly what breaks a CNAME whose entire purpose is to be followed to AWS.
Cloudflare will not proxy a `_domainkey` name anyway, but setting it
explicitly means nobody has to know that.

CREDENTIALS: identical scheme to `deploy_cloudflare_dns.py` — a scoped
`CLOUDFLARE_API_TOKEN`, or the legacy `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY`
pair. Read from the environment, never from this file. AWS credentials come
from the ambient profile, as with `cdk deploy`.
"""
import argparse
import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloudflare_credentials as credentials

CF_API = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "punchoutsandbox.com"




def _cf(session: requests.Session, method: str, path: str, **kw) -> dict:
    """Cloudflare returns HTTP 200 with `success: false` for plenty of logical
    failures, so the envelope is checked here rather than trusting the status
    code."""
    resp = session.request(method, f"{CF_API}{path}", timeout=30, **kw)
    try:
        body = resp.json()
    except ValueError:
        raise SystemExit(f"Cloudflare returned non-JSON from {path}: {resp.text[:200]}")
    if not body.get("success"):
        raise SystemExit(f"Cloudflare {method} {path} failed: "
                         f"{body.get('errors') or resp.text[:400]}")
    return body


def ensure_identity(ses, domain: str, *, dry_run: bool = False) -> list[str]:
    """Create the identity if absent; return its DKIM tokens either way.

    `dry_run` is honoured HERE and not only at the DNS step, because creating
    the identity is itself a write. The first version of this script checked
    for dry-run after this call and duly created an SES identity while
    reporting that it had written nothing."""
    try:
        existing = ses.get_email_identity(EmailIdentity=domain)
        tokens = existing["DkimAttributes"].get("Tokens", [])
        status = existing["DkimAttributes"].get("Status")
        print(f"identity: {domain} already present (DKIM {status})")
        return tokens
    except ses.exceptions.NotFoundException:
        pass

    if dry_run:
        print(f"identity: WOULD create {domain} (no tokens to show until it exists)")
        return []

    created = ses.create_email_identity(
        EmailIdentity=domain,
        # Easy DKIM with a 2048-bit key. The default is 1024; 2048 is the
        # stronger choice and the only cost is that the CNAME answers are
        # bigger, which nothing cares about.
        DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
    )
    print(f"identity: created {domain}")
    return created["DkimAttributes"].get("Tokens", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="eu-west-2")
    ap.add_argument("--domain", default=ZONE_NAME)
    ap.add_argument("--dmarc", action="store_true",
                    help="Also publish a p=none DMARC record if none exists.")
    ap.add_argument("--spf", action="store_true",
                    help="Also publish an SPF record authorising SES.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    
    ses = boto3.client("sesv2", region_name=args.region)
    tokens = ensure_identity(ses, args.domain, dry_run=args.dry_run)
    if not tokens and not args.dry_run:
        raise SystemExit(
            "SES returned no DKIM tokens. That usually means the identity was "
            "created with BYODKIM rather than Easy DKIM — check the console."
        )

    session = credentials.session()
    zones = _cf(session, "GET", "/zones", params={"name": ZONE_NAME})["result"]
    if not zones:
        raise SystemExit(f"zone {ZONE_NAME} not found on this Cloudflare account.")
    zone_id = zones[0]["id"]
    print(f"zone    : {ZONE_NAME} ({zone_id})")

    records = [
        {
            "type": "CNAME",
            "name": f"{token}._domainkey.{args.domain}",
            "content": f"{token}.dkim.amazonses.com",
            # Grey cloud, deliberately — see the module docstring.
            "proxied": False,
            "ttl": 1,
            "comment": "SES Easy DKIM for PunchOut Sandbox",
        }
        for token in tokens
    ]

    if args.spf:
        # =================================================================
        # WHY THIS WAS MISSING, AND WHY IT MATTERS
        # =================================================================
        # DKIM alone satisfies DMARC when the signing domain aligns, which it
        # does here — so mail from this domain passes DMARC without SPF, and
        # nothing bounces. It still costs deliverability: a young domain with
        # no SPF, sending to a personal mailbox, with a Reply-To on a
        # different domain from the From, is a combination spam filters score
        # harshly. A contact-form message really did land somewhere the
        # operator never saw it.
        #
        # `-all` rather than `~all`: SES is the ONLY thing that sends as this
        # domain, so a hard fail is both accurate and stronger. If a mailbox
        # provider is ever added for this domain, this record has to change
        # first or its mail will be rejected.
        records.append({
            "type": "TXT",
            "name": args.domain,
            "content": "v=spf1 include:amazonses.com -all",
            "proxied": False,
            "ttl": 1,
            "comment": "SPF — SES is the only sender for this domain",
        })

    if args.dmarc:
        records.append({
            "type": "TXT",
            "name": f"_dmarc.{args.domain}",
            # p=none: monitor, do not reject. Publishing an enforcing policy
            # on a domain whose sending you have not yet observed is how you
            # discover a misconfiguration by having mail silently disappear.
            "content": "v=DMARC1; p=none;",
            "proxied": False,
            "ttl": 1,
            "comment": "PunchOut Sandbox DMARC (monitor only)",
        })

    if args.dry_run:
        print("\n-- dry run, nothing written --")
        for record in records:
            print(f"  {record['type']:5} {record['name']} -> {record['content']}")
        return 0

    existing = _cf(session, "GET", f"/zones/{zone_id}/dns_records",
                   params={"per_page": 200})["result"]

    def _match(record):
        """Find the record this one replaces, if any.

        =================================================================
        MATCHING BY (name, type) DESTROYED A LIVE RECORD. DO NOT GO BACK.
        =================================================================
        A name can hold many TXT records and they are independent. This
        matched on name and type alone, so adding SPF at the apex found the
        Google Search Console verification TXT sitting there, decided it was
        the same record, and PATCHED it out of existence. The output said
        `updated` and nothing looked wrong.

        `deploy_search_verification.py` was written with a docstring warning
        about exactly this hazard. This script was not fixed at the same time,
        on the reasoning that its own records had distinct names — which was
        true right up until it was asked to write one that did not.

        TXT records therefore match on name AND a content prefix; everything
        else, whose content is a target rather than a value, still matches on
        name and type."""
        same_name = [r for r in existing
                     if r["name"] == record["name"] and r["type"] == record["type"]]
        if record["type"] != "TXT":
            return same_name[0] if same_name else None
        prefix = record["content"].split("=")[0] + "="
        for candidate in same_name:
            if candidate["content"].startswith(prefix):
                return candidate
        return None

    for record in records:
        found = _match(record)
        if found:
            _cf(session, "PATCH",
                f"/zones/{zone_id}/dns_records/{found['id']}", json=record)
            print(f"dns     : updated {record['name']} ({record['content'][:28]}…)")
        else:
            _cf(session, "POST", f"/zones/{zone_id}/dns_records", json=record)
            print(f"dns     : created {record['name']} ({record['content'][:28]}…)")

    print("\nDKIM records published. SES verifies asynchronously — usually")
    print("minutes, occasionally an hour. Check with:")
    print(f"  aws sesv2 get-email-identity --email-identity {args.domain} "
          f"--region {args.region} --query DkimAttributes.Status")
    print("\nNothing sends until it reports SUCCESS. Until then app/mailer.py")
    print("logs contact messages instead of losing them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
