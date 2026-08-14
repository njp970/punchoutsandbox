# PunchOut Sandbox

**A hosted virtual supplier for testing e-procurement integrations.**
cXML PunchOut, OCI, order confirmation, dispatch notification, and the invoice
flow back — pointed at by anyone building a buyer-side punchout client who has
nowhere else to point it.

Live at [punchoutsandbox.com](https://punchoutsandbox.com) *(not yet deployed)*.

---

## Why this exists

Every free punchout tool in existence runs the wrong way. They simulate a
*buyer* so that a *supplier* can check their catalogue works. Nobody built the
mirror image, so a developer writing the buyer side has nothing to test
against — and can only test their integration against itself.

That distinction is the whole point, and it is worth being precise about:

> Round-tripping your own `build` → `extract` proves the two halves agree with
> each other. It does **not** prove either conforms to the spec. If both share
> a misreading, every test passes and the first real supplier rejects every
> document.

What is missing is an **independent judge** — and, per [RESEARCH.md](RESEARCH.md),
no vendor or network anywhere issues one. TradeCentric, Greenwing, Cloudfy,
Coupa, SAP Ariba and Jaggaer all do bilateral, buyer-coordinated sign-off
instead. There is no portable conformance report at any price.

This service is that judge. Everything else here is a plausible-looking shop
that exists so the judge has something to judge.

## What it does

- **Serves a real punchout session.** Point your cXML `PunchOutSetupRequest`
  or OCI form POST at it, browse a catalogue, return a cart.
- **Validates every document against the actual cXML DTDs** (vendored 1.2.071),
  in both directions, and tells you exactly what is wrong and where.
- **Separates errors from advisories.** Errors come from the DTD and are not a
  matter of opinion. Advisories are things a DTD cannot express but that break
  real integrations — a dropped `SupplierPartAuxiliaryID`, mixed currencies,
  totals that do not add up, `each` where `EA` was meant.
- **Runs the whole downstream flow**: order confirmation, dispatch notice,
  and invoices with multi-country tax (VAT, GST, sales tax, reverse charge).

## Status

Early build. See [BRIEF.md](BRIEF.md) §8 for the honest commercial read — this
is deliberately a **free tool**, not a product. It is worth more as
credibility and reach into the audience that buys procurement software than as
revenue.

| Area | State |
|---|---|
| CDK infrastructure | scaffolded |
| Hardened XML parsing + DTD validation | working, smoke-tested |
| Design system | built |
| Mock catalogue, tax engine, UI templates, handlers | in progress |

## Repository layout

```
app/                 the Lambda application
  xml_safe.py        THE hardened parser — nothing else may touch raw XML
  validation.py      the independent judge; this module is the product
  cxml/dtd/          vendored cXML 1.2.071 DTDs (see that directory's README)
  ui/                design system and templates
infra/               Python CDK — DataStack + SiteStack
  scripts/           Cloudflare DNS + edge-secret deploy
BRIEF.md             the idea, the case against, and the verdict
RESEARCH.md          market evidence behind that verdict
HOSTING.md           hosting plan, naming, and the AWS account layout
```

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Deploying

```bash
cd infra && cdk deploy --all && python scripts/deploy_cloudflare_dns.py
```

Cloudflare credentials come from the environment (`CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID`) and are never committed. See the deploy script's
docstring.

## Licence note

The vendored cXML DTDs are third-party. **Their licence terms could not be
established** — cxml.org's own licence link is broken. Read
[app/cxml/dtd/README.md](app/cxml/dtd/README.md) before making this repository
public.

Generated invoices and PDFs are synthetic, always watermarked as test
artifacts, and always for invented companies. Never generate documents
carrying a real company's name or branding.
