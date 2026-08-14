# Order confirmation, ship notice and status update — implementation reference

*Compiled 2026-08-14 from `cXML.dtd` and `Fulfill.dtd` (1.2.060/1.2.071) read
locally, plus the cXML Reference Guide v1.2.070 (646pp) extracted to text.
Everything below is quoted or directly derived from those sources.*

Spec for `app/cxml/fulfil.py`. Read with `platform-conformance.md`, which
covers what buyer platforms do on top of these rules.

---

## 0. Corrections to assumptions

Four things that are commonly got wrong, including in our own earlier notes:

1. **`PunchOutOrderMessage.dtd` does not exist.** The modules are `cXML.dtd`,
   `Fulfill.dtd`, `InvoiceDetail.dtd`, `Quote.dtd` (plus Catalog, Contract,
   Logistics, PaymentRemittance, Private). `PunchOutOrderMessage` lives in
   `cXML.dtd`; all three fulfilment documents live in `Fulfill.dtd`.
2. **`ConfirmationHeader@type` is `replace`, not `replaced`** — and the full
   set includes `requestToPay`. `ConfirmationStatus@type` also carries
   `backordered` and `requestToPay`.
3. **There is no `OrderResponse` element.** A PO is answered with a bare
   `<Response><Status/></Response>`. Commercial acceptance is a *later*
   `ConfirmationRequest`.
4. **cXML status codes 202, 404, 405, 407, 408 and 410 do not exist.** The
   real set fills those gaps with 211, 280, 281, 406, 409, 476, 477 and the
   46x/499/56x catalog-upload range. `404` appears only as an example of an
   HTTP *transport* error.

---

## 1. The empty-cart rule, stated precisely

`platform-conformance.md` §1.1 flags that an `edit` returning an empty cart
deletes the buyer's requisition lines. That is right but incomplete, and the
missing half matters because **two wire forms mean opposite things**:

| Operation | Empty item list | `<Status code="204"/>` |
|---|---|---|
| `create` | add nothing — user cancelled | add nothing |
| `edit` | **DELETE the existing lines** | **change nothing** — user cancelled without editing |
| `inspect` | item list must be ignored entirely | ignored |

Spec, verbatim on 204: *"indicates the end of a session without change to the
shopping cart… This code would be handled identically to the other 'empty'
cases **unless the operation was `edit`. In that case, the user canceled the
session without making any change and no change should be made to the
requisition.**"*

So a supplier whose "user cancelled" path emits an empty cart rather than a
204 **silently wipes the buyer's lines**. Both forms must be separate
scenarios in the sandbox, and the difference is worth calling out in the UI —
it is the single most destructive correctness bug the protocol permits.

Note also the DTD comment: when `Status` is absent from a `Message`,
`<Status code="200" text="OK"/>` is implied.

---

## 2. ConfirmationRequest

```dtd
<!ELEMENT ConfirmationRequest (ConfirmationHeader, OrderReference,
        (OrderStatusRequestReference | OrderStatusRequestIDInfo)*, ConfirmationItem*)>
<!ATTLIST ConfirmationHeader
    confirmID  %string;       #IMPLIED
    operation  (new | update) "new"
    type (accept | allDetail | detail | backordered | except | reject |
          requestToPay | replace)  #REQUIRED
    noticeDate %datetime.tz;  #REQUIRED
    invoiceID  %string;       #IMPLIED
    incoTerms  (cfr|cif|cip|cpt|daf|ddp|ddu|deq|des|exw|fas|fca|fob) #IMPLIED
    version    %uint;         #IMPLIED>
<!ATTLIST ConfirmationStatus
    quantity %r8; #REQUIRED
    type (accept | allDetail | detail | backordered | reject | unknown | requestToPay) #REQUIRED
    shipmentDate %datetime.tz; #IMPLIED
    deliveryDate %datetime.tz; #IMPLIED>
```

### Header types

- **`accept`** — whole order. Items, if present, may carry only `accept`
  statuses.
- **`allDetail`** — updates specific lines, including *all* information the
  supplier holds whether or not it differs from the PO. Statuses must be
  `allDetail`, `reject` or `unknown` — **never** `accept` or `detail`. The
  spec itself calls this a short-term "bridge" strategy because of the
  reconciliation problems it causes.
- **`detail`** — updates specific lines, carrying **only what differs**.
  Statuses may be anything **except** `allDetail`.
- **`except`** — accepts the whole order with exceptions; unmentioned lines
  stand as ordered.
- **`backordered`**, **`reject`** — whole order.
- **`requestToPay`** — invokes a payment service; result returns as a
  `StatusUpdateRequest` carrying `PaymentStatus`.

### The multiplicity rules a harness should test

> Each confirmation must mention a line item only once. **A line item must not
> be mentioned in more than one confirmation request.** … **Only one
> confirmation per order is allowed for `accept`, `except`, or `reject`. When
> a confirmation with one of these types arrives, the receiving system must
> discard all previous confirmations for the purchase order.**

And: *"Quantities at this level must sum to the quantity in the containing
`ConfirmationItem`."*

### How each business event is expressed

| Event | Encoding |
|---|---|
| Price change | `type="detail"`; `ConfirmationStatus type="detail"` containing a `UnitPrice` |
| Date change | `ConfirmationStatus` with `deliveryDate`/`shipmentDate`. Omit if unchanged from the PO's `requestedDeliveryDate` |
| Partial accept | split one `ConfirmationItem` into several `ConfirmationStatus` elements by quantity |
| Backorder | `ConfirmationStatus type="backordered"` + `shipmentDate` |
| Substitution | `ConfirmationStatus type="detail"` containing an `ItemIn`. Quantities must match unless the UOM changed — and *"you should then wait for a corresponding change order from the buyer before shipping"* |
| Status unknown | `type="unknown"` — a placeholder, also the way to reset a line accepted or rejected in error |

**Counter-intuitive rule worth a scenario:** in a `detail` update, *absence is
meaningful*. Re-stating the PO's original tax means "the PO was right"; the
supplier must **not** repeat variations sent in an earlier confirmation.

---

## 3. ShipNoticeRequest

```dtd
<!ELEMENT ShipNoticeRequest (ShipNoticeHeader, ShipControl*, ShipNoticePortion*)>
<!ATTLIST ShipNoticeHeader
    shipmentID      %string;      #REQUIRED
    operation       (new | update | delete) "new"
    noticeDate      %datetime.tz; #REQUIRED
    shipmentDate    %datetime.tz; #IMPLIED
    deliveryDate    %datetime.tz; #IMPLIED
    shipmentType    (actual | planned)   #IMPLIED
    fulfillmentType (partial | complete) #IMPLIED
    reason          (return)      #IMPLIED>
```

### Multi-leg ordering rules — precise and frequently broken

> `ShipControl` elements **must appear in the order the shipment will travel.
> The first such element must not have an explicit starting date** … **All
> later `ShipControl` elements must have increasing starting dates.**

Further: `Contact role="shipFrom"` **must** appear in every `ShipControl`
after the first and **must not** appear in the first (it would duplicate the
header). `role="shipTo"` must not be used in `ShipControl` at all.

### Partial shipments

- `fulfillmentType="partial"` when not everything ships; `shipmentType`
  `actual` vs `planned` decides whether the date is real or estimated.
- **An order must not appear more than once** in one `ShipNoticeRequest`, and
  each PO line must be mentioned in **at most one** `ShipNoticeItem`.
- `shipNoticeLineNumber` is how one PO line splits across several packages
  within a single ASN.
- **The trap:** *"If a `ShipNoticePortion` element contains no
  `ShipNoticeItem` elements, the entire referenced order is included in the
  shipment."* A supplier sending an empty portion meaning "nothing yet" has
  just declared the whole order shipped.

### Carrier identification

Recognised `CarrierIdentifier@domain` values: `companyName`, `SCAC`, `IATA`,
`AAR`, `UIC`, `EAN`, `DUNS`. A domain must not repeat within one
`ShipControl`, and all identifiers must denote the same company.
`ShipmentIdentifier@domain` is typically `trackingNumber` or `billOfLading`;
`trackingNumberDate` is required once a carrier is named.

**Scoping rule:** *"`ShipNoticeRequest` documents do not provide updates to tax
and shipping amounts. This information should be transmitted with
`ConfirmationRequest` documents."*

`operation="update"` must be **complete** — *"all data from the original
should be discarded by the recipient"*. A partial update leaves phantom lines
in any buyer that merges rather than replaces.

---

## 4. StatusUpdateRequest and the PO reply state machine

A PO POST is answered with a bare `Response`. **200 means "received and
accepted for processing", not "order accepted commercially."** A supplier
intending to reject still returns 200 and rejects later via a
`ConfirmationRequest`.

The state machine, verbatim:

> Suppliers and hubs utilizing the StatusUpdate transaction **must return code
> 201/Accepted when an `OrderRequest` is queued for later processing.** After
> it sends 200/OK … the server should send no further StatusUpdate
> transactions for that order.

So: **201 → later `StatusUpdateRequest` 200 (terminal)**, or **200 immediately
(terminal, nothing owed)**. A supplier that emits 201 and never follows up is
a valid negative test — the buyer is left waiting forever.

---

## 5. Status codes

Ranges: **2xx** success. **4xx** permanent, do not retry. **5xx** transient —
*"recommended number of retries is 10, with a frequency of one hour… At a
minimum a six hour retry window."*

| Code | Meaning |
|---|---|
| 200 / 201 | executed / accepted for forwarding, expect later `StatusUpdateRequest` |
| 204 | no content — in a `PunchOutOrderMessage`, session ended without change |
| 211 | buyer broadcast to suppliers |
| 280 / 281 | forwarded by a hub / forwarded over unreliable transport (e.g. email) |
| 400 | unacceptable **although it parsed correctly** |
| 401 | credentials in `Sender` not recognised |
| 402 / 403 | payment required / insufficient privileges |
| 406 | unacceptable, **likely a parse failure** — the spec's preferred code for DTD validation errors |
| 409 | server state prevented the update |
| 412 | precondition failed — e.g. an `edit` with no matching session, or the client ignored `operationAllowed` |
| 417 | expectation failed — implied resource condition unmet |
| 450 | not implemented — e.g. the requested operation is unsupported |
| 475 / 476 / 477 | signature required / verification failed / unacceptable |
| 500 / 550 / 551 / 560 | server error / cannot reach upstream / cannot forward / temporary |

Catalog upload adds 461–470, 499 (document size), 561–563.

Unknown-code rule: *"Older clients should treat all new 2xx codes as 200, 4xx
as 400, and 5xx as 500."* So be tolerant on input.

**The single most important operational rule**, verbatim:

> All transport errors should be treated as transient and the client should
> retry, as if a cXML 500 range status code had been received. **All HTTP
> replies that don't include valid cXML content, including HTTP 404 and HTTP
> 500 status codes, are considered transport errors.**

Therefore: **return HTTP 200 with a cXML `Status` body for every
business-level error.** Returning HTTP 401 with an HTML page turns a permanent
authentication failure into an hours-long retry loop. That asymmetry is the
best single conformance test in the whole protocol — offer the same logical
error four ways (HTTP 200 + cXML 401; HTTP 401 + cXML 401; HTTP 500 + HTML;
HTTP 200 + malformed XML) and a conforming buyer stops on the first and
retries the third and fourth.

---

## 6. Round-trip fidelity — the rule to enforce

> The `ItemIn` and `ItemOut` structures match one-to-one, except for the
> `Distribution` and `Comments` elements and `requisitionID` and
> `requestedDeliveryDate` attributes … **`ItemDetail` data (with the possible
> exception of `Extrinsic` elements) contained within `ItemIn` elements must
> not be removed when converting from `ItemIn` to `ItemOut`.**

This is the cleanest statement anywhere of what a buyer must preserve between
the returned cart and the PO, and it is directly checkable: capture the cart,
capture the PO, diff the `ItemDetail` subtrees. That comparison is a strong
candidate for the sandbox's headline check, because it needs both halves of
the round trip and therefore cannot be done by any tool that only sees one.

## 7. Types that are looser than they look

`%r8;` and `%uint;` are both `CDATA` — **`quantity` is a free decimal string,
not an integer.** The spec: *"the protocol allows fractional quantities.
Should never be negative."* `%isoCurrencyCode;` is `NMTOKEN` with no
enumeration, so `gbp` and `UK£` both pass the DTD.

`ItemDetail` requires `Description+` **and** `Classification+` — one or more
of each, so multi-language descriptions and multi-scheme classification are
both normal. `Description@xml:lang` is `#REQUIRED`; `Status@xml:lang` and
`Comments@xml:lang` are not.

`ShortName` is *"30-character recommended, 50-character maximum"*, and
*"clients must continue to truncate the `Description` text if no `ShortName`
is provided"* — which is where mid-multibyte-character truncation bugs come
from.
