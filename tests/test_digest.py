"""The digest must not contradict itself about whose activity it is reporting.

This is the second time the report has disagreed with itself. The first was a
headline saying "Nobody has used it yet" above "Orders received 6". The second
listed four of our own video-capture orders under REAL ACTIVITY, as
"(unknown account)", in the same digest whose `Orders received` line correctly
said zero — because the two blocks applied different rules to the same fact.

So the rule is tested here rather than trusted: an account id the tenant table
cannot resolve is ours, because cleanup deletes test accounts and nothing
deletes a real one.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SANDBOX_TABLE", "test-table")

from app.digest import attribute_activity, _is_test, TEST_DOMAIN

failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


print("\nA. Whose activity is it?")

REAL = "someone@company.com"
IDENTITIES = {"PSB-real": REAL, "PSB-test": f"qa{TEST_DOMAIN}"}

active, orphaned = attribute_activity(
    {"PSB-real": Counter({"validate": 3})}, IDENTITIES)
check("a resolvable, non-test account is real activity",
      active == [(REAL, "validate 3")] and orphaned == 0, str(active))

active, orphaned = attribute_activity(
    {"PSB-test": Counter({"order_received": 1})}, IDENTITIES)
check("an account on the test domain is not real activity",
      active == [] and orphaned == 1, f"{active} {orphaned}")

# The regression. These four ids are the shape of the real incident: orders
# posted by video-capture accounts that cleanup then deleted.
ORPHANS = {f"PSB{n}": Counter({"order_received": 1})
           for n in ("926710670", "590064254", "975863225", "236249262")}
active, orphaned = attribute_activity(ORPHANS, IDENTITIES)
check("an account the table cannot resolve is NOT real activity",
      active == [], f"leaked {active}")
check("...and its events are counted as ours, not dropped",
      orphaned == 4, f"orphaned={orphaned}")
check("...and it is never labelled '(unknown account)'",
      not any("unknown" in e for e, _ in active))

active, orphaned = attribute_activity(
    {**ORPHANS, "PSB-real": Counter({"validate": 1})}, IDENTITIES)
check("real activity still surfaces alongside orphans",
      active == [(REAL, "validate 1")] and orphaned == 4, str(active))


print("\nB. The rule matches the one orders use")

# order_is_real() treats a missing tenant as test. attribute_activity must
# agree, or the two blocks of the same report disagree again.
def order_is_real(tenant_email, order_tenant):
    email = tenant_email.get(order_tenant)
    if email is None or _is_test(email):
        return False
    return True

for tid, expected in [("PSB-real", True), ("PSB-test", False),
                      ("PSB926710670", False)]:
    a, _ = attribute_activity({tid: Counter({"order_received": 1})}, IDENTITIES)
    check(f"orders and activity agree about {tid}",
          bool(a) == order_is_real(IDENTITIES, tid),
          f"activity={bool(a)} orders={order_is_real(IDENTITIES, tid)}")


print("\nC. The report as it will actually arrive")
# Everything that touches AWS is replaced, so this runs the real
# build_report and reads the real text — the place both earlier
# contradictions were only visible.
import datetime as _dt
from decimal import Decimal as _D
import app.digest as digest

NOW = _dt.datetime(2026, 9, 21, 8, 0, tzinfo=_dt.timezone.utc)
WEEK_AGO = (NOW - _dt.timedelta(days=3)).timestamp()
ITEMS = [
    {"pk": "TENANT#t-real", "sk": "TENANT", "email": "dev@vendor.example.org",
     "sandbox_id": "PSB111111111", "created_at": _D(str(WEEK_AGO))},
    {"pk": "ORDERS#t-real", "sk": "ORDER#r1", "tenant_id": "t-real",
     "order_id": "PO-1", "received_at": _D(str(WEEK_AGO))},
    # an order whose tenant was cleaned up: ours
    {"pk": "ORDERS#t-gone", "sk": "ORDER#r2", "tenant_id": "t-gone",
     "order_id": "PO-QA-1", "received_at": _D(str(WEEK_AGO))},
]
EVENTS = Counter()
for payload in ([{"event": "punchout_setup", "outcome": "200"}] * 76
                + [{"event": "punchout_setup", "outcome": "400"}]
                + [{"event": "auth_refused", "reason": "wrong_secret"}] * 9
                + [{"event": "auth_refused", "reason": "placeholder_identity"}] * 5
                + [{"event": "auth_refused", "reason": "unknown_identity"}] * 2):
    digest.tally(EVENTS, payload)

digest._table = lambda: None
digest._scan_all = lambda table: ITEMS
digest._events = lambda since: EVENTS
digest._events_by_account = lambda since: {
    "PSB111111111": Counter({"punchout_setup": 40, "order_received": 1}),
    "PSB926710670": Counter({"order_received": 4}),       # deleted test account
}
digest._github = lambda: None
_, body = digest.build_report(NOW)

check("the tally splits punchouts by outcome",
      EVENTS["punchout_setup"] == 77 and EVENTS["punchout_setup:200"] == 76)
check("successful punchouts are reported", "Punchout sessions      76" in body)
check("refused logins are reported", "Refused logins         16" in body)
check("...broken down by reason, largest first",
      "wrong secret 9, placeholder identity 5, unknown identity 2" in body,
      body[body.find("Refused logins"):][:120])
check("the real account's activity is named",
      "dev@vendor.example.org: punchout setup 40, order received 1" in body)
check("a deleted account's events are not REAL ACTIVITY",
      "unknown account" not in body and "PSB926710670" not in body)
check("...they are counted as ours instead", "Events from deleted    4" in body)
check("orders received counts only the live account's",
      "Orders received        1" in body)


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("The digest agrees with itself about whose activity it is reporting.")
