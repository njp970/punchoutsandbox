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

## Licence — RESOLVED, and what keeps it resolved

**Settled 2026-08-14. Vendoring these files is permitted. The full licence is
attached at `LICENSE-cXML.txt`, as the licence itself requires.**

### Why it looked unresolved

Every DTD in this directory carries the header:

> For cXML license agreement information, please see
> http://www.cxml.org/home/license.asp

**That URL is dead and returns 404**, and it has been for long enough that the
current site does not redirect it. The downloadable archive contains no licence
file either. So the only pointer shipped *with* the specification leads
nowhere, which is why the first pass here recorded the position as "we could
not find terms" — an honest but incomplete answer.

The agreement is published, at a different address entirely:

    https://www.cxml.org/license.html

reachable from the site's own "License Agreement" nav link. A past revision
(2002-03-07) is linked from that page. Licensor is **Ariba, Inc.**

### What it says

Clause 2 grants a *"perpetual, nonexclusive, royalty-free, worldwide right and
license"* to *"use, copy, publish, and distribute (including but not limited to
distribution as part of a separate computer program) the unmodified
Specification"*, and to implement it in software. That is precisely what this
repository does.

Two conditions come with it, and both are enforced rather than merely noted:

| Condition | Where it comes from | How it is held |
|---|---|---|
| The Specification must be **unmodified** | clause 2 grants rights over the "unmodified Specification" | `tests/test_dtd_licence.py` checksums every DTD and fails on any change |
| The licence must be **attached** when distributed | clause 3: *"If you publish, copy or distribute the Specification, then this License must be attached."* | `LICENSE-cXML.txt`, verified present and intact by the same suite |

Clause 1 adds a detail worth understanding rather than skimming: *"The rights
granted under this license ... are subject to the version of the Agreement in
effect at the time it was downloaded or accessed by you."* Ariba may publish
new terms at any time, and those do not reach backwards — but only if you can
show which version you accepted. `LICENSE-cXML.txt` therefore records the
retrieval date and the SHA-256 of the page as fetched, so that "the version in
effect when we accessed it" is a fact we hold rather than a claim we make.

### Consequences for this repository

- The DTDs are **not** under the repository's MIT licence and cannot be
  relicensed by us. `/LICENSE` says so explicitly, because silence would read
  as a claim that MIT covers them.
- MIT's permission to *modify* does not extend to these files. Fixing a typo in
  a DTD to make a test pass would forfeit the grant. If one genuinely needs
  changing, the change belongs upstream — `comments@cxml.org` per clause 10.
- Refreshing to a newer cXML version is fine, and `scripts/fetch_dtds.sh`
  exists for it. Update the checksum block above **and** the table in
  `tests/test_dtd_licence.py`, then re-run every conformance suite: a DTD
  revision changes which documents we call valid, and that is the product.

### The option that was rejected

The alternative was to not commit the DTD bodies at all and have the build
fetch them. With the licence resolved that trade is clearly the wrong one: it
would cost build reproducibility and make a conformance verdict depend on a
third party's webserver being up, to avoid a restriction that turns out not to
exist.
