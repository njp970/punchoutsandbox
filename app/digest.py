"""The weekly digest — the answer to "has anyone actually used this?".

*Runs on a schedule in its own Lambda (`infra/sandbox/site_stack.py`), reading
the same table and log group the application writes to, and sending through
the same SES identity as the contact form.*

=============================================================================
WHY THIS EXISTS
=============================================================================
Answering that question used to mean opening three consoles and remembering
what to look for. Which means it does not get asked, which means the first
real user could arrive, try it, hit something broken and leave, and nobody
would know until somebody thought to go and check.

A service nobody watches is a service whose failures are only ever reported by
the person they happened to — and this one is aimed at people who will not
report anything, because they are debugging something else at the time.

=============================================================================
THE HARD PART IS NOT COUNTING, IT IS SUBTRACTION
=============================================================================
Almost all traffic here is ours. Every QA run signs up two accounts, posts a
purchase order, generates three documents and attempts a delivery; deploys and
smoke checks add more. A digest that counted all of it would report a busy
service every week and be worthless — worse than nothing, because it would
read as evidence.

So the report is built around the distinction: **test traffic is named and
subtracted, and the headline is about what is left.** If the answer is nobody,
the digest says nobody, in the first line, every week, until it is not true.
That is the whole point of it.

Test traffic is identified by construction rather than by guessing:
`@punchoutsandbox.example` is the reserved-for-documentation domain the QA
suite uses (RFC 2606), so no real person can hold one.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from collections import Counter
from typing import Optional

from . import mailer, telemetry

#: RFC 2606 reserves `.example`, so an address here cannot belong to anybody.
#: The QA suite and every smoke check use it deliberately, which is what makes
#: the subtraction below reliable rather than a heuristic.
TEST_DOMAIN = "@punchoutsandbox.example"

#: The operator's own address is real, but it is not a stranger turning up.
#: Counted separately so a week of our own testing cannot read as adoption.
OPERATOR = (os.environ.get("CONTACT_TO") or "").strip().lower()

DAYS = 7


def _table():
    import boto3
    return boto3.resource("dynamodb").Table(os.environ["SANDBOX_TABLE"])


def _scan_all(table) -> list[dict]:
    items, start = [], None
    while True:
        page = table.scan(**({"ExclusiveStartKey": start} if start else {}))
        items.extend(page["Items"])
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


def _is_test(email: str) -> bool:
    return TEST_DOMAIN in (email or "").lower()


def _events(since: float) -> Counter:
    """Count telemetry events in the window.

    `filter_log_events` rather than a Logs Insights query: Insights is
    asynchronous and would mean polling for results inside a Lambda whose
    whole job takes a second otherwise. At this volume a filter scan is
    cheaper in both senses."""
    import boto3
    logs = boto3.client("logs")
    group = f"/aws/lambda/punchout-sandbox-{os.environ.get('STAGE', 'prod')}"
    counts: Counter = Counter()
    token = None
    try:
        while True:
            kwargs = {"logGroupName": group, "startTime": int(since * 1000),
                      "limit": 10000}
            if token:
                kwargs["nextToken"] = token
            page = logs.filter_log_events(**kwargs)
            for entry in page.get("events", []):
                message = entry.get("message", "").strip()
                if not message.startswith("{"):
                    continue
                try:
                    name = json.loads(message).get("event")
                except json.JSONDecodeError:
                    continue
                if name:
                    counts[name] += 1
            token = page.get("nextToken")
            if not token:
                return counts
    except Exception as exc:
        # A digest that fails because the log group was rotated is a digest
        # that stops arriving, which is the one thing it must not do.
        counts["_log_read_failed"] = 1
        telemetry.event("digest_log_read_failed", error=type(exc).__name__)
        return counts


def _github() -> Optional[dict]:
    """Public repository counters. No token, so no traffic figures.

    Deliberately not authenticated: the digest Lambda holding a GitHub
    credential to read a star count would be a poor trade. Stars and forks are
    public; views and clones are not, and are also mostly crawlers — see the
    note this prints."""
    import urllib.request
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/njp970/punchoutsandbox",
            headers={"accept": "application/vnd.github+json",
                     "user-agent": "punchoutsandbox-digest"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return {"stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "watchers": data.get("subscribers_count", 0),
                "issues": data.get("open_issues_count", 0)}
    except Exception:
        return None


def build_report(now: Optional[datetime.datetime] = None) -> tuple[str, str]:
    """Return `(subject, body)`."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=DAYS)
    since_ts = since.timestamp()

    items = _scan_all(_table())
    tenants = [i for i in items if str(i.get("pk", "")).startswith("TENANT#")]
    orders = [i for i in items if str(i.get("pk", "")).startswith("ORDERS#")]

    new_accounts, new_test, new_operator = [], [], []
    for tenant in tenants:
        created = float(tenant.get("created_at", 0))
        if created < since_ts:
            continue
        email = str(tenant.get("email", ""))
        if _is_test(email):
            new_test.append(email)
        elif OPERATOR and email.strip().lower() == OPERATOR:
            new_operator.append(email)
        else:
            new_accounts.append(email)

    # Orders have to be attributed too, not just counted. The first version
    # subtracted test ACCOUNTS and then reported every order — so it printed
    # "nobody has used it" directly above six orders, all of which were the QA
    # suite's. A number that contradicts the headline immediately above it
    # destroys the credibility of both.
    #
    # An order whose tenant no longer exists is ours by construction: the
    # cleanup script deletes test accounts, and nothing deletes a real one.
    tenant_email = {
        str(t["pk"]).split("#", 1)[1]: str(t.get("email", ""))
        for t in tenants
    }

    def order_is_real(order) -> bool:
        email = tenant_email.get(str(order.get("tenant_id", "")))
        if email is None:
            return False                      # orphaned — a cleaned-up test
        if _is_test(email):
            return False
        return not (OPERATOR and email.strip().lower() == OPERATOR)

    recent = [o for o in orders if float(o.get("received_at", 0)) >= since_ts]
    recent_orders = [o for o in recent if order_is_real(o)]
    test_orders = [o for o in recent if not order_is_real(o)]
    events = _events(since_ts)
    github = _github()

    real = len(new_accounts)
    total_real_accounts = len([
        t for t in tenants
        if not _is_test(str(t.get("email", "")))
        and str(t.get("email", "")).strip().lower() != OPERATOR])

    # THE HEADLINE. It is the only line most weeks will need, and saying
    # "nobody" plainly is the job — a digest that dresses up a quiet week is
    # one you stop believing.
    if real:
        headline = (f"{real} new account{'s' if real != 1 else ''} this week "
                    "— someone who is not us has signed up.")
    elif recent_orders:
        # An existing account sending an order is somebody using it, even
        # though nobody signed up this week.
        headline = (f"{len(recent_orders)} order"
                    f"{'s' if len(recent_orders) != 1 else ''} from an "
                    "existing account — somebody is actually integrating.")
    elif total_real_accounts:
        headline = ("No new accounts this week. "
                    f"{total_real_accounts} exist in total from earlier.")
    else:
        headline = "Nobody has used it yet."

    lines = [
        f"PunchOut Sandbox — week to {now.strftime('%d %B %Y')}",
        "=" * 60,
        "",
        headline,
        "",
        "REAL ACTIVITY",
        f"  New accounts           {real}",
    ]
    for email in new_accounts:
        lines.append(f"      {email}")
    lines += [
        f"  Orders received        {len(recent_orders)}",
        f"  Contact messages       {events.get('contact_received', 0)}"
        "   (includes any you sent yourself — they reach your inbox too)",
        f"  Documents delivered    {events.get('delivery', 0)}",
        f"  Anonymous limit hit    {events.get('anon_quota_exhausted', 0)}",
        "",
    ]

    if github:
        lines += [
            "GITHUB",
            f"  Stars {github['stars']}   Forks {github['forks']}   "
            f"Watchers {github['watchers']}   Open issues {github['issues']}",
            "  (Clone counts are deliberately not reported: a public repo is "
            "swept by",
            "   mirrors and crawlers, and the number reads as adoption when "
            "it is not.)",
            "",
        ]

    lines += [
        "NOT REAL USAGE — ours, listed so it is not mistaken for anything",
        f"  Test accounts created  {len(new_test)}",
        f"  Operator signups       {len(new_operator)}",
        f"  Orders from those      {len(test_orders)}",
        f"  Deliveries refused     {events.get('delivery_refused', 0)}"
        "   (the SSRF checks in the QA suite)",
        "",
    ]

    if events.get("_log_read_failed"):
        lines += ["  ⚠ The log group could not be read, so event counts above "
                  "are zero rather than absent.", ""]

    lines += [
        "-" * 60,
        "Everything here is counted over the last 7 days, except the GitHub",
        "totals and the account total, which are cumulative.",
        "",
        "This arrives whether or not anything happened. A quiet week reported",
        "is worth more than a busy week you had to go looking for.",
    ]

    subject = f"PunchOut Sandbox weekly — {headline}"[:180]
    return subject, "\n".join(lines)


def handler(event=None, context=None) -> dict:
    subject, body = build_report()
    sent = mailer.send(subject=subject, body=body, kind="digest")
    telemetry.event("digest_sent", delivered=sent)
    # Returned so a manual invoke shows the report rather than only mailing it.
    return {"sent": sent, "subject": subject, "body": body}
