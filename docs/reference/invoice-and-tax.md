# Invoice and tax — implementation reference

*Compiled 2026-08-14 from the cXML DTDs (1.2.060 and 1.2.071, diffed —
`TaxDetail`, `Tax`, `InvoiceDetailSummary`, `InvoiceDetailItemReference` and
`InvoiceDetailRequestHeader` are byte-identical between them apart from a
doc-comment typo), the OASIS UBL 2.1 XSDs, the CEF `eInvoicing-EN16931`
schematron, and the OpenPEPPOL BIS Billing 3.0 rules and example invoices.*

This is the spec for `app/cxml/invoice.py` and `app/tax/`. Read it before
writing either.

---

## 1. Corrections to common assumptions

These bit us in the research and will bite anyone implementing from memory:

1. **`TaxDetail@category` and `@purpose` are NOT enumerated.** Both are
   `%string;` (CDATA). `category` is `#REQUIRED`, `purpose` is `#IMPLIED`. The
   DTD names `sales`/`usage`/`vat`/`gst` as "defined categories" and adds
   *"other values are permitted"*. Real traffic uses `category="CA"` and
   `category="Standard Rate"`. **Our validator must never enum-check these**
   — doing so would reject valid documents, which is the one thing a
   conformance tool may not do.
2. **`exemptDetail` genuinely is enumerated:** `(zeroRated | exempt)`, nothing
   else. It is the only enumerated tax attribute.
3. **`Description` is `#REQUIRED` inside `Tax`**, with `xml:lang` required on
   it. It may be empty (`<Description xml:lang="en"/>`) but the element must
   exist. This is the single most common cXML invoice validation failure.
4. **`purpose` on the header has five values**, not four: `standard`,
   `creditMemo`, `debitMemo`, `lineLevelCreditMemo`, `lineLevelDebitMemo`.
5. **`UnitOfMeasure` and `UnitPrice` come FIRST in `InvoiceDetailItem`** —
   before `InvoiceDetailItemReference`. The reverse of the intuitive order.
6. **The indicator elements are not booleans.** `isTaxInLine` et al. accept the
   literal string `yes` and nothing else; absence means false. There is no
   `no`. Emit `<InvoiceDetailHeaderIndicator/>` for the all-false case — both
   indicator elements are mandatory and ordered even when empty.
7. **`shipTo`/`shipFrom` do not belong in `InvoicePartner`.** The DTD forbids
   it; they go in `InvoiceDetailShipping`.
8. **`ManufacturerPartID` and `ManufacturerName` are an all-or-nothing pair**
   — `(A, B)?`. One alone is invalid.
9. **cXML has no native reverse-charge construct.** See §3.

---

## 2. cXML InvoiceDetailRequest

```
InvoiceDetailRequest
  ├─ InvoiceDetailRequestHeader        (invoiceID, invoiceDate, purpose, operation)
  │    ├─ InvoiceDetailHeaderIndicator (isHeaderInvoice, isVatRecoverable)
  │    ├─ InvoiceDetailLineIndicator   (isTaxInLine, isShippingInLine, …)
  │    ├─ InvoicePartner*              (Contact role=, IdReference*)
  │    ├─ DocumentReference?           (REQUIRED for credit memos + delete)
  │    └─ InvoiceDetailShipping?       (shipFrom / shipTo live HERE)
  ├─ (InvoiceDetailOrder+ | InvoiceDetailHeaderOrder+)
  │    └─ InvoiceDetailItem+           (UnitOfMeasure, UnitPrice, then reference)
  └─ InvoiceDetailSummary              (SubtotalAmount, Tax, NetAmount mandatory)
```

`isHeaderInvoice="yes"` selects `InvoiceDetailHeaderOrder`; omitted selects
`InvoiceDetailOrder`.

**Summary cardinality:** `SubtotalAmount`, `Tax` and `NetAmount` are
mandatory. `DueAmount` is *optional* — surprising, given the credit-memo rules
are phrased in terms of it. If `purpose="creditMemo"` you must emit
`DueAmount` to satisfy the negative-amount requirement.

### Credit memos — two distinct mechanisms

| | Header-level | Line-level |
|---|---|---|
| `purpose` | `creditMemo` | `lineLevelCreditMemo` |
| `isHeaderInvoice` | must be `yes` | false |
| `DocumentReference` | — | **required**, identifies the original |
| Amounts | `DueAmount` negative | all line amounts and `DueAmount` negative |

Live practice keeps the **unit price positive and the quantity negative**,
with derived amounts negative. The DTD also permits a negative
`SubtotalAmount` with positive quantity. Pick one convention and hold it —
the sandbox should be able to emit both, since a buyer's parser will meet
both in the wild.

`reason="return"` on `InvoiceDetailItem` is the only enumerated credit reason.

### IdReference domains (tax-relevant subset)

`vatID`, `gstID`, `federalTaxID`, `stateTaxID`, `provincialTaxID`,
`taxExemptionID`, `companyRegistrationNumber`, `courtRegisterID`.

**`taxID` and `abn` are not in the domain list** — a common invention.
`domain` is `%string;` so the list is advisory, but staying on it is what
buyers expect. Access points such as Pagero additionally mirror VAT numbers
into `<Extrinsic name="supplierVatID">`; emitting both is the defensive
choice for a simulator.

---

## 3. Tax in cXML

```xml
<Tax>
  <Money currency="GBP">200.00</Money>              <!-- TOTAL tax -->
  <Description xml:lang="en">VAT</Description>       <!-- REQUIRED -->
  <TaxDetail purpose="tax" category="vat" percentageRate="20"
             taxRateType="Standard" taxPointDate="2026-08-14T00:00:00+01:00">
    <TaxableAmount><Money currency="GBP">1000.00</Money></TaxableAmount>
    <TaxAmount><Money currency="GBP">200.00</Money></TaxAmount>
    <TaxLocation xml:lang="en">United Kingdom</TaxLocation>
    <Description xml:lang="en">UK VAT @ 20%</Description>
  </TaxDetail>
</Tax>
```

`TaxAmount` is the only required `TaxDetail` child. Repeat `TaxDetail` per
distinct rate/category/jurisdiction; the parent `Tax/Money` is the sum. Use
`purpose` to separate goods tax from `shippingTax` / `specialHandlingTax` at
the same rate.

Line-level vs header-level is controlled entirely by
`InvoiceDetailLineIndicator@isTaxInLine`. **The summary `Tax` is mandatory
either way** and must carry the total. Lines omitting `Tax` when
`isTaxInLine="yes"` are treated as **zero**, not "unspecified".

### Zero / exempt / reverse charge

- **Zero-rated:** `percentageRate="0" exemptDetail="zeroRated"`.
- **Exempt:** `exemptDetail="exempt"`, optionally with
  `<TaxExemption exemptCode="M07"><ExemptReason xml:lang="en">…</ExemptReason></TaxExemption>`
  for regimes (Portugal SAF-T) that mandate a coded reason. `TaxExemption` is
  independent of and coexists with `exemptDetail`.
- **Reverse charge:** ⚠️ **no native construct exists.** The working pattern is
  `exemptDetail="exempt"`, `percentageRate="0"`, both parties' `vatID`
  present, the statutory wording in `TaxDetail/Description`, and
  `TaxExemption exemptCode="AE"` borrowing the UNCL5305 code so a downstream
  PEPPOL mapper can round-trip it. **This is convention, not standard** —
  flag it as such in the sandbox UI.
- **Triangulation** is the one EU case cXML models natively:
  `isTriangularTransaction="yes"` plus
  `TriangularTransactionLawReference` and a `subsequentBuyer` contact.

`basePercentageRate` and `isIncludedInPrice` are **Quote messages only**.

---

## 4. PEPPOL BIS Billing 3.0 / EN 16931 (UBL)

### Element order is schema-significant

Unlike cXML's DTD content models, a UBL instance with elements out of
sequence fails XSD validation outright. The trap that catches everyone
migrating from cXML:

```
… cac:AllowanceCharge → cac:TaxTotal → cac:LegalMonetaryTotal → cac:InvoiceLine
```

**Totals precede lines** — the inverse of cXML, where `InvoiceDetailSummary`
follows the orders. Two more inside `cac:Item`: `cbc:Description` comes
**before** `cbc:Name`, and `cac:ClassifiedTaxCategory` comes **after**
`cac:CommodityClassification`.

### UNCL5305 category codes — there are TEN

| Code | Meaning | Rate | Exemption reason |
|---|---|---|---|
| `S` | Standard rate | **> 0** | forbidden |
| `Z` | Zero rated | 0 | **forbidden** |
| `E` | Exempt | 0 | required |
| `AE` | Reverse charge | 0 | required |
| `K` | Intra-community supply | 0 | required |
| `G` | Free export, tax not charged | 0 | required |
| `O` | Outside scope | **absent** | required |
| `L` | Canary Islands IGIC | — | rules `BR-AF-*` |
| `M` | Ceuta/Melilla IPSI | — | rules `BR-AG-*` |
| **`B`** | **Transferred (VAT), Italy** — split payment | — | — |

**A hardcoded nine-code enum wrongly rejects Italian invoices.** Note also
that `BR-IC-*` governs `K`, not a code called IC.

### Cardinality and grouping

`S` is *at least one*; **`Z`, `E`, `AE`, `K`, `G`, `O` are EXACTLY ONE.**

Consequence worth simulating: **EN 16931 cannot express two different
exemption reasons on one invoice.** You must split the invoice. And
`BR-S-08` groups by **(category, rate)**, not category alone — the classic
bug is one `S` subtotal summing two rates.

`O` is **exclusive**: no other breakdown group, line category, or
allowance/charge category may appear on the same invoice.

### Party identity is a three-way split

| Path | BT | Meaning |
|---|---|---|
| `PartyTaxScheme/CompanyID` (`TaxScheme/ID = VAT`) | BT-31/48 | VAT number |
| `PartyTaxScheme/CompanyID` (`TaxScheme/ID != VAT`) | BT-32 | non-VAT tax registration |
| `PartyLegalEntity/CompanyID` | BT-30/47 | company registration number |
| `PartyLegalEntity/RegistrationName` | BT-27/44 | **legal name — where BR-06/07 look** |
| `PartyName/Name` | BT-28/45 | trading name only |

**Sending only `cac:PartyName` is a fatal BR-06 failure** even though the
invoice visibly "has a seller name".

Per-category identity requirements differ, and AE vs K is not symmetric:

- **AE**: seller BT-31/32/63; buyer **BT-48 or BT-47** (legal-entity fallback OK)
- **K**: seller BT-31 or BT-63 (**not** BT-32); buyer **BT-48 only**
- **O**: seller must have **none** of BT-31/BT-63; buyer **no** BT-48

`K` also requires actual delivery date (or invoicing period) and deliver-to
country code — and since `cac:Delivery` **precedes** `cac:TaxTotal`, you must
decide the tax category *before* serialising the delivery block.

### PEPPOL-only rules that are the real traps

| Rule | Requirement |
|---|---|
| `PEPPOL-EN16931-R003` | buyer reference **or** order reference MUST be present |
| `R010` / `R020` | buyer and seller `cbc:EndpointID` MUST be present |
| `BR-62` / `BR-63` | those endpoints **shall carry `@schemeID`** (EAS list) |
| **`R008`** | **document MUST NOT contain empty elements** — a serialiser that writes `<cbc:Note/>` for absent optionals fails *every* invoice |
| `R051` | all `@currencyID` = BT-5, except BT-111 |
| `R053`/`R054` | explains `TaxTotal` 1..2 — only one *with* subtotals |
| `F001` | dates MUST be `YYYY-MM-DD` |
| `BR-DEC-01…28` | **max 2 decimals on every monetary amount** — a float serialiser emitting `1250.0000000001` fails fatally. Use `Decimal` throughout. |

Arithmetic (`BR-CO-10` … `BR-CO-17`) is asserted, including
`BT-117 = BT-116 × (BT-119/100)` rounded to two decimals.

### cXML → UBL is lossy in both directions

- **cXML → UBL**: `category` is free text; UBL demands one of ten codes.
  `category="CA"` maps to nothing automatically.
- **UBL → cXML**: `AE`, `K`, `G`, `O` all collapse to `exemptDetail="exempt"`
  plus free text. `O` cannot be represented at all — "outside scope" and
  "exempt" are the same thing in cXML.
- **cXML puts tax at line *or* header (exclusive); UBL requires both** —
  per-line `ClassifiedTaxCategory` *and* a document `TaxTotal` breakdown,
  arithmetically reconciled. A cXML header-only invoice has no line
  categories to derive the UBL breakdown from; they must be synthesised.
- **UBL requires `TaxableAmount`; cXML does not.** Round-tripping means
  recomputing the base.
- **cXML linkage is explicit** (`TaxDetail@taxedElement`, an `IDREF`);
  **UBL matches by value** on (category, rate).
- **cXML credit memos are the same document with `purpose="creditMemo"`;
  UBL uses a structurally distinct `CreditNote` root and namespace.**
- **No envelope in UBL at all** — no `<Header>`, no `<Credential>`. Identity
  is `EndpointID/@schemeID` plus the AS4 transport layer. The
  self-contained-authenticated-document model does not transfer.

---

## 5. Tax rates — 2026

⚠️ **These are research output, not a maintained tax table.** Anything the
sandbox *asserts* about a rate should be traceable here, and the UI should say
the rates are illustrative. Markers: **V** = verified against a cited source,
**I** = inferred/recalled and unverified.

| Jurisdiction | Tax | Standard | Reduced / notable |
|---|---|---|---|
| UK | VAT | 20% (V) | 5%, 0%. **Domestic electricity zero-rated 1 Oct 2026 – 31 Mar 2027, GB only, NI excluded** (V) |
| Ireland | VAT | 23% (V) | 13.5%; **9% food/catering & hairdressing made permanent 1 Jul 2026** (V); 4.8% livestock |
| Germany | USt | 19% (V) | 7% — **all restaurant/catering food permanently from 1 Jan 2026**, beverages excluded (V) |
| France | TVA | 20% (V) | 10%, 5.5%, 2.1% (V) |
| Netherlands | BTW | 21% (V) | 9%. **Short-stay accommodation 9%→21% from 1 Jan 2026**; the culture/books/sport rise was rejected and did NOT happen (V) |
| Spain | IVA | 21% (V) | 10%, 4% |
| Italy | IVA | 22% (V) | 10%, 5%, 4% |
| Poland | VAT | 23% (V) | 8%, 5%, 0% |
| Sweden | Moms | 25% (V) | 12%, 6%. **Food 12%→6% 1 Apr 2026 – 31 Dec 2027**; takeaway 6%, dine-in 12% (V) |
| Switzerland | MWST | 8.1% (V) | 2.6%, 3.8%. A rise to 8.5% faces a Nov 2026 referendum, effective 2028 at earliest — **do not apply in 2026** (V) |
| Norway | MVA | 25% (V) | 15% food, 12% transport/hotels (V) |
| US — CA | Sales | 7.25% state, ~9.03% combined (V) | **No reverse charge** — use tax + exemption certificates |
| US — NY / TX | Sales | 4.00% / 6.25% state (V) | combined ~8.54% / ~8.20% |
| US — DE / OR | — | **0%** (V) | cleanest zero cases |
| Canada | GST | 5% federal (V) | ON 13% HST; **NS 14% — cut from 15% on 1 Apr 2025**, a very common stale figure (V); NB/NL/PEI 15%; BC +7% PST; QC +9.975% QST |
| Mexico | IVA | 16% (V) | 8% border zones; CFDI 4.0 XML stamped by a PAC is mandatory |
| Brazil | transitional | see below | |
| Australia | GST | 10% (V) | 0% fresh food, medical, exports |
| New Zealand | GST | 15% (V) | |
| Japan | Consumption | 10% (V) | 8% food/non-alcoholic, newspapers. **Unregistered-supplier input relief drops 80%→50% on 1 Oct 2026** (V) |
| India | GST | 18% (V) | **GST 2.0 from 22 Sep 2025: slabs are 0/5/18/40. The 12% and 28% slabs were ABOLISHED** (V) |
| UAE | VAT | 5% (V-ish) | |
| Singapore | GST | 9% (V) | |
| South Africa | VAT | **15%** (V) | The 2025 rises to 15.5%/16% were **withdrawn**; any source from Mar–Apr 2025 is wrong (V) |

**Brazil 2026 is a parallel-running test year** and makes an excellent stress
case for repeated `TaxDetail`: legacy ICMS + IPI + PIS + COFINS + ISS still
collected, plus **CBS 0.9% + IBS 0.1%** which must be *displayed but are not
collected*. Seven tax lines, two informational. All legacy rates are
placeholders (I) — ICMS is per-state/per-product.

### Known-stale risks
`CNPJ went alphanumeric on 1 Jul 2026` — a numeric-only regex is already
broken for new Brazilian registrations. Nova Scotia 14% and India's 0/5/18/40
slabs are the two figures most likely wrong in any inherited reference data.
Singapore's tax-ID format could not be resolved — sources actively conflict;
**do not ship a Singapore regex** without checking IRAS.

---

## 6. What this means for the sandbox

1. **Do not enum-check `category`/`purpose`.** Advisory at most.
2. **Use `Decimal` everywhere.** `BR-DEC-*` caps monetary values at two
   decimals and a float serialiser fails fatally.
3. **Never emit empty elements in UBL** (`R008`).
4. **Reverse charge in cXML is a convention we are inventing.** Label it in
   the UI as "no native cXML construct — this is the common pattern", and let
   the user switch it off. Presenting a convention as a standard is exactly
   the false authority `validation.py`'s docstring warns about.
5. The genuinely valuable edge cases to offer as one-click scenarios:
   credit memo (both conventions), reverse charge, Brazilian seven-line tax,
   Canadian GST+PST split, US zero-tax state, mixed-rate `S` grouping,
   and an `O`-category invoice (which is structurally a different document).
