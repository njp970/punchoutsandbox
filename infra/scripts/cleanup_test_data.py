#!/usr/bin/env python3
"""Delete the accounts and orders that `tests/qa_live.py` leaves behind.

    .venv/bin/python infra/scripts/cleanup_test_data.py            # dry run
    .venv/bin/python infra/scripts/cleanup_test_data.py --execute

=============================================================================
WHY THIS IS NEEDED AT ALL
=============================================================================
The live QA suite signs up two accounts every time it runs, and accounts have
a ONE YEAR TTL (`tenants.TENANT_TTL_SECONDS`) because a real user who comes
back next quarter should still have their credentials. Orders last seven days;
sessions and counters clear themselves within hours.

So the only thing that accumulates is accounts — a year's worth of them, at two
per QA run. That is what this removes.

=============================================================================
WHAT IT WILL NOT TOUCH
=============================================================================
**Anything that is not obviously test data.** Targets are selected by explicit
pattern, never by "everything older than X":

  accounts  email ends @punchoutsandbox.example  (a reserved-for-examples TLD,
            so no real person can ever hold one)
  orders    orderID starts PO-QA- or PO-XSS- or equals PO-LIVE-1

**Sessions.** All of them expire within the hour on their own, and deleting one
that a browser is mid-punchout in would break something live for no gain.
Leave what is about to delete itself.

**Counters** (ANON/CONTACT/ALERT). Two-day TTL, and clearing them would reset
somebody's rate limit as a side effect of housekeeping.

Accounts are stored as TWO rows — `TENANT#<id>` and a `SANDBOXID#<id>` pointer
that the machine endpoints authenticate against — and both go together. A
stranded pointer authenticates nothing (the lookup that follows it returns
None) but it is exactly the kind of debris that makes a table hard to read.
"""
from __future__ import annotations

import argparse
import re
import sys

import boto3

TABLE = "punchout-sandbox-prod"
REGION = "eu-west-2"

#: Reserved by RFC 2606 for documentation, so an address here cannot belong to
#: a real person — which is what makes deleting on this pattern safe.
# Not anchored to the end of the string. A shell quoting slip once stored an
# address as `...@punchoutsandbox.example}` — real enough to create an account,
# and outside an anchored pattern, so it survived every cleanup run.
TEST_EMAIL = re.compile(r"@punchoutsandbox\.example", re.I)
TEST_ORDER = re.compile(r"^(PO-QA-|PO-XSS-|PO-LIVE-1$)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete. Without it, prints the plan only.")
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--region", default=REGION)
    ap.add_argument("--keep-email", action="append", default=[],
                    help="An address to spare even if it matches. Repeatable.")
    args = ap.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    items, start = [], None
    while True:
        page = table.scan(**({"ExclusiveStartKey": start} if start else {}))
        items.extend(page["Items"])
        start = page.get("LastEvaluatedKey")
        if not start:
            break

    spare = {e.lower() for e in args.keep_email}
    doomed_tenants: dict[str, str] = {}      # tenant_id -> email
    for item in items:
        pk = item.get("pk", "")
        if not pk.startswith("TENANT#"):
            continue
        email = str(item.get("email", ""))
        if TEST_EMAIL.search(email) and email.lower() not in spare:
            doomed_tenants[pk.split("#", 1)[1]] = email

    keys: list[tuple[dict, str]] = []
    for item in items:
        pk, sk = item.get("pk", ""), item.get("sk", "")
        key = {"pk": pk, "sk": sk}

        if pk.startswith("TENANT#") and pk.split("#", 1)[1] in doomed_tenants:
            keys.append((key, f"account {doomed_tenants[pk.split('#', 1)[1]]}"))
        elif pk.startswith("SANDBOXID#") and str(item.get("tenant_id", "")) in doomed_tenants:
            keys.append((key, f"credential pointer {pk.split('#', 1)[1]}"))
        elif pk.startswith("ORDERS#") and TEST_ORDER.match(str(item.get("order_id", ""))):
            keys.append((key, f"order listing {item.get('order_id')}"))
        elif pk.startswith("ORDER#"):
            # Order detail rows key on the ref, not the orderID, so match on
            # the summary rows found above rather than re-deriving.
            ref = pk.split("#")[-1]
            if any(str(o.get("ref", "")) == ref and TEST_ORDER.match(str(o.get("order_id", "")))
                   for o in items if o.get("pk", "").startswith("ORDERS#")):
                keys.append((key, f"order data {ref} [{sk}]"))

    if not keys:
        print("Nothing to remove.")
        return 0

    summary: dict[str, int] = {}
    for _, label in keys:
        summary[label.split(" ")[0]] = summary.get(label.split(" ")[0], 0) + 1

    print(f"{'DELETING' if args.execute else 'WOULD DELETE'} {len(keys)} rows "
          f"from {args.table}:")
    for kind, count in sorted(summary.items()):
        print(f"  {count:4}  {kind}")
    print(f"\naccounts removed: {len(doomed_tenants)}")
    for email in sorted(doomed_tenants.values()):
        print(f"  {email}")

    kept = [str(i.get("email")) for i in items
            if i.get("pk", "").startswith("TENANT#")
            and i.get("pk", "").split("#", 1)[1] not in doomed_tenants]
    if kept:
        print(f"\naccounts KEPT: {len(kept)}")
        for email in sorted(set(kept)):
            print(f"  {email}")

    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.")
        return 0

    with table.batch_writer() as batch:
        for key, _ in keys:
            batch.delete_item(Key=key)
    print(f"\nDeleted {len(keys)} rows.")
    print("Sessions and rate-limit counters were left alone — they expire on "
          "their own within hours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
