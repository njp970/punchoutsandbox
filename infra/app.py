#!/usr/bin/env python3
"""PunchOut Sandbox — CDK entry point.

*Deploy target: a DEDICATED AWS account, created as a member account in the
existing Xenia AWS Organization (HOSTING.md §2). Same payer, same bill, its own
account boundary. Nothing here should ever be deployed into Xenia's own
account — a free public sandbox and a product with customers do not belong
behind the same blast radius.*

Two stacks, split for the ordinary reason: the table outlives the compute.
`cdk deploy` on SiteStack alone must never be able to replace the table, and
keeping them in one stack makes that a matter of remembering rather than a
matter of structure.

Region: eu-west-2 to match Xenia, so a session working across both repos does
not have to keep two consoles on different regions.
"""
import os

import aws_cdk as cdk

from sandbox.data_stack import DataStack
from sandbox.site_stack import SiteStack

app = cdk.App()

stage = app.node.try_get_context("stage") or "prod"
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "eu-west-2"),
)

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
