# Vendored cXML DTDs — provenance and licence status

**Version 1.2.071. Retrieved 2026-08-14** from
`https://xml.cxml.org/current/cXML_DTDs.zip` (the "Latest Downloads" link on
cxml.org). The archive's own files are dated 2026-08-12.

Refresh with `scripts/fetch_dtds.sh`, which re-downloads, re-checksums, and
diffs against what is committed here.

## Why these are committed rather than fetched

Two reasons, and the second is the important one:

1. A Docker build that reaches out to someone else's webserver is a build that
   fails when that webserver is down.
2. **A conformance verdict has to be reproducible.** If the validator fetched
   its rules at runtime, the same document could pass on Tuesday and fail on
   Wednesday because a third party published a revision, and we would have no
   record of which rules produced which verdict. The DTD version is part of
   the answer, so it is part of the repository. Note that 1.2.071 landed on
   2026-08-12 — two days before we vendored it — which is exactly the kind of
   silent movement that argument is about.

## Checksums as retrieved

```
113a82e3f7e86c9503a1d1a228735b7e7f453749e7490d71380b758c5ac30ba2  Catalog.dtd
1a9cea79eff811e909f0c2d696505d5373d7795d5bae78ff22b1a5fb240ee8fb  Contract.dtd
1bdb5394eac2ea243dcb124e7cb149603a53848a75d89018ee06bdc6e699d21c  Fulfill.dtd
4351e2b54d919e8b89c5e1dd6791347649334acec4ec3e0b31180b17163336c1  InvoiceDetail.dtd
e36913216f99e88ca731f7e65b613dde49830a10961ae5b5493cc3ecbafceafa  Logistics.dtd
4068ed75161e6bf3625062838c9aa96d7ecd93cbe68d2de36e7dd6dbbcf58050  PaymentRemittance.dtd
6dac214b0bfc45e88e4574897ec811fb103cf7ffdca3322073c84c107e41338a  Private.dtd
01b084f04a9f6a0ff047c6feb111a08f768abb702c8648d3e40676783e951414  Quote.dtd
d267ad7b19cbd6608b972821daacab0f4d94a8ec78610b7127dba23222198a64  cXML.dtd
```

## Which DTD validates which document

The modules are self-contained, not layered — `Fulfill.dtd` and
`InvoiceDetail.dtd` each embed the whole common cXML definition and then add
their own document types. So each redefines `PunchOutOrderMessage`, and you
pick ONE DTD per document rather than composing several.

| Document | DTD |
|---|---|
| `PunchOutSetupRequest` / `PunchOutSetupResponse` / `PunchOutOrderMessage` / `OrderRequest` | `cXML.dtd` |
| `ConfirmationRequest` / `ShipNoticeRequest` | `Fulfill.dtd` |
| `InvoiceDetailRequest` | `InvoiceDetail.dtd` |
| `CatalogUploadRequest` | `Catalog.dtd` |
| `QuoteMessage` | `Quote.dtd` |

## ⚠️ Licence status — UNRESOLVED, resolve before making this repo public

The DTD headers say:

> For cXML license agreement information, please see
> http://www.cxml.org/home/license.asp

**That URL returns 404.** The current site's own "License Agreement" nav link
(`https://cxml.org/license.html`) **301-redirects to the homepage**, and the
downloadable archive contains no licence file. As of 2026-08-14 the cXML
licence terms are not published at any URL we could find.

So: the DTDs are distributed publicly and free of charge with no registration
(BRIEF.md §2 verified this), and vendoring them is what every cXML
implementation does. But "we could not find terms forbidding it" is not the
same as "the terms permit it", and this repository is intended to be public.

Before publishing, either:
- get the licence text from Ariba/SAP (cXML is Ariba-originated; the `$Id:`
  strings in the DTDs still say `//ariba/cxml/modules/...`) and record it
  here; **or**
- do not commit the DTD bodies — ship `scripts/fetch_dtds.sh` and have the
  Docker build fetch them, accepting the reproducibility cost above and
  pinning by checksum so a silent revision still fails loudly.
