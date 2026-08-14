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

## 2. AWS: logically separate, same organisation

**Create a new member account in the existing AWS Organization.**
(AWS Organizations → Add an AWS account.) Same payer, same bill, but its own
account ID, own IAM boundary, own resource namespace, own blast radius. If a
free lead-gen toy gets hammered, Xenia's production account is untouched.

**Caveat worth knowing before you commit:** AWS Free Tier is aggregated at the
*organization* level under consolidated billing. A new member account does
**not** get a fresh 12-month free tier — the always-free allowances (Lambda 1M
req/mo, DynamoDB 25GB, CloudFront 1TB) are shared with Xenia. At the traffic
in RESEARCH.md §D this is irrelevant: the allowances are three-plus orders of
magnitude above expected load. A genuinely independent free tier would need a
standalone account with its own payment method — not worth the admin.

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
