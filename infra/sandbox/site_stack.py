"""SiteStack — the whole application: one container Lambda behind one Function
URL, fronted by Cloudflare.

*Contract: HOSTING.md §2. Modelled on Xenia's `infra/xenia/procurement_stack.py`
for docstring posture, but the compute shape is deliberately different — see
below, because the difference is the entire reason this product can exist.*

=============================================================================
A ZIP WITH VENDORED WHEELS — AND WHY THIS REVERSED AN EARLIER DECISION
=============================================================================
This was originally a container image, on the argument that `lxml` — the only
maintained Python DTD validator, and therefore the thing that makes this
product possible at all — was a C extension nobody wanted in a zip bundle.

**That argument was wrong**, and it is worth recording why rather than quietly
deleting it. `pip install --platform manylinux2014_aarch64 --only-binary=:all:`
downloads the prebuilt Linux ARM binary onto any developer machine, whatever
that machine is. No Docker, no emulation, no cross-build. It is precisely the
vendored-wheels pattern Xenia already uses for its own procurement Lambda, so
the precedent was sitting in the next repository the whole time.

The container cost a 2GB developer dependency, an ECR repository, slower
deploys and slower cold starts, and bought nothing that mattered. The DTD
validation that BRIEF.md §2 identifies as the product works identically here:
`lxml` and the whole cXML DTD set ship inside the asset, and
`scripts/build_asset.sh` FAILS the build if either is missing or is not
actually aarch64 Linux — because a macOS build of lxml imports perfectly on a
laptop and dies in Lambda with an opaque "invalid ELF header".

The one real constraint is that the asset must be built before `cdk deploy`
runs; `_ASSET_PATH` points at build output, not at source.

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

# Pre-built by scripts/build_asset.sh — the application package plus vendored
# aarch64 wheels plus the cXML DTDs. This is BUILD OUTPUT, not source: run the
# build script before `cdk deploy` or you will ship a stale bundle.
_ASSET_PATH = os.path.join(os.path.dirname(__file__), "..", "build", "sandbox")


class SiteStack(Stack):
    def __init__(
        self, scope: Construct, cid: str, *, stage: str,
        table: ddb.Table,
        site_url: str,
        **kwargs,
    ):
        super().__init__(scope, cid, **kwargs)

        self.fn = lambda_.Function(
            self, "Sandbox",
            function_name=f"punchout-sandbox-{stage}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.handler.handler",
            code=lambda_.Code.from_asset(_ASSET_PATH),
            # ARM64 is ~20% cheaper per ms and costs nothing extra to target:
            # the wheels are downloaded for aarch64 regardless of the
            # developer's own architecture. Must stay in step with PLATFORM in
            # scripts/build_asset.sh — change one without the other and the
            # Lambda cannot import its own dependencies.
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
            # THE SETTING THAT MAKES SHARING XENIA'S ACCOUNT SAFE.
            #
            # Account-level Lambda concurrency defaults to 1000 and is shared
            # by every function in the account. Without a cap, a public
            # unauthenticated tool being hammered — BRIEF.md §3's "open
            # invitation to burn compute" — could consume that pool and
            # throttle Xenia's production handlers. A free lead-gen toy taking
            # down the product it exists to promote would be a bad day.
            #
            # Reserved concurrency both guarantees these slots AND caps the
            # function at them, so the sandbox can never take more however
            # hard it is pushed. 20 is generous for single-digit users per
            # year (RESEARCH.md §D) and leaves 980 for everything else.
            #
            # The cost of the cap is that a genuine traffic spike gets 429s
            # rather than scaling. For a free sandbox that is the correct
            # trade: throttling this is always better than throttling Xenia.
            reserved_concurrent_executions=20,
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
