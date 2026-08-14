"""SiteStack — the whole application: one container Lambda behind one Function
URL, fronted by Cloudflare.

*Contract: HOSTING.md §2. Modelled on Xenia's `infra/xenia/procurement_stack.py`
for docstring posture, but the compute shape is deliberately different — see
below, because the difference is the entire reason this product can exist.*

=============================================================================
WHY A CONTAINER IMAGE AND NOT A ZIP — THIS IS THE LOAD-BEARING DECISION
=============================================================================
Xenia's procurement Lambda is a zip asset with vendored arm64 wheels, and it
parses cXML with `defusedxml` + stdlib `ElementTree`. Neither validates
against a DTD. BRIEF.md §2 states the consequence precisely: Xenia's cXML
tests round-trip `build.py` -> `extract.py`, which proves the two halves agree
with each other and NOT that either conforms to the spec. The fix — `lxml`,
the only sane Python DTD validator — is a C extension nobody wants in a zip
bundle, so it never got added, so the independent judge never existed.

A container image has no such constraint. `lxml` and the cXML DTD set ship
inside the image, `app/validation.py` validates every document in both
directions, and **that validation is the product**. If this stack ever drifts
back to `lambda_.Function` + `Code.from_asset`, the thing being sold goes with
it.

The cost of the choice is cold start: a slim arm64 Python image carrying
`lxml` cold-starts in roughly 1-2s. A punchout session is a human clicking
through a catalogue, so that lands inside "the page took a moment" rather than
"the integration timed out". Provisioned concurrency would remove it and cost
real money every month to serve single-digit users; not worth it.

=============================================================================
WHY A FUNCTION URL AND NOT API GATEWAY
=============================================================================
API Gateway's free tier — REST and HTTP API alike — expires 12 months after
account creation. Lambda Function URLs have no per-request charge at all,
ever. For a product whose whole thesis is "£0/month forever" (BRIEF.md §8),
a component that starts billing on a timer is disqualifying.

The trade-off is that Function URLs have no built-in throttling, no WAF and no
usage plans. That is what Cloudflare is for.

=============================================================================
WHY CLOUDFLARE IN FRONT, AND NOT CLOUDFRONT
=============================================================================
HOSTING.md's first draft put CloudFront here. Cloudflare replaced it, and the
reasoning is worth keeping because it is not obvious:

- CloudFront + Origin Access Control CAN lock a Function URL down properly
  (AuthType AWS_IAM, signed by the distribution). That is the tighter AWS
  answer, and if you want it, it is a contained swap.
- But CloudFront gives no rate limiting without AWS WAF, and a WAF web ACL is
  ~$5/month — the single largest line item in an otherwise free product, spent
  on the exact risk BRIEF.md §3 flags as "an open invitation to burn storage
  and compute".
- Cloudflare's FREE plan includes rate limiting, bot filtering and caching.
  The abuse control is the thing we actually need, and it is the thing
  Cloudflare gives away.

So: Cloudflare proxied (orange cloud) -> this Function URL as origin, SSL mode
Full (strict) — AWS serves a valid public certificate for
`*.lambda-url.<region>.on.aws`, so strict verification passes with nothing to
configure and no ACM certificate, no us-east-1 cert stack, and no DNS
validation dance.

THE BYPASS, AND WHAT CLOSES IT. A Function URL with `auth_type=NONE` is
reachable directly on its `.on.aws` hostname by anyone who learns it, which
would route straight past every Cloudflare control above. `EDGE_SHARED_SECRET`
closes it: a Cloudflare Transform Rule injects the header on every proxied
request, and `app/http.py` refuses any request arriving without it. The secret
is NOT set here — it is written post-deploy (see the stack's outputs and
`infra/scripts/deploy_cloudflare_dns.py`), because a value committed to a CDK
file is a value published to the repo.

Note the honest limit of that control: it is a bearer header over TLS, not a
signature. It stops casual direct hits on the origin; it does not survive
someone who has already seen a legitimate request's headers. For a free
sandbox serving synthetic data about invented companies, that is the right
amount of security. Do not carry this pattern into Xenia.
"""
import os

from aws_cdk import (
    CfnOutput, Duration, Stack,
    aws_dynamodb as ddb,
    aws_lambda as lambda_,
)
from constructs import Construct

# The Dockerfile lives at the repo root next to `app/`, so the build context
# includes the application package AND the vendored DTDs it validates against.
_IMAGE_CONTEXT = os.path.join(os.path.dirname(__file__), "..", "..")


class SiteStack(Stack):
    def __init__(
        self, scope: Construct, cid: str, *, stage: str,
        table: ddb.Table,
        site_url: str,
        **kwargs,
    ):
        super().__init__(scope, cid, **kwargs)

        self.fn = lambda_.DockerImageFunction(
            self, "Sandbox",
            function_name=f"punchout-sandbox-{stage}",
            code=lambda_.DockerImageCode.from_image_asset(_IMAGE_CONTEXT),
            architecture=lambda_.Architecture.ARM_64,
            # 30s: the slow path is not the punchout handshake but generating a
            # multi-page PDF invoice with a real font, and DTD-validating a
            # large cart. Both are bounded by app/xml_safe.py's 4MB cap.
            timeout=Duration.seconds(30),
            # 1024MB is chosen for CPU, not RAM. Lambda scales vCPU with memory,
            # and lxml's DTD validation plus PDF rendering are CPU-bound — at
            # 512MB the same request bills roughly the same total (half the
            # rate, twice the duration) while feeling twice as slow to a human.
            memory_size=1024,
            environment={
                "SANDBOX_TABLE": table.table_name,
                "STAGE": stage,
                "SITE_URL": site_url,
                # EDGE_SHARED_SECRET is deliberately ABSENT here — set it
                # post-deploy. app/http.py treats "unset" as "edge enforcement
                # off", which is what you want on a fresh stack that has no
                # Cloudflare rule in front of it yet, and a footgun if you
                # forget the second step. The deploy script prints the reminder.
            },
        )

        table.grant_read_write_data(self.fn)

        self.function_url = self.fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.BUFFERED,
        )

        # Consumed by infra/scripts/deploy_cloudflare_dns.py, which reads this
        # output rather than making you paste a hostname between two consoles.
        CfnOutput(
            self, "FunctionUrl",
            value=self.function_url.url,
            description="Origin hostname for the Cloudflare proxied CNAME",
            export_name=f"punchout-sandbox-{stage}-function-url",
        )
