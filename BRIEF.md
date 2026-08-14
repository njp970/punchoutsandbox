# A hosted cXML virtual supplier — idea brief

*Status: IDEA. Nothing built, nothing committed to. Written 2026-08-13 so a
fresh session can pick this up cold.*

*Origin: while building Xenia's Purchasing module we needed to test a
buyer-side punchout client and discovered there is nowhere to point it. This
brief is the evidence gathered on the way, plus an honest read of whether it is
a business.*

---

## 1. The idea, in one line

Host a **fake supplier** — cXML PunchOut, OCI, PO transmission, order
confirmation, ship notice, invoices, PDFs — that anyone building procurement
integrations can point their system at. Subscription access.

## 2. The gap is real, and this is the evidence

Not speculation. Verified twice during the Xenia build:

- **A research sweep** across the punchout tooling landscape found no free
  hosted supplier endpoint.
- **Three tools checked directly** on 2026-08-13:

| Tool | Direction | Useful to a buyer-side builder? |
|---|---|---|
| `punchout-tester.takeoffdigital.co.uk` | Buyer → supplier | No — same direction as us |
| `punchoutcommerce.com/tools` | Buyer → supplier, **plus** a cXML Visualizer, XML Validator, and a "cXML Test Responses" tool that runs supplier → buyer | Partly — the only one that runs the right way |
| `punchoutcatalogs.com/cxml-simulator` | Buyer → supplier | No |

**Every free punchout tool is buyer-side.** They exist so a *supplier* can
check their catalogue works. Nobody has built the mirror image.

Corroborating facts from the same research:

- **No maintained Python or Node cXML library exists.** The best open-source
  option is Ruby (`officeluv/cxml-ruby`, last release Jan 2021). Anyone doing
  this in a modern stack is hand-rolling against the DTDs, as we did.
- **TradeCentric / PunchOut2Go start around $10,000/yr** and are white-glove.
  That is the incumbent price point in the neighbourhood — they sell the
  integration, not a test harness, but it establishes that budget exists.
- **cXML DTDs are free** at cxml.org, no registration. No licensing obstacle to
  implementing a conforming supplier.

### The specific pain, stated precisely

A buyer-side integration can only be tested against itself. Xenia's own cXML
tests round-trip `build.py` → `extract.py`, which proves the two halves agree
with each other — **not that either conforms to the spec.** If both share a
misreading, every test passes and the first real supplier rejects every
document. There is no independent judge in the loop, and no cheap way to get
one: `defusedxml` and stdlib `ElementTree` do not validate against a DTD, and
`lxml` (which does) is a C extension you do not want in a Lambda bundle.

That is the thing worth selling: **an independent judge.**

---

## 3. The honest case against

Written first, deliberately.

**The market is thin.** Punchout is a mature, slow-moving standard. The buyer
side is dominated by Ariba, Coupa, Jaggaer, Oracle and SAP. New buyer-side
implementations are plausibly dozens a year globally, not thousands. The
addressable set is small and hard to reach.

**£10/month is the weakest part of the proposal.** At £120/yr you need
thousands of customers to make a business; at that scale of demand the market
almost certainly does not exist. Worse, the price *signals* hobby project to
exactly the enterprise buyer you want, who reads cheap as unsupported. This is
classic developer-tool underpricing.

**It is a focus tax.** Xenia has not launched. A second product — however small
— carries a support surface, an uptime expectation and a distraction cost.
"Low marginal cost to build" is not "low cost to run".

**Abuse economics.** "Unlimited queries" plus "a massive catalogue" plus
"generate fake PDFs" is an open invitation to burn storage and compute for
£10/mo.

---

## 4. The strongest version of it

Not "a test supplier". **Punchout conformance-as-a-service.**

- **Bidirectional.** Validate my buyer client *and* my supplier
  implementation. The buyer-side direction is unserved; the supplier-side
  direction is served badly by free tools with no reporting.
- **Ends in a report.** A conformance certificate a supplier can hand a
  prospective customer to shorten an onboarding cycle. That is what people pay
  for — not the harness, the *weeks removed from someone else's project plan*.
- **Priced per project, not per month.** £1–3k for a conformance engagement
  sits naturally next to the £10k incumbents and needs dozens of customers, not
  thousands.
- **Breadth is the moat**: cXML *and* OCI *and* PEPPOL/UBL invoicing, with
  deliberately awkward edge cases — partial ship, order rejection, credit
  notes, re-pricing, dropped `SupplierPartAuxiliaryID` — that a real supplier
  will not produce on demand and that are exactly where integrations break.

---

## 5. Why Xenia is unusually well placed

- **We are building the mock anyway.** A self-hosted cXML/OCI supplier mock is
  already in `docs/design/procurement/BUILD.md` §10 as a build dependency. The
  marginal cost of hosting what we need regardless is small.
- **We have the domain model.** `services/procurement/` already implements
  both protocols reducing to one canonical shape, a hardened XML parser, the
  PEPPOL/UBL invoice leg, and the awkward-case handling.
- **Developer marketing.** It reaches precisely the audience that buys
  procurement software. It may be worth more as credibility and lead
  generation than as revenue.

---

## 6. Legal / ethical constraints

- Synthetic catalogues, invoices and PDFs are fine **for invented companies**.
  "ACME Ltd" — fine. Generating realistic fake **Amazon** or **Staples**
  invoices would be a real problem: trademark, and a document that could
  function as a forgery outside the sandbox.
- Watermark every generated PDF as a test artifact.
- Do not host anyone's real catalogue data.

---

## 7. What a new session should do FIRST

Do not build. Test the market claim, cheaply.

1. **Find five people who hit this in the last year.** Procurement
   integrators, ERP implementation teams, e-procurement vendors. If they are
   easy to find, the market is bigger than this brief assumes. If five cannot
   be found, that is a cheap and decisive answer — and we still get the mock
   Xenia needs regardless.
2. **Check what TradeCentric, Greenwing and Cloudfy actually bundle.** If a
   test/certification environment is already included in their engagements,
   the gap is narrower than it looks.
3. **Ask whether the pain is testing or CERTIFYING.** If people mostly want a
   document to hand a customer, build the report and treat the harness as
   plumbing. That reframing changes the whole product.
4. Only then scope a build.

---

## 8. Verdict as it stands

*Written 2026-08-13, after executing §7 steps 1–3. Evidence in RESEARCH.md.*

**The gap is real. The business is not — not as a standalone product.**

What the research confirmed:

- **Structurally unserved.** Zero hosted cXML test suppliers exist anywhere;
  the mirror-image (buyer simulators for supplier-side devs) exists several
  times over. No incumbent sells test tooling standalone, and no vendor or
  network anywhere issues a portable conformance report — validation is
  bilateral buyer-coordinated sign-off across TradeCentric, Greenwing,
  Cloudfy, Coupa, Ariba and Jaggaer alike. The "independent judge" does not
  exist at any price.
- **The five-people test roughly passes** — three buyer-side developers asking
  on Stack Overflow in 2024–2025, plus four separate individuals writing their
  own localhost mock-supplier repos in 2024–2026. But none of them asked for a
  hosted service and none shows willingness to pay. The coping behaviour is:
  build a throwaway mock, or pay middleware $15–50k/yr to own the problem.
- **The pain is testing, not certifying.** §4's conformance-certificate
  framing found no demand signal at all — no developer or supplier was found
  asking for a document to hand a customer. §4 was a supply-side invention
  and cannot be treated as validated.
- **Volume is yearly-scale.** Single-digit relevant public posts per year.
  High pain when hit (questions sit unanswered for years), near-zero search
  volume. A hosted supplier would win by being the only answer to an
  infrequent question, not by riding demand.

**Decision: build the mock Xenia needs anyway (BUILD.md §10). Host it as a
free tool, not a product.**

- No billing, no subscription, no certification reports, no SLA. Signup-gated
  to capture leads and cap abuse; watermarked PDFs; invented companies only
  (§6 stands).
- Its value to Xenia is credibility and reach into exactly the audience that
  buys procurement software — worth more than any plausible revenue, which the
  volume numbers say would be a few thousand pounds a year at best.
- The £1–3k conformance engagement stays on the shelf unless someone asks to
  pay for it. The free tool is the cheapest possible way to find out if that
  person exists: put a "need a conformance report for a customer? talk to us"
  link on it and wait. If nobody ever clicks, §4 is dead and it cost nothing
  to learn.
- Watch item: two of the 2026 mock repos are weeks old. If one turns into a
  hosted service, the free-tool window closes; that argues for hosting ours
  when the Xenia mock lands rather than someday.
