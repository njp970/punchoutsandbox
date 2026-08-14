# Market-claim research — §7 of BRIEF.md, executed 2026-08-13

Two research sweeps run in parallel: (A) what the incumbents actually bundle,
(B) public demand evidence and the testing-vs-certifying question. Findings
below, verbatim conclusions with sources; verdict written into BRIEF.md §8.

---

## A. What the incumbents bundle (§7, step 2)

**Headline: no vendor examined sells standalone test tooling, and none issues
a portable, customer-owned conformance report. Validation everywhere is
bilateral, buyer-coordinated sign-off.** The "independent judge" gap is real
across all six vendors examined.

### TradeCentric (formerly PunchOut2Go)
- Sells managed iPaaS-style integration ("Connect Packages"), typically
  supplier-funded. Includes a Business Intelligence Portal with connection
  testing and session simulation — but only inside a managed engagement.
- Sandbox: yes, included in engagements (their Coupa FAQ confirms a sandbox
  for PunchOut/PO/ASN/invoice validation pre-go-live). Not purchasable
  standalone.
- Certification: none. Validation is test-script execution with the buyer's
  technical contact; no customer-receivable artifact.
- **Public pricing: mid-market ($100M–$1B revenue) starts at $15,000–50,000/yr
  with a 3-year minimum.** (Updates BRIEF §2's "$10k/yr" figure upward.)
- Sources: tradecentric.com/punchout/coupa/, tradecentric.com/pricing/

### Greenwing Technology
- Service-led punchout catalog development + managed platform; 135+ connectors.
- A "Greenwing Technology Testing Suite" with "Punchout Mode Emulation" exists
  — but is visible only via a third-party OroCommerce extension listing, has no
  page on Greenwing's own site, and their own KB article "How to test your
  punchout catalog" is placeholder-empty. Appears to be an internal engagement
  tool, not independently purchasable.
- Certification: none for customers (CoupaLink partner badge only).
- No public pricing.

### Cloudfy
- UK SaaS B2B ecommerce platform; punchout is a platform feature for its own
  tenants, not neutral tooling. Nothing public on sandboxes, validation, or
  conformance. *Caveat: pages were largely demo-gated — this is a gap in
  evidence, not a confirmed absence.*

### The networks (brief)
- **Coupa**: no public supplier sandbox; punchout testing happens inside a
  specific buyer's Coupa test instance. "Coupa Verified" is a directory trust
  badge, not conformance; "CoupaLink Certified" certifies partners, not
  integrations.
- **SAP Ariba / SBN**: strongest sandbox story — linked test accounts
  (ANID + "-T"), a catalog validator/tester. But punchout *endpoint* testing is
  still buyer-driven (enablement team schedules a test cycle). Catalog
  validation is a pass/fail gate, not an exportable report.
- **Jaggaer**: per-engagement test environment via the buyer's project;
  "certification" is project sign-off, not a portable artifact.

### Incidental competitive finds
- Punchout Cloud (Shopify app): self-serve supplier-side punchout at a
  **published $399/month** — a rare public self-serve price point.
- punchoutcommerce.com operator unconfirmed (could not verify a Greenwing
  connection).

---

## B. Demand evidence (§7, steps 1 and 3)

**Headline: the gap is structurally confirmed — several free hosted buyer
simulators exist, zero free hosted cXML supplier simulators — but publicly
visible demand is thin: single-digit relevant posts per year. This is a
low-search-volume, high-pain-when-hit niche.**

### The "five people" test (step 1) — roughly met, with a caveat
People who verifiably hit the buyer-side testing problem recently:

1. A Microsoft Dynamics 365 Business Central developer building a buyer-side
   punchout receiver — three Stack Overflow questions, Jun 2025
   (SO 79669240, 79653161, 79649936).
2. An Oracle APEX developer building a requisition app, stuck receiving the
   PunchOutOrderMessage, testing against a *live supplier* for lack of
   anything else — Aug 2024 (SO 78837516, 472 views, no accepted answer).
3. An xPages developer, explicitly "setting up the Buyer-Side of a PunchOut
   solution" — Mar 2024 (SO 78089161).
4. Author of `nilsmartensson/oci-punchout-demo` (Jun 2026) — a cXML 1.2 mock
   supplier server built to test S/4HANA/Ariba external catalog integration.
5. Author of `slawomir-szostak/punchout-simulator` (Jun 2026) — includes a
   "Virtual Supplier" mode; README frames the problem as exercising punchout
   end-to-end "without finding a cooperative real partner".
6. Plus two more tiny fresh mock repos by individuals (`Naeda1902/mock-punchout`
   Aug 2026, `jengagnon2021-glitch/mock-punchout-catalog` Mar 2026).

So ≥5 findable — **but none of them posted asking for a hosted service, and
none demonstrates willingness to pay.** The observed coping behaviour is:
build a throwaway localhost mock, or pay middleware to make the problem go
away entirely.

### Volume signal
- Stack Overflow: ~3–6 punchout/cXML integration questions per year globally;
  explicitly buyer-side ~1–3/year; "where do I find a test supplier" asked on
  SO in the last 3 years: zero (the ask surfaces on SAP Community instead,
  rarely). Questions that are asked often go unanswered — two of the SO items
  above have 0 answers; an SAP Community "OCI Punchout free testing" thread
  (2018) waited **6 years** for its single answer (Nov 2024).
- Reddit: a handful of substantive threads/year; answers uniformly route to
  paid middleware (TradeCentric, Vurbis, Greenwing). (r/instapunchout is one
  vendor's self-spam — ignore its counts.)
- Hacker News: effectively zero, ever. GitHub issues: zero direct asks.
- Countervailing: 4 separate mock-supplier repos created by individuals in
  2024–2026 suggests the pain is under-expressed rather than absent — people
  in this niche build rather than post.

### Does any hosted test supplier exist? (secondary check)
- **OCI: one** — heth.biz/oci, a free hosted OCI 4.0 test catalog. German,
  single-maintainer hobby site (© 2022), built "because most OCI catalogs on
  the web require registration".
- **cXML: none.** Closest is punchoutcommerce.com/tools/cxml-test-responses —
  canned supplier→buyer responses by status code, no browsable catalog, no
  shopping session, no cart return. All other free tools (TakeOff Digital,
  punchoutcommerce tester, TradeCentric's simulator) simulate the *buyer* side.
- Historical confirmation of the gap: a 2008 SAP Community thread asking for a
  sample punchout catalog to test against got the accepted answer *"approach a
  supplier and ask them if they have one you could use."* Eighteen years
  later, that is still the state of the art.

### Testing vs certifying (step 3) — decisive
**Near-zero public evidence of certification demand.** Searches for "punchout
certification" / "cxml conformance" return supplier marketing and platform
onboarding guides, not developer questions. cxml.org offers no test
endpoints, no tooling, no conformance program — DTDs and PDFs only. The
observed need is practical debugging/testing during a build; platform-level
validation happens inside Ariba/Coupa onboarding with real trading partners.

**This undercuts BRIEF §4.** The "conformance certificate" framing was a
supply-side invention: nobody was found asking for a document to hand a
customer. The £1–3k per-project conformance engagement has no observed demand
signal. (Absence of public asks isn't proof of absence — enterprise pain often
never reaches forums — but it means §4 cannot be treated as validated.)
