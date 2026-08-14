"""DataStack — the single DynamoDB table behind PunchOut Sandbox.

*Modelled on Xenia's `infra/xenia/data_stack.py`, deliberately smaller: this
product has four entity kinds and no tenancy model worth the name.*

=============================================================================
WHY ONE TABLE, AND WHY ON-DEMAND
=============================================================================
Everything here is keyed by a single opaque id and read by that id — there is
no query pattern that wants a second table. `pk`/`sk` follow Xenia's
convention so anyone moving between the two repos reads the same shapes.

Billing is `PAY_PER_REQUEST` rather than provisioned. The DynamoDB always-free
tier is 25 GB of storage plus 25 provisioned WCU/RCU, and the provisioned
allowance is the part people reach for — but provisioned capacity on a service
with **single-digit users per year** (RESEARCH.md §D) means paying attention
to a capacity dial that will never move. On-demand at this volume rounds to
zero and needs no dial. If this ever gets popular enough for on-demand to cost
real money, that is a good problem and a two-line change.

=============================================================================
TTL IS LOAD-BEARING, NOT HOUSEKEEPING
=============================================================================
`expires_at` is registered as the TTL attribute and every ephemeral row sets
it. This is the entire storage-cost control and half the abuse control
(BRIEF.md §3, "abuse economics"):

- punchout sessions          — 1 hour
- captured documents         — 30 days
- generated invoices/PDFs    — 30 days

TTL deletes are FREE — they consume no write capacity. A cron sweeping rows
with `DeleteItem` would consume write units for the privilege of doing the
same job worse. Anything written here without an `expires_at` is, by
construction, something we intend to keep forever, so the absence of that
attribute should be a deliberate decision at every call site.

`removal_policy` is DESTROY on purpose. This is a free sandbox holding
synthetic test documents about invented companies; there is nothing here worth
a retained orphan stack if the whole thing is ever torn down. Xenia's own
tables take the opposite policy for the opposite reason.
"""
from aws_cdk import RemovalPolicy, Stack, aws_dynamodb as ddb
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, stage: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        self.table = ddb.Table(
            self, "SandboxTable",
            table_name=f"punchout-sandbox-{stage}",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            point_in_time_recovery=False,  # synthetic data; PITR is not free
            removal_policy=RemovalPolicy.DESTROY,
        )

        # One GSI: "show me this tenant's recent sessions/documents, newest
        # first". The console's session list is the only screen that needs it,
        # and without it that screen would be a Scan — which is fine at ten
        # rows and pathological at ten million, and the difference between
        # those two is one bad afternoon of someone scripting the API.
        self.table.add_global_secondary_index(
            index_name="gsi1",
            partition_key=ddb.Attribute(name="gsi1pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="gsi1sk", type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.ALL,
        )
