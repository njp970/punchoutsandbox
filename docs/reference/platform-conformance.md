# Platform conformance — what actually breaks integrations

*Compiled 2026-08-14 from the cXML User's Guide 1.2.037 and Reference Guide
1.2.071, the SAP Ariba Catalog Format Reference (PUBLIC 2022-02), the Ariba
Network Level 1 PunchOut Catalog Guide (SAP 2021), the JAGGAER Supplier
Integration Specification (294pp, rev 07.17.23), Dell Premier's cXML mapping
guide, Amazon Business PunchOut Technical Administration v1.0, Four51's cXML
PunchOut Implementation Guide v2.2, and NC E-Procurement's published spec.*

---

## The finding that reframes the product

**The cXML specification imposes almost no length limits.** A grep of the
646-page Reference Guide and the 536-page User's Guide turns up exactly two
`maxlength`-style statements in the entire specification, both 255, both on
company-type fields.

Every limit in §2 below is **platform-imposed**, not schema-imposed. Which
means:

> **A document can be perfectly DTD-valid and still be rejected or silently
> truncated by every buyer network on the market.**

So `validation.py`'s DTD pass is necessary and nowhere near sufficient. The
advisory layer is not a nice-to-have bolted onto the real product — for a
large class of real failures it *is* the product, because the DTD has nothing
to say about them. This also vindicates the errors/advisories split: we can
report a platform limit as an advisory with a named source, without claiming
the document is invalid, which it isn't.

Corollary for positioning: what we offer is **platform conformance**, not
merely schema validity. Nobody else offers either.

---

## 1. Top failure modes, ranked by how badly they bite

### 1.1 `edit` with an empty cart DELETES the buyer's requisition lines
The highest-severity behaviour in the protocol. cXML User's Guide §5.6.4.3,
operation-dependent empty-cart semantics:

- `inspect` → item list **must be ignored**; supplier should return no `ItemIn`.
- `create` + no `ItemIn` → nothing added; the user cancelled.
- **`edit` + no `ItemIn` → existing items from this session must be DELETED.**

A supplier that returns an empty cart on an `edit` timeout silently wipes the
buyer's lines. Both sides need to be tested against this.

### 1.2 The `Z` timezone designator is prohibited
User's Guide §3.1.6.2, verbatim: *"The 'Z' time zone designator is not
allowed."* cXML requires local time with a numeric UTC offset.

Every naive `datetime.utcnow().isoformat() + "Z"` or Java `Instant.toString()`
emits an illegal timestamp. This is likely the single most violated rule in
the spec — and note the asymmetry that makes it survive testing: **the DTD
enforces the attribute's presence, never its format** (*"validation of the
value's format depends on your application"*). It passes DTD validation and
fails at the application layer, or silently doesn't fail at all.

Mandatory advisory check.

### 1.3 `?`, `{`, `}` in Supplier Part ID or Aux ID are a hard reject
SAP Ariba states this three separate times. It is a validation error, not a
truncation. Suppliers that base64- or JSON-encode session state into the
SupplierPartAuxiliaryID hit `{`/`}` constantly.

### 1.4 `deploymentMode` defaults to `production`
`/cXML/Request/@deploymentMode` — allowed values `production` (**default**) or
`test`. A test harness that simply omits it is treated as production, silently.

Note it lives on `Request`, not on `cXML` and not on `PunchOutSetupRequest`.
`PunchOutOrderMessage` is wrapped in `Message`, which carries its own
`deploymentMode` — read it off the wrong node and you get nothing.

Ariba resolves the punchout URL from **account configuration**, not from the
document; `SupplierSetup/URL` is deprecated and ignored. So the only thing
separating test from production is the ANID/realm plus this attribute.

### 1.5 Unit-of-measure handling differs by platform — silently
- **JAGGAER: unmapped UOM values silently default to `EA`.** No error. Stated
  consequences include wrong data shown to the user and downstream invoice
  problems.
- **Coupa: the cart import FAILS if the UOM does not already exist.**
- **Many Oracle systems require `EACH`, not `EA`.**
- **Ariba systems very often do not have `UN`** as a valid UOM.

Documented JAGGAER price failure: 1 Box of 30 gloves at £9.99 returned as
qty 30 `EA` at £9.99 → the buyer computes £299.70. Packaging size belongs in
the Description, never the UOM (`<UnitOfMeasure>100/BX</UnitOfMeasure>` is
explicitly "Not Recommended").

### 1.6 `cXML-urlencoded` must be us-ascii — even when it declares UTF-8
User's Guide §3.1.12: for `cXML-urlencoded` the receiving parser cannot assume
a charset, so the document **must use us-ascii encoding**, and *"the receiving
parser must ignore any encoding attribute in the XML declaration"* because the
browser may have changed it.

A product description containing `é`, `®`, `€` or CJK sent via
`cXML-urlencoded` is spec-violating **even though it declares UTF-8**. It must
go via `cXML-base64`, or every non-ASCII character must become a numeric
entity. This is the root cause of the mojibake-in-requisitions bug that is
endemic in real punchout.

Also: *"Suppliers should never URL encode the cXML-urlencoded field"* — the
browser does it. Double-encoding is the classic mistake.

### 1.7 HTTP status vs cXML status is a layering trap
*"All HTTP replies that don't include valid cXML content… are considered
transport errors [and] should be treated as transient and the client should
retry."* Recommended retry policy is **10 attempts, hourly, minimum six-hour
window**.

So: HTTP 500 carrying a valid cXML `<Status code="400">` is a *permanent*
error and stops. HTTP 500 carrying an HTML error page is *transient* and gets
retried ten times. Suppliers that emit HTML error pages on failure get
hammered.

### 1.8 BuyerCookie must not be used to track sessions
User's Guide §5.3: *"Do not use the BuyerCookie to track PunchOut sessions,
because it changes for every session, from create, to inspect, to edit."*
Suppliers that key their session store on it work for `create` and break on
`edit`. The supplier must return it **unchanged**.

### 1.9 SupplierPartAuxiliaryID must not change on edit/inspect
Ariba composes item identity as `SupplierID + SupplierPartID +
SupplierPartAuxiliaryID`. Change the aux ID on an `edit` round-trip and the
returned line is a *different item* — the buyer cannot reconcile it and
duplicates the line rather than updating it.

JAGGAER adds a live hazard: users can **copy a previous requisition** into a
new cart, **and the SPAID copies with it**, so suppliers receive stale
session tokens. JAGGAER's own advice is to ask them to disable copy-cart.
Four51 documents the resulting error as an opaque
`Exception has been thrown by the target of an invocation`, cause: *"SPAIDs
from a different environment (test vs. production)"*.

Dell inverts the field for Oracle buyers: aux ID null, cart ID carried in
`SupplierPartID` instead — the same supplier emitting structurally different
carts per buyer platform.

---

## 2. Consolidated limits matrix

Where platforms disagree, **the tighter value is the safe target.**

| Field | Safe max | Tightest source | Forbidden chars | Failure mode |
|---|---|---|---|---|
| `SupplierPartID` | **100** | JAGGAER | `?` `{` `}` (Ariba) | Ariba validation error; JAGGAER cart-return failure |
| `SupplierPartAuxiliaryID` | **100** (50 if Cisco-style verbatim echo matters) | JAGGAER / Cisco | `?` `{` `}` | Failed cart return; opaque exceptions |
| `Description` | **255** | Ariba display; JAGGAER 256 | unescaped `&` `<` — use CDATA | **Silent truncation on the PO** |
| Short Name | **50** | Ariba Network | — | truncation |
| `ManufacturerPartID` / `Name` | **100** | JAGGAER | special chars | — |
| `UnitOfMeasure` | UN/CEFACT Rec 20 code | Ariba prefers UNUOM | — | JAGGAER silently defaults to `EA`; Coupa import fails |
| `Classification` (UNSPSC) | **8 digits**, no punctuation | JAGGAER POOM | dashes, periods, prefixes | not categorised |
| Currency | 5 | Ariba | — | — |
| `UnitPrice` | **≤4 decimals** | JAGGAER | `$` `,` | rounding → price mismatch |
| Header Comments + Extrinsics | **100 combined** | JAGGAER | — | silent truncation; comments consume the budget first |

**Ariba's own asymmetry:** the CIF field table declares Supplier ID and
Supplier Part ID as `String 255`, then states the application supports **128**.
Transport-level and application-level limits differ.

**Ariba limits are in BYTES, not characters.** Extended-ASCII ≈ 2 bytes,
CJK ≈ 3. Against the 2000-byte description cap, Japanese descriptions max out
around **666 characters**. And the description accepts 2000 but **only the
first 255 appear on requisitions and purchase orders** — pass validation,
display fine in search, get silently cut on the actual PO.

**UTF-8 files must not carry a byte-order mark** — Notepad adds them
automatically and Ariba errors on them.

**No cart line-item cap is documented anywhere.** `499 Document Size Error`
exists but is unquantified; `468 Catalog Too Large` (4MB) applies to catalog
uploads, not carts. The real ceiling is the buyer's HTTP POST body limit after
browser URL-encoding, which inflates roughly 3× for XML.

---

## 3. Status codes worth getting right

| Code | Meaning |
|---|---|
| `200` | OK |
| `204` | **In a PunchOutOrderMessage: session ended with no change to the cart** |
| `400` | Unacceptable, **although it parsed correctly** |
| `401` | Credentials in the **Sender** element not recognised |
| `406` | Unacceptable, **likely a parsing failure** — the spec's preferred code for validation errors |
| `412` | **Precondition failed — e.g. an `edit` with no matching punchout session**, or the client ignored `operationAllowed` |
| `450` | Not implemented — e.g. the requested operation is unsupported |
| `499` | Document too large |
| `500`/`550` | Transient; retry |

Structural rule most breakage violates: *"Servers should **not** include
additional Response elements (for example, a `PunchOutSetupResponse`) unless
the status code is in the cXML 200 range."* Returning `<Status code="500">`
alongside a `<PunchOutSetupResponse>` is malformed.

---

## 4. Credentials and identity

The Ariba shape carries **multiple sibling `<To>` blocks** — a parser reading
only the first `To/Credential` mis-routes. The `Sender` identity is a generic
Ariba service account (`sysadmin@ariba.com`), **not the buyer**; suppliers who
authenticate on Sender rather than From conflate all buyers into one.

The shared secret Ariba inserts is **the supplier's own**, looked up from the
supplier's account — not the buyer's. Suppliers expecting a per-buyer secret
break on their second customer.

Case inconsistencies that are real conformance hazards, all from vendor
documentation:
- The User's Guide prose says `NetworkId`; every example says `NetworkID`.
- Test ANIDs are suffixed `-T` in SAP's 2016 guide and `-t` in SAP's own
  community blog. Exact string matching fails in one realm or the other.
- The guide's own DOCTYPE examples mix `xml.cxml.org` and `xml.cXML.org`, and
  one omits `SYSTEM` entirely.

**Security finding worth surfacing in the UI:** User's Guide §3.1.7 says *"Do
not use authentication elements in documents sent through one-way
communication"* because the browser exposes the source — yet the
PunchOutOrderMessage *is* a browser form POST and Ariba's own sample includes
`<SharedSecret>` in it. A direct spec-versus-vendor contradiction.

---

## 5. Extrinsics guarantee nothing

*"The cXML specification does not define the content of Extrinsic elements."*
Ariba's canonical examples carry only `CostCenter` and `User`. Ariba's own
guidance is *"Ask the supplier's customers what data the supplier can expect
to receive."*

So **no extrinsic is ever guaranteed present.** A supplier that hard-requires
`UserEmail` breaks on the next buyer. Amazon accepts the user email in **four**
different locations (`Extrinsic name="UserEmail"`, `Extrinsic name="Email"`,
`ShipTo/Email`, `Contact/Email`) — a good "any-of" test.

Amazon also documents a silent-default hazard: omit
`Extrinsic name="UserBusinessUnit"` on a multi-group org and **the account
silently defaults to the root group** rather than erroring.

NC E-Procurement documents the opposite case — a *required inbound* extrinsic
(`Contract`, carrying the State Term Contract ID) that the supplier must
recognise and use to block non-contract items.

---

## 6. Scenarios this gives us

Directly implementable one-click break cases, each with a documented source:

1. `edit` returning an empty cart (destructive — deletes buyer lines)
2. Timestamp with `Z` designator
3. `{`/`}` in the SupplierPartAuxiliaryID
4. Aux ID changed between punchout and edit (duplicate line, not update)
5. Stale/replayed aux ID from a copied requisition
6. Non-ASCII description over `cXML-urlencoded` (mojibake)
7. UTF-8 with a BOM
8. Unmapped UOM (`each` / `PCE` / `UN` / `EACH`)
9. Unit price with 5+ decimal places
10. Description over 255 characters
11. `<Status code="500">` returned alongside a `PunchOutSetupResponse`
12. HTML error page instead of a cXML Status
13. Multiple sibling `<To>` credential blocks
14. Missing `BuyerCookie`; changed `BuyerCookie`
15. UNSPSC with punctuation (`44.12.21.04`) or a prefix (`UNSPC 234992835` —
    which appears in a vendor's *own published sample*)
16. `operationAllowed="create"` followed by an `edit` attempt (expect `412`)
17. Fractional, zero and negative quantities

## 7. Sources not yet retrieved

Cisco's cXML guide (403 to direct fetch) documents a "first 50 characters
verbatim" aux-ID rule worth confirming. ESM Solutions and Unimarket specs also
403 and are indexed as containing field definitions. JAGGAER's actual UOM
allowlist ("Standard Units and Unit Mappings") is portal-only and is the
concrete list we would most like.

**No named distributor** (Grainger, Staples, CDW, Insight, SHI, MSC, Fastenal,
Airgas, Henry Schein, VWR, McMaster, RS, Farnell) publishes a technical guide
publicly — all are NDA-distributed via enablement portals. That absence is
itself useful market evidence: the knowledge this document collects is not
freely available anywhere, which is part of why the gap in RESEARCH.md exists.
