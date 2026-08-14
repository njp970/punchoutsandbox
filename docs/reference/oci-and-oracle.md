# OCI and Oracle — field limits and failure modes

*Compiled 2026-08-14 from the SAP Open Catalog Interface specs (3.0, 4.0, 5.0),
the Oracle Procurement Supplier's Guide to Punchout and Transparent Punchout,
the Oracle iProcurement/Exchange Buyer's Guide, and the Oracle Fusion Self
Service Procurement attribute references (26a/26b).*

Companion to `platform-conformance.md`, which covers cXML. Same headline
applies: almost none of these limits come from a schema. They come from the
buyer's database columns.

---

## 1. The OCI field table

Naming is `NEW_ITEM-<FIELD>[n]`, **the type is always CHAR**, and **the index
starts at 1, not 0**.

| Field | Len | Notes |
|---|---|---|
| `DESCRIPTION[n]` | **40** | *not* unlimited — see §5 |
| `MATNR[n]` | **40** | the **buyer's** SRM product number, not the supplier's |
| `QUANTITY[n]` | 15 | 11 digits before the point, 3 after |
| `UNIT[n]` | **3** | ISO code, must be mapped in SRM |
| `PRICE[n]` | 15 | **per price unit** — see §2 |
| `CURRENCY[n]` | **5** | ISO code |
| `PRICEUNIT[n]` | **5** | whole numbers; **empty means 1** |
| `LEADTIME[n]` | 5 | days |
| `LONGTEXT_n:132[]` | ∞ | **special index syntax** — see §5 |
| `VENDOR[n]` | 10 | must already exist as a Business Partner |
| `VENDORMAT[n]` | 40 | the **supplier's** number |
| `MANUFACTCODE[n]` / `MANUFACTMAT[n]` | 10 / 40 | |
| `MATGROUP[n]` | **10** | must be a valid SRM material group |
| `SERVICE[n]` | 1 | |
| `CONTRACT[n]` / `CONTRACT_ITEM[n]` | 10 / 5 | |
| `EXT_QUOTE_ID[n]` / `EXT_QUOTE_ITEM[n]` | 35 / 10 | |
| `EXT_PRODUCT_ID[n]` | 40 | required for DETAIL / VALIDATE |
| `ATTACHMENT[n]` / `_TITLE` / `_PURPOSE` | 255 / 255 / 1 | |
| `EXT_SCHEMA_TYPE[n]` | 10 | |
| `EXT_CATEGORY_ID[n]` / `EXT_CATEGORY[n]` | 60 / 40 | |
| `CUST_FIELD1–3[n]` | **10** | |
| `CUST_FIELD4[n]` / `CUST_FIELD5[n]` | **20** / **50** | |
| `ITEM_TYPE[n]` / `PARENT_ID[n]` | 1 / 5 | OCI 5.0, SRM 7.0+ |

**Conditional requirements:** `DESCRIPTION` **or** `MATNR` (only one);
`QUANTITY` always; `UNIT` if `MATNR` absent; `CURRENCY` if `PRICE` present;
`EXT_SCHEMA_TYPE` if either `EXT_CATEGORY*`; `EXT_QUOTE_ID` if
`EXT_QUOTE_ITEM`; `CONTRACT` if `CONTRACT_ITEM`.

### Cross-version drift — a genuine supplier-breaker

| Field | OCI 3.0 | OCI 4.0 / 5.0 |
|---|---|---|
| `MATNR` | CHAR-18 | CHAR-40 |
| `PRICEUNIT` | **CHAR-9** | **CHAR-5** |

`PRICEUNIT` **shrank**. A supplier emitting a 6–9 digit price unit — legal
under 3.0 — overflows a 4.0 buyer.

---

## 2. `PRICEUNIT` — the silent multiplication error

The highest-value case in the whole OCI surface, because it has **no cXML
equivalent** and fails silently in both directions.

`PRICE` is the price *per price unit*; `PRICEUNIT` defaults to 1 when empty.
SAP's own OCI 3.0 example ships the trap:

```html
<input type="hidden" name="NEW_ITEM-PRICE[1]"     value="50.00">
<input type="hidden" name="NEW_ITEM-PRICEUNIT[1]" value="5">
```

True unit price is 10.00. A consumer that ignores `PRICEUNIT` books 50.00 per
unit — **a 5× overcharge with no error and no warning**. OCI 5.0's JSON
equivalent (`PRICE_QUANTITY`) documents the motivating case: *"1 screw for
0.005 cents… '5,- Euro per 1000 PCE' with 1000 being the PRICE_QUANTITY"*.

SAP KBA 3382679 documents a worse variant: `PRICEUNIT` survives the initial
transfer but **is reset when SRM runs the OCI VALIDATE function**, so the
price changes between add-to-cart and requisition creation.

Because a supplier building one cart model for both protocols has nowhere to
put the divisor in cXML, they will either lose it or apply it twice. Oracle's
own cXML→XML conversion table maps `Money → <unitPrice>` with no price-unit
term anywhere, confirming the information is simply dropped in translation.

**Numeric format:** *"Do not use commas for thousands"*, and OCI 3.0 adds that
numeric fields "may not include commas or any other non-numeric characters"
and must not have leading spaces. So `1,234.56`, `1.234,56` (German locale),
`1 234.56` and 4-decimal prices are all break cases.

---

## 3. `UNIT` must be an ISO code, and usually isn't

The field is CHAR-3 and *"must be maintained as ISO code in the SRM Server"*.
SAP stores the internal unit in **T006** and the ISO code in
**T006-ISOCODE / T006I**, maintained through transaction **CUNI**. The OCI
field must carry the **ISO** code (`PCE`, `KGM`, `MTR`, `DZN`) — but SAP's
internal codes are German-derived and different (`ST`, `KG`, `M`).

Production error string, verbatim:

> `Unit of measure D97 is not an ISO code. Item will not be transferred`

wrapped for the user as *"Incomplete items in catalog, only complete items
were transferred"* — i.e. **the cart silently loses lines**. OCI 5.0 states
the rule generally: if a required field is missing or fails validation,
*"the item is ignored and not indexed"*.

Break cases: `EA` (not ISO), `PCE` (ISO but frequently unmapped), `ST`
(internal, not ISO), `D97` (ISO pallet, commonly unmapped), `EACH`
(4 characters — overflows CHAR-3), empty unit with empty `MATNR`.

Diagnostics worth telling users about: SRM error log via transaction **SLG1**,
object **`BBP_OCI`**; the mapping itself runs in function module
`BBP_WS_MAP_OCI_TO_SC`, and BAdI `BBP_CATALOG_TRANSFER` can dump every
transferred field.

---

## 4. Transport mechanics

Call-up carries `HOOK_URL`, `OCI_VERSION`, `http_content_charset`,
`returntarget`. **POST is the standard**, and the spec warns GET "can lead to
browser-dependent length restrictions".

**The most-missed requirement — `HOOK_URL` must be SPLIT:**

> "It usually contains other parameters that must first be **extracted and
> placed in separate input fields (of type hidden)** of the form… The URL
> **without these parameters** must be placed into the action attribute."

A supplier who reuses cXML return code for OCI will POST to the whole
`HOOK_URL` including its query string, and SRM loses or duplicates
parameters. Oracle documents the mirror-image bug on its own side (§6).

**Real POST-size ceiling:** with stock PHP limits (`post_max_size 8M`,
`max_input_vars 1000`) only about **20 articles** fit, at roughly 50 input
vars each — and `max_input_vars` truncation is **silent**, dropping the tail
of the cart.

**The `~` control fields** (`~OkCode=ADDI`, `~target`, `~CALLER`) and the
XML-variant type field are a case-sensitivity minefield: SAP's own documents
spell the latter `xmltype`, `~xmlType` **and** `~xml_type` in three different
places. Emit the wrong spelling and the cart parses as HTML-variant with zero
items.

---

## 5. `LONGTEXT` and `DESCRIPTION`

`DESCRIPTION` is **CHAR-40**. The widely-repeated claim that it is unlimited
is false — that footnote applies **only** to `LONGTEXT_n:132[]`, whose index
goes *before* the colon with empty brackets.

Writing it as `NEW_ITEM-LONGTEXT[1]` instead of `NEW_ITEM-LONGTEXT_1:132[]`
is a documented, frequently-hit bug: **all long texts from all items get
appended to the first item.**

---

## 6. Oracle — the same payload truncates differently by route

Oracle silently truncates, with its own worked example: a 32-character
manufacturer item number *"becomes `ABmanufacturer30plusCDEitem123`"*.

The killer is that limits differ by path — Exchange versus direct to
iProcurement:

| Field | via Exchange | direct to iProcurement |
|---|---|---|
| `supplierItemNumber/itemID` | **740** | **25** |
| `supplierUOMType` | **3** | **80** |
| `manufacturerName` | **255** | **30** |
| `currency` | **4** | **15** |
| `categoryCode` | 250 | 80 |
| `itemDescription` | 240 | 240 |

So `EACH` becomes `EAC` on the Exchange route only, and a 30-character SKU
survives one hop and is mangled on the other. Multibyte languages allow
*fewer* characters still.

Silent-default behaviours: `<catalogType>` set to anything other than
`CONTRACTED` (or omitted) means **the contract number is ignored**; an invalid
`lineType`/`itemClassification` **defaults to Goods**; an unmapped
`hazardClass` is **left blank**; an unspecified category identifier
**assumes SPSC**.

### EBS hard-fails where Fusion passes through

The sharpest behavioural divergence, worth two separate conformance profiles:

> **EBS:** "all categories and units of measure sent by the supplier… **must
> be mapped**… Otherwise, **creation of the requisition will fail**. You
> **cannot create a 'catchall' category**… even if they use the same name."

> **Fusion:** resolution order is map set → BU default map set → **external
> value used as-is** → default category name.

**The same unmapped UOM that hard-fails requisition creation in EBS passes
through unmapped into a Fusion requisition line.**

### Oracle error codes worth emitting

`204` server-side (HTTP response was not 200) · `400`/`401` invalid XML in
login response / shopping cart, **including missing mandatory values** ·
`501` invalid supplier currency · `700` authentication failure (cXML status
401) · `701` misc login failure (**cXML status anything other than 200 or
401**) · `900` punchout not published.

Fusion wraps these as `POR-2010059`. An **empty-cart return is a proven crash
case** — Oracle logged a null pointer exception when a user returns with a
blank cart, backported as a critical bug.

Two more hard rules: Oracle **does not support client-side certificate
authentication** — a supplier server that requests it fails the connection by
design. And **TBD-priced items are rejected outright**.

Encoding: Oracle Exchange supports **UTF-8 only**, the cart must be
URL-encoded, and `java.net`'s `URLEncoder` handles ISO-8859-1 only — so
multibyte carts need a different encoder. Note Oracle's own documentation
writes the prolog as `encoding="UTF8"` (no hyphen), which is itself a
nonconformant token worth testing.

---

## 7. OCI versus cXML — where suppliers get caught

| | OCI | cXML |
|---|---|---|
| Setup handshake | **none** — browser redirect with `HOOK_URL` | server-to-server request/response |
| Return address | `HOOK_URL`, **query params must be split out** | `BrowserFormPost/URL`, used whole |
| Default charset | **ISO-8859-1** | **UTF-8** |
| Index base | **1** | n/a |
| Price semantics | `PRICE` **per `PRICEUNIT`** | `Money` is per one `UnitOfMeasure` |
| Failure signal | **none — bad items silently dropped** | `<Status code=…>` |

The last row is the reason this sandbox matters more for OCI than for cXML:
OCI has no status mechanism at all, so a buyer integration can be losing
lines on every single cart and never see an error.

---

## 8. A caution about secondary sources

The widely-cited `gatebold.com/en/tools/oci-fields` reference contradicts the
SAP specs on several normative points — it calls `MATNR` the supplier item
number (it is the **buyer's**), gives `MATGROUP` as 9 (it is 10) and
`CURRENCY` as 3 (it is 5), marks conditionally-required fields as required,
and lists a `NEW_ITEM-DELIVERYDATE` field that **appears in no OCI spec**.

Do not seed the harness from secondary sources. Every limit in this document
came from a vendor primary PDF.
