"""The published reference pages.

=============================================================================
WHY THESE ARE PUBLISHED AT ALL
=============================================================================
`docs/reference/` holds the material this whole product is built on: the
platform-imposed field limits, the tax rules, the OCI field lengths, the
fulfilment document ordering traps. BRIEF.md's central claim is that **none of
it is written down anywhere** — every real limit is imposed by a buyer platform
rather than by the schema, and no vendor publishes theirs.

Leaving it in a repository serves the person already reading the repository.
Publishing it serves the person who has not found this site yet, which is
almost everybody: nobody searches for "punchout sandbox", they search for the
error in front of them at eleven at night. These pages are what such a search
can land on, and the validator is one click away from each of them.

=============================================================================
CURATED TITLES, VERBATIM BODIES
=============================================================================
The bodies are rendered exactly as written, module references and all — they
are engineering notes and read like it, and dressing them up would cost the
precision that makes them worth reading.

The TITLES and DESCRIPTIONS are curated here rather than derived from the
files, because a file's own first line is aimed at whoever is about to edit
`app/cxml/invoice.py`, and a search result is read by someone who has never
heard of this site. Those are different audiences and deserve different
sentences. This is also the only place the ordering lives.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from . import markdown


@dataclass(frozen=True)
class Page:
    slug: str
    filename: str
    #: What a search result says. Written for someone who has never heard of
    #: this site and is looking at an error message.
    title: str
    description: str
    #: One line on the index. Concrete beats comprehensive.
    blurb: str


PAGES: tuple[Page, ...] = (
    Page(
        slug="platform-conformance",
        filename="platform-conformance.md",
        title="cXML field limits by platform — what actually breaks a punchout",
        description=(
            "cXML itself imposes almost no length limits. Every limit that "
            "truncates your data is imposed by the buyer platform, and none of "
            "them publish it. What Ariba, Coupa, Jaggaer and SAP actually do."),
        blurb=("Why your Description arrives truncated at 254 characters, why "
               "leading zeros vanish from part numbers, and why the schema "
               "said it was fine."),
    ),
    Page(
        slug="invoice-and-tax",
        filename="invoice-and-tax.md",
        title="cXML InvoiceDetailRequest — the rules that fail validation",
        description=(
            "The mandatory-but-empty indicator elements, why Tax/Description "
            "is required, the element order everyone gets backwards, and how "
            "tax is actually calculated across jurisdictions."),
        blurb=("The four things everyone gets wrong in an invoice, and "
               "multi-country VAT, GST and sales tax with the reasoning shown."),
    ),
    Page(
        slug="fulfilment-documents",
        filename="fulfilment-documents.md",
        title="ConfirmationRequest and ShipNoticeRequest — cXML fulfilment",
        description=(
            "The confirmation and ship notice documents, the type values that "
            "are valid against the DTD and still rejected by the buyer, and "
            "the UnitOfMeasure that has to appear twice."),
        blurb=("Order confirmation and dispatch, including the rules the DTD "
               "cannot express and the buyer enforces anyway."),
    ),
    Page(
        slug="oci-and-oracle",
        filename="oci-and-oracle.md",
        title="SAP OCI field limits — NEW_ITEM lengths and HOOK_URL failures",
        description=(
            "OCI's real field limits (DESCRIPTION is 40 characters), the "
            "LONGTEXT workaround, what each FUNCTION value does, and why your "
            "characters turn into question marks."),
        blurb=("SAP SRM's Open Catalog Interface: field lengths, the FUNCTION "
               "values, and the charset trap."),
    ),
)

BY_SLUG = {page.slug: page for page in PAGES}

#: Populated at build time by `scripts/build_asset.sh`; falls back to the
#: repository copy when running from a checkout, so `python -m app.handler`
#: works with no build step.
_BUNDLED = os.path.join(os.path.dirname(__file__), "reference_docs")
_CHECKOUT = os.path.join(os.path.dirname(__file__), "..", "docs", "reference")

_cache: dict[str, str] = {}


def _source(page: Page) -> str:
    for directory in (_BUNDLED, _CHECKOUT):
        path = os.path.join(directory, page.filename)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read()
    return ""


def body(slug: str) -> Optional[str]:
    """Rendered HTML for a page, or None if the slug is unknown.

    Cached on the module: these files never change within the life of a
    container, and re-rendering 20KB of Markdown on every request would be
    paying for nothing."""
    page = BY_SLUG.get(slug)
    if page is None:
        return None
    if slug not in _cache:
        source = _source(page)
        if not source:
            return None
        # The file's own `# Title` is dropped: the template renders the
        # curated title as the page's h1, and two competing titles is worse
        # than either.
        lines = source.split("\n")
        for index, line in enumerate(lines):
            if line.strip().startswith("# "):
                lines = lines[index + 1:]
                break
        _cache[slug] = markdown.render("\n".join(lines))
    return _cache[slug]
