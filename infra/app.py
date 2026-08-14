#!/usr/bin/env python3
"""PunchOut Sandbox — CDK entry point.

*Deploy target: **the existing Xenia AWS account**, eu-west-2. Decided
deliberately — a second account was considered and rejected as more admin than
the isolation was worth for a free tool.*

=============================================================================
CO-TENANCY WITH XENIA: WHAT THAT COSTS AND WHAT PROTECTS AGAINST IT
=============================================================================
Everything here is named `PunchoutSandbox-*` (stacks) and `punchout-sandbox-*`
(resources), which collides with nothing in Xenia's `Xenia-*` namespace. The
account is already CDK-bootstrapped, so no bootstrap step is needed.

The real cost of sharing an account is not naming, it is the **account-level
Lambda concurrency pool**, which defaults to 1000 and is shared by every
function in the account. A public, unauthenticated tool that gets hammered —
the exact scenario BRIEF.md §3 flags as "an open invitation to burn storage
and compute" — could otherwise consume that pool and throttle Xenia's
production handlers. A free lead-generation toy taking down the product it
exists to promote would be a genuinely bad day.

`SiteStack` therefore sets **reserved concurrency** on the sandbox function.
Reserved concurrency both guarantees the function that many slots and CAPS it
at that number, so the sandbox can never take more than its allocation however
hard it is pushed. That single setting is what makes co-tenancy safe enough,
and it is the thing to check first if this is ever refactored.

The other shared limit is the AWS Free Tier, which is aggregated per account
anyway — so nothing changes there versus a member account, and at the traffic
in RESEARCH.md §D the allowances are orders of magnitude above expected load.

=============================================================================
TWO STACKS
=============================================================================
Split for the ordinary reason: the table outlives the compute. `cdk deploy` on
SiteStack alone must never be able to replace the table, and keeping them in
one stack makes that a matter of remembering rather than of structure.
"""
import os

import aws_cdk as cdk

from sandbox.data_stack import DataStack
from sandbox.site_stack import SiteStack

app = cdk.App()

stage = app.node.try_get_context("stage") or "prod"

# Region pinned, account resolved from ambient credentials — the same shape
# Xenia's own infra/app.py uses, so one set of credentials deploys either.
env = cdk.Environment(region="eu-west-2")

SITE_URL = "https://punchoutsandbox.com"
MAIL_DOMAIN = "punchoutsandbox.com"

# Where the contact form and the quota alert deliver. Read from the
# environment and NOT hardcoded, because this repository is intended to be
# public and a personal address committed to it is a personal address
# scraped from it. Set it in the gitignored infra/.env alongside the
# Cloudflare credentials. Unset is a valid state: app/mailer.py then logs
# messages instead of sending them, so nothing breaks and nothing is lost.
CONTACT_TO = os.environ.get("SANDBOX_CONTACT_TO")

# =============================================================================
# THE EDGE SECRET — READ HERE SO THAT A DEPLOY CANNOT SILENTLY REMOVE IT
# =============================================================================
# A Lambda Function URL with auth_type=NONE is reachable by anyone who learns
# its .on.aws hostname, which routes past every Cloudflare control: the rate
# limiting, the bot filtering, all of it. `app/http.py:require_edge` closes
# that by refusing any request without the shared header.
#
# It was open in production for a day, and the mechanism was not what failed —
# the WIRING was. `deploy_edge_worker.py` set the variable on the function
# directly, and this stack's `environment` dict, which omitted it, is
# authoritative: so every subsequent `cdk deploy` quietly deleted it. Nothing
# broke, nothing logged, and the site kept working. That is the worst shape a
# security control can have.
#
# Now both paths read the same value from the environment (infra/.env, as with
# the Cloudflare credentials), so a deploy RESTORES the variable instead of
# removing it — and a missing value stops the deploy rather than shipping an
# open origin. `-c allow_open_origin=1` is the deliberate escape hatch for a
# first deploy, before any Cloudflare rule exists to send the header.
EDGE_SHARED_SECRET = os.environ.get("EDGE_SHARED_SECRET")
if not EDGE_SHARED_SECRET and not app.node.try_get_context("allow_open_origin"):
    raise SystemExit(
        "EDGE_SHARED_SECRET is not set, so this deploy would leave the Lambda "
        "Function URL reachable directly, bypassing Cloudflare and every rate "
        "limit with it.\n\n"
        "  Set it in the gitignored infra/.env, then run "
        "scripts/deploy_edge_worker.py so the Worker sends the same value.\n"
        "  First deploy of a brand new stack, with no Worker in front of it "
        "yet:  cdk deploy -c allow_open_origin=1\n")

data = DataStack(app, f"PunchoutSandbox-Data-{stage}", stage=stage, env=env)
SiteStack(
    app, f"PunchoutSandbox-Site-{stage}",
    stage=stage,
    table=data.table,
    site_url=SITE_URL,
    mail_domain=MAIL_DOMAIN,
    contact_to=CONTACT_TO,
    edge_secret=EDGE_SHARED_SECRET,
    env=env,
)

app.synth()
