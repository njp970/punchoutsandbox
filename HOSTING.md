# Hosting plan + naming — AWS, free tier

*Written 2026-08-14. Follows the BRIEF.md §8 decision: host the mock Xenia
needs anyway as a free, signup-gated lead-gen tool. No billing, no SLA.*

---

## 1. Naming

Availability checked by whois on 2026-08-14. **All of the below were
available at time of checking — verify again at purchase.**

### Recommendation: `punchoutsandbox.com`

- "Sandbox" is the word this audience already uses — TradeCentric's own Coupa
  FAQ answers *"Is there a sandbox environment...? Yes"*, and Ariba/Jaggaer
  both call their test environments sandboxes. It needs no explanation.
- Exact-match for the high-intent search. Search volume is tiny (RESEARCH.md
  §D: single-digit posts/year) but intent is near-total — being the literal
  answer to "punchout sandbox" is the entire marketing strategy.
- Reads as a tool, not a hobby project — matters given §3's warning about
  signalling cheap to enterprise readers.
- Product name: **PunchOut Sandbox**.

**Global TLD only — no country domains.** The audience is worldwide (the
buyer-side developers found in RESEARCH.md were on Dynamics, Oracle APEX and
S/4HANA, not UK-specific), and a `.co.uk` would reinforce precisely the
"small local hobby project" signal §3 warns against. `.com` is the correct
choice: it is the only TLD that is globally neutral *and* carries no
sub-brand connotation.

TLD sweep for `punchoutsandbox`: **AVAILABLE** — `.com` `.io` `.net` `.org`
`.cloud` `.co` `.tech` `.xyz`. **Taken** — `.dev` `.app` `.tools`.

### The real trade-off to decide

| Domain | Case for | Case against |
|---|---|---|
| **punchoutsandbox.com** | Best search intent; instantly legible | Boxes you into punchout — §4's moat is breadth (cXML *and* OCI *and* PEPPOL/UBL invoicing), and PEPPOL is not punchout |
| **suppliersandbox.com** | Breadth-proof; survives adding invoicing | Vaguer; loses the exact-match search |
| **mocksupplier.com** | Developer-native vocabulary — the four repos found in RESEARCH.md are literally named `mock-punchout`, `mock-punchout-catalog`, `punchout-simulator` | "Mock" reads throwaway to a procurement buyer |

`suppliersandbox.com/.io/.net` and `mocksupplier.com/.io/.net` all available.

**Suggested buy: `punchoutsandbox.com` as the primary, plus
`mocksupplier.com` as a cheap redirect** capturing the developer-vernacular
search (~£20/yr total). Both global. Don't over-buy the long tail — `.io`
and `.net` only matter if someone else starts squatting, and at this
market's visibility nobody will.

### Also available (checked, not recommended)
`cxmlsandbox.com` · `punchoutmock.com` · `stubsupplier.com` ·
`supplierstub.com` · `supplierzero.com` · `nullsupplier.com` ·
`fakesupplier.com` · `punchouttest.com` · `cxmltest.com` · `punchoutdev.com` ·
`sandboxandsons.com` — all `.com`, all global.

Taken: `punchout.dev` · `cxml.dev` · `punchin.dev/.io` · `counterparty.dev` ·
`testsupplier.com` · `virtualsupplier.com` · `punchoutlab.com` ·
`acmesupply.co`

---

## 2. AWS: the existing Xenia account, eu-west-2

**Decision (2026-08-14): deploy into Xenia's own AWS account.** A separate
member account was considered and rejected — more admin than the isolation was
worth for a free tool, and the free-tier allowances are aggregated across an
organisation anyway, so a member account would not have bought a fresh one.

Nothing collides. Stacks are `PunchoutSandbox-*` against Xenia's `Xenia-*`;
resources are `punchout-sandbox-*`. The account is already CDK-bootstrapped,
so there is no bootstrap step.

### The one thing co-tenancy actually costs

Not naming — the **account-level Lambda concurrency pool**, which defaults to
1000 and is shared by every function in the account. A public,
unauthenticated tool getting hammered (BRIEF.md §3's explicit risk) could
consume it and throttle Xenia's production handlers.

`SiteStack` sets **`reserved_concurrent_executions=20`**. Reserved concurrency
both guarantees those slots and caps the function at them, so the sandbox can
never take more than 20 however hard it is pushed, leaving 980 for everything
else. **If this file is ever refactored, check that setting first** — it is
the single line that makes sharing the account safe.

The trade-off is that a genuine spike gets throttled rather than scaling.
For a free sandbox that is correct: throttling this is always better than
throttling Xenia.

### Blast radius: what is NOT isolated

Being honest about what was given up. Sharing an account means shared IAM
boundary, shared CloudTrail, shared service quotas beyond concurrency, and a
compromise of this Lambda starts inside the same account as production. The
mitigations are that its execution role grants access to its own DynamoDB
table and nothing else, and that it holds no customer data — only synthetic
documents about invented companies. That is a reasonable posture for what
this is, and it would not be if the sandbox ever handled anything real.

### Architecture — all permanent free tier

| Piece | Service | Why / free-tier note |
|---|---|---|
| App | **Lambda container image** | 1M req/mo always free. *Container image, not zip* — this is what lets you bundle `lxml` + the cXML DTDs and do real DTD validation. That was the blocker in BRIEF §2; it disappears here. |
| Entry point | **Lambda Function URL** | Free forever. Avoid API Gateway — its free tier expires after 12 months. |
| CDN / TLS / domain | **CloudFront + ACM** | 1TB out + 10M req/mo, permanent. Free certs. Caches the catalogue pages and gives you somewhere to bolt WAF on later if abused. |
| Sessions + signups | **DynamoDB on-demand** | 25GB always free. Put a TTL attribute on punchout sessions — expiry deletes are free. |
| PDFs | **generate on the fly, don't store** | Kills both the storage cost and §3's abuse vector. If you must store, S3 with a 24h lifecycle rule. |
| Signup email | **SES** | ~£0.08/1000. Needs domain verification and a sandbox-exit request — do that early, it takes a day. |
| Config | **SSM Parameter Store** (standard) | Free. |
| Logs | **CloudWatch Logs** | 5GB/mo ingest free. **Set 7-day retention** — otherwise this becomes the only real line on the bill. |
| DNS | **Route 53** (£5/yr/zone) or **Cloudflare** (free) | Route 53 if you register there; Cloudflare free if you want a true £0. |
| Deploy | separate repo + CDK/SAM, GitHub Actions via **OIDC role** | No long-lived access keys into the new account. |

**Expected run cost: £0/month AWS**, plus ~£20/yr domains and optionally £5/yr
for a Route 53 zone.

### Notes
- Cold start for a container-image Lambda carrying `lxml` is roughly 1–2s.
  Punchout sessions are interactive but human-paced; acceptable. Don't pay for
  provisioned concurrency.
- Keep **all** state in DynamoDB, never on the Lambda filesystem.
- Abuse control (§3): start with the signup gate plus DynamoDB-counter rate
  limiting inside the app. AWS WAF is ~£4/month for a web ACL — add it only if
  abuse actually materialises, not upfront.
- The DTD validation is the product. Validate every inbound
  `PunchOutSetupRequest` *and* every document you emit, and show the user the
  validation result — that is the "independent judge" from BRIEF §2 and the
  only thing here nobody else offers.

### Next steps
1. Register the domain (a purchase — yours to make, not mine).
2. Create the member account in Organizations.
3. Verify the domain in SES and request sandbox exit early.
4. Scaffold the CDK app + Lambda container.

---

## 9. Knowing when a limit is hit

`ANON_DAILY_QUOTA = 25` is an admitted guess, and until now nothing recorded
whether anyone reached it — a stranger hit the wall, saw a page, and left. A
limit nobody observes is a limit nobody can tune. Three ways to see it now, in
descending order of how little effort they demand:

**1. Email, unprompted.** The first time an IP exhausts its daily allowance,
`app/mailer.py` sends to `CONTACT_TO`. Capped at `ALERT_DAILY_LIMIT = 3` a
day across all visitors, so one bad afternoon cannot bury the next real alert.

The alert deliberately reports a *hashed* source tag rather than an address,
and the distinction it exists to support is this:

| What the alerts look like | What it means | What to do |
|---|---|---|
| Same tag, repeatedly | One person doing real work | 25 is too low — raise it |
| A different tag each time | A scraper walking through addresses | 25 is about right |

**2. The log, on demand.** Every event is one JSON line in
`/aws/lambda/punchout-sandbox-prod`, retained three months.

```bash
aws logs tail /aws/lambda/punchout-sandbox-prod --since 7d --profile xenia --region eu-west-2 --filter-pattern '{ $.event = "anon_quota_exhausted" }'
```

For the distribution rather than the breaches — which is the more useful
number, because it says how close people get before stopping — CloudWatch Logs
Insights over the same group:

```bash
aws logs start-query --log-group-name /aws/lambda/punchout-sandbox-prod --start-time $(($(date +%s) - 604800)) --end-time $(date +%s) --profile xenia --region eu-west-2 --query-string 'fields @timestamp, ip | filter event = "anon_quota_exhausted" | stats count() by ip'
```

**3. Someone tells you.** The 429 page and `/contact` are both open, and
`tests/test_contact.py` §10 pins the property that makes that work: the
validation counter and the contact counter are separate, so a person rate
limited out of the tool can still say so.

### What is deliberately not here

No CloudWatch alarm, no SNS topic, no dashboard. An alarm would need a metric
filter, a metric, an alarm and a confirmed subscription — four resources to
deliver the same email the Lambda already sends itself, on a service whose
expected traffic is single-digit users per year. Revisit if that assumption
ever turns out to be wrong; the log lines are already in the right shape for a
metric filter to extract.

## 10. Outbound mail

SES, in the shared Xenia account, sending as `contact@punchoutsandbox.com` —
its own DKIM-verified identity (`infra/scripts/setup_ses_domain.py`), not a
borrowed Xenia domain.

Reputation in SES is account-level and cannot be partitioned, so the thing
protecting Xenia's deliverability is not the separate domain. It is that
**the recipient is an environment variable and `mailer.send()` has no `to`
parameter**: every message goes to one mailbox that asked for it, a stranger's
address travels only in `Reply-To`, and no stranger can generate a bounce or a
complaint against this account. `tests/test_contact.py` §3 asserts that
property directly, including that nobody has added a recipient argument.

The IAM grant is scoped to one identity *and* one From address by an
`ses:FromAddress` condition — the resource ARN alone would still permit any
address at the domain.

## 11. Outbound delivery, and why it is the riskiest thing here

`/order` accepts a purchase order; the order screen generates confirmations,
ship notices and invoices; and `app/delivery.py` POSTs them to a URL the user
supplied. That last step is the only place this application makes an outbound
request on someone else's instruction, which makes it a textbook SSRF
primitive unless constrained.

| Constraint | Closes |
|---|---|
| Account required | Anonymous abuse; makes it attributable |
| https, port 443 only | Protocol smuggling, internal port scanning |
| Every resolved address must be global unicast | Loopback, private ranges, `169.254.169.254` |
| Connection pinned to the vetted address | DNS rebinding — checked one address, connected to another |
| No redirects followed | A 302 to an internal address bypassing all of the above |
| Certificate verified | A sandbox teaching people to skip TLS verification on a channel carrying shared secrets |

Two things bound the damage independently: the Lambda is **not in a VPC**, so
it has no route to anything private in the account, and **Lambda has no
instance metadata service** to steal credentials from. Neither is a reason to
relax the list — they are why a mistake in it would be survivable.

**No automatic retries**, which reverses the cXML spec deliberately. The spec
tells suppliers to retry a transport failure hourly for ten hours. Right for
production, wrong for a sandbox: the user is trying to *see* the failure, and
automatic retries would also make any delivery endpoint a modest amplifier —
one submission, ten outbound requests.

---

## 12. QA — what a full pass covers, and what it found

`tests/qa_live.py` drives a **deployed** instance end to end: signup, punchout,
storefront, cart return, OCI, purchase order, confirmation, ship notice,
invoice, delivery — then attacks it. 147 checks.

```bash
ORIGIN_URL=https://<fn>.lambda-url.eu-west-2.on.aws .venv/bin/python tests/qa_live.py
```

It exists because every bug that has actually reached production here slipped
through the gap the unit suites cannot cover: they run against in-process
stores and an in-process handler, so nothing that is wrong only *when
deployed* can fail them.

The first full pass found six defects. Three were serious:

| Severity | Defect | Fix |
|---|---|---|
| **High** | The Lambda Function URL was reachable directly, bypassing Cloudflare and every rate limit. `EDGE_SHARED_SECRET` was applied post-deploy by the Worker script while the CDK stack's `environment` dict omitted it — so **every `cdk deploy` silently deleted it** | Both paths now read one value from `infra/.env`; a deploy restores it, and a missing value stops the deploy |
| **High** | Anonymous carts lived in a module-level dict, shared by every request a warm Lambda container handled — **two strangers browsing at once saw each other's carts** | Per-browser cart holder in DynamoDB, cookie-keyed; a read still writes nothing |
| **High** | A punchout session survived exactly one page view. The StartPage URL carries `?session=`; no storefront link does, so the banner vanished on the first click and the cart return answered 409 — the core flow, broken for real browsers and invisible to `/console`, which sets the cookie itself | The session cookie is issued on first sight, centrally in the dispatcher |
| Medium | Cloudflare's Browser Integrity Check answered **403** to `Python-urllib/*`, `libwww-perl/*` and UA-less clients — i.e. to buyer systems, which got a Cloudflare HTML page instead of a cXML `Status` | Configuration Rule disables BIC on the three machine endpoints only |
| Medium | The site served content over plain `http`, and session cookies lacked `Secure` — while the comment beside one claimed it had it | Always Use HTTPS, HSTS, `Secure` on all three cookies |
| Low | No `Content-Security-Policy`, `X-Content-Type-Options` or `Referrer-Policy` | Added centrally in `Response.to_lambda` |

Every one of the three high-severity defects is now pinned by a test that
fails if it returns.

### What passed first time

Hostile XML (XXE against `file://` and cloud metadata, entity expansion, deep
nesting) refused on every entry point. Stored-XSS payloads in a buyer's
`OrderRequest` escaped everywhere they are rendered. Cross-account access to
orders and generated documents refused. SSRF blocked at the send step as well
as in settings, including IPv6 loopback and a hostname that resolves privately.
Path traversal, method handling, credential rejection, and the anonymous rate
limit firing at the right place.

### Deliberately not "fixed"

**`X-Frame-Options` is absent.** Some buyer platforms open a punchout catalogue
in an iframe, and a supplier that refuses to be framed does not work for them.
A cart of invented products is not worth clickjacking.

**Browser Integrity Check stays on for the browser pages**, including
`/validate`. Scripted bulk validation is what the per-IP quota is for.

---

## 13. Cloudflare credentials

`infra/scripts/cloudflare_credentials.py` is the only place that answers "how
do I authenticate to Cloudflare". It resolves in this order:

1. `CLOUDFLARE_API_TOKEN` in the environment — an explicit override, and how
   you test a new token before storing it.
2. **AWS Secrets Manager `xenia/dev/cloudflare/dns-token`** (eu-west-2) — the
   normal path. Needs AWS credentials, so `AWS_PROFILE=xenia`.
3. `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY` — the legacy Global API Key,
   deprecated and last.

Every deploy script prints which one it used, because silently choosing among
three is how you end up debugging a 403 against the wrong credential.

### What this replaced, and why

Each script carried its own copy of the same `_auth_headers()`, and all of
them read from `infra/.env` — so rotating the credential meant editing a file
on one laptop and nowhere else. The Global API Key in that file stopped working
mid-session with `9103 Unknown X-Auth-Key`, which looks *identical* to a scoped
token stored in the wrong variable, because the two are sent as different
headers. The failure message now says so explicitly.

The dead value can stay in `infra/.env`: Secrets Manager resolves ahead of it,
so it is inert.

### A label is not a scope

That secret is described as covering `onxenia.com`. It actually reaches six
zones including `punchoutsandbox.com` — established by asking Cloudflare, not
by reading the description. `zone_id()` reports which zones a credential can
actually see when a lookup fails, because "zone not found" otherwise reads as
"the zone is missing" rather than "this token cannot see it".

### What QA could not have found

The suite above is 147 checks against the deployed site and it is worth having.
It did not find the two worst bugs in this application.

Both were found by an outside integrator pointing a real buyer system at the
service for an afternoon:

- **A punchout session survived exactly one page view.** Invisible to QA
  because the QA client signs up before it does anything, and therefore always
  carries an account cookie.
- **A stale `pos` cookie shadowed a fresh StartPage URL.** Invisible because
  the QA client starts each run with an empty jar, so it never carries the
  residue a real developer's browser does.

The pattern is the same in both: **the harness was well-behaved in exactly the
way that hid the bug.** A test client that signs up first cannot see a gate
that only affects people who have not. A test client with no cookie history
cannot see a precedence bug between a cookie and a URL.

That is not an argument for more checks. It is an argument for the thing the
checks cannot replace — someone using it for real, whose setup is untidy in
ways nobody thought to simulate. Both bugs are now pinned by tests
(`test_sessions.py` §5b–5e), but the tests were written *after* the report, and
would not have existed without it.
