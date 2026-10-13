#!/usr/bin/env python3
"""Give every existing account the `SECRET#` pointer that secret-only login needs.

    infra/.venv/bin/python infra/scripts/backfill_secret_pointers.py            # dry run
    infra/.venv/bin/python infra/scripts/backfill_secret_pointers.py --execute

=============================================================================
WHY THIS IS NEEDED AT ALL
=============================================================================
A buyer behind Ariba Network cannot send our identity — the network writes its
own IDs into From and To — so the machine endpoints fall back to looking the
account up by its shared secret (`handler._machine_auth`). That lookup follows
a `SECRET#<digest>` pointer row, which `DynamoTenants.put` now writes.

Accounts created before that have no pointer until something saves them again,
and the thing that saves them is an authenticated request — which is exactly
what a network-routed buyer cannot make. Waiting for accounts to heal on use
would leave the people this was built for locked out. Hence this.

=============================================================================
WHAT IT WRITES, AND WHAT IT LEAVES ALONE
=============================================================================
One pointer row per live account that lacks one, with the account's own
`expires_at` so it disappears with the account. It never touches the account
row itself, and an account that already has its pointer is skipped, so running
it twice is harmless.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import boto3

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from app.tenants import secret_digest  # noqa: E402

TABLE = "punchout-sandbox-prod"
REGION = "eu-west-2"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="Actually write. Without it, prints the plan only.")
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--region", default=REGION)
    args = ap.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    items, start = [], None
    while True:
        page = table.scan(**({"ExclusiveStartKey": start} if start else {}))
        items.extend(page["Items"])
        start = page.get("LastEvaluatedKey")
        if not start:
            break

    existing = {item["pk"] for item in items
                if item.get("pk", "").startswith("SECRET#")}
    now = time.time()
    pointers: list[tuple[dict, str]] = []
    for item in items:
        pk = item.get("pk", "")
        if not pk.startswith("TENANT#") or item.get("sk") != "TENANT":
            continue
        secret = str(item.get("shared_secret", ""))
        expires_at = int(item.get("expires_at", 0))
        if not secret or expires_at <= now:
            continue
        pointer_pk = f"SECRET#{secret_digest(secret)}"
        if pointer_pk in existing:
            continue
        pointers.append(({"pk": pointer_pk, "sk": "POINTER",
                          "tenant_id": pk.split("#", 1)[1],
                          "expires_at": expires_at},
                         str(item.get("sandbox_id", "?"))))

    if not pointers:
        print("Every live account already has its secret pointer.")
        return 0

    print(f"{'WRITING' if args.execute else 'WOULD WRITE'} {len(pointers)} "
          f"secret pointers in {args.table}, for accounts:")
    for _, sandbox_id in sorted(pointers, key=lambda p: p[1]):
        print(f"  {sandbox_id}")

    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.")
        return 0

    with table.batch_writer() as batch:
        for item, _ in pointers:
            batch.put_item(Item=item)
    print(f"\nWrote {len(pointers)} pointers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
