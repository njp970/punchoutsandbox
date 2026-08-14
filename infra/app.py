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
import aws_cdk as cdk

from sandbox.data_stack import DataStack
from sandbox.site_stack import SiteStack

app = cdk.App()

stage = app.node.try_get_context("stage") or "prod"

# Region pinned, account resolved from ambient credentials — the same shape
# Xenia's own infra/app.py uses, so one set of credentials deploys either.
env = cdk.Environment(region="eu-west-2")

SITE_URL = "https://punchoutsandbox.com"

data = DataStack(app, f"PunchoutSandbox-Data-{stage}", stage=stage, env=env)
SiteStack(
    app, f"PunchoutSandbox-Site-{stage}",
    stage=stage,
    table=data.table,
    site_url=SITE_URL,
    env=env,
)

app.synth()
