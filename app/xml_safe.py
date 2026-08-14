"""PunchOut Sandbox — THE hardened XML front door. Nothing in `app/` may call
`ElementTree.fromstring`, `lxml.etree.fromstring`, or `lxml.etree.parse`
directly. One `parse()`, small enough to read in full while asking "is this
safe".

*Deliberately close to Xenia's `services/procurement/xml_safe.py`, because the
threat model is identical and two divergent hardened parsers is one hardened
parser and one weaker one. Read that file's docstring for the three attacks
(XXE, billion laughs, depth/size exhaustion) and why the DOCTYPE is permitted
while entities are not — all of it applies here verbatim and is not repeated.*

=============================================================================
WHAT IS DIFFERENT HERE, AND WHY IT MATTERS
=============================================================================
Xenia parses cXML with `defusedxml` alone. This service ALSO has `lxml`,
because DTD validation is the product (see `validation.py`). That introduces a
second XML engine, and therefore a second attack surface that Xenia does not
have. The rule that keeps it safe:

    defusedxml parses FIRST and decides whether the document is hostile.
    lxml only ever sees bytes defusedxml has already accepted.

`parse()` below enforces that ordering by doing both, in that order, and
returning both trees. There is no code path that reaches lxml without having
gone through defusedxml, and there must never be one — a caller that wants
only the lxml tree still pays for the defusedxml pass, which is the point.

Why not just harden lxml and drop defusedxml? Because lxml's hardening is
configuration (`resolve_entities=False`, `no_network=True`, ...) and
defusedxml's is refusal. Configuration can be got wrong in a future edit by
someone who does not know why the flags are there; a library whose entire
purpose is to say no is harder to accidentally disarm. Belt and braces, where
the belt costs about a millisecond.

=============================================================================
THE DOCTYPE IS PERMITTED. THE NETWORK IS NOT.
=============================================================================
Every conforming cXML document opens with

    <!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">

and forbidding DTDs outright would reject every legitimate document a buyer
ever sends us. So the DOCTYPE is allowed through — and the `SYSTEM` URL in it
is NEVER fetched. Not once, not cached, not "only in dev".

This matters more here than it does in Xenia, and it is worth being explicit
about why, because the temptation is real and specific: this service is a
DTD VALIDATOR. The obvious implementation of "validate against the DTD the
document declares" is to fetch the URL the document declares. That would mean
(a) an attacker chooses which URL our Lambda makes an outbound request to,
which is a server-side request forgery with extra steps, and (b) a
conformance verdict that depends on someone else's uptime.

`validation.py` therefore validates against the LOCAL vendored DTD chosen by
document type (`app/cxml/dtd/`, see its README), and ignores the declared
SYSTEM identifier entirely except to report it back to the user as an
observation. `no_network=True` on the lxml parser is the belt for that
particular braces.
"""
from __future__ import annotations

import hmac
from typing import Optional
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring as _defused_fromstring
from lxml import etree

# A cXML PunchOutOrderMessage with a few hundred lines is tens of KB; an
# InvoiceDetailRequest likewise. 4MB matches Xenia's cap — far above any
# legitimate document, far below the 6MB Lambda response ceiling.
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

# cXML's deepest legitimate nesting is around a dozen levels. 100 is generous;
# 10,000 is an attack.
MAX_DEPTH = 100


class XmlRejected(Exception):
    """The document was refused before it was trusted. Carries a short reason
    safe to log and safe to show a user — never the document itself, which may
    be hostile and is certainly untrusted.

    Note the difference from a VALIDATION failure: this is "we would not
    process this at all", whereas `validation.ConformanceReport` describes a
    document we happily parsed and then judged against the DTD. Users of this
    sandbox will see far more of the latter, and confusing the two would make
    a hostile document look like a merely non-conformant one."""


class SharedSecretMismatch(Exception):
    """Authentication failed. A cXML shared secret is transmitted IN the
    document, so it can only be compared, never recomputed: it proves the
    sender knows the secret, NOT that the body is unmodified. TLS protects the
    body. Do not let a caller read this as an integrity guarantee."""


def _depth(element: Element, *, _current: int = 1) -> int:
    """Iterative, not recursive: a deeply nested document is precisely the
    input being defended against, and a recursive walk would raise
    `RecursionError` — an error that reads like a bug in us rather than a
    refusal of them."""
    deepest = _current
    stack = [(element, _current)]
    while stack:
        node, level = stack.pop()
        if level > deepest:
            deepest = level
        if level > MAX_DEPTH:
            return level
        for child in node:
            stack.append((child, level + 1))
    return deepest


def _hardened_lxml_parser() -> etree.XMLParser:
    """The only lxml parser configuration in this codebase.

    Every flag here is load-bearing:
    - `resolve_entities=False` — do not expand entity references.
    - `no_network=True` — never fetch anything, including the DOCTYPE's SYSTEM
      URL. See the module docstring on why this one is specifically tempting
      to get wrong in a validation service.
    - `load_dtd=False` — do not load the declared DTD. `validation.py` loads a
      LOCAL one explicitly and validates against that.
    - `huge_tree=False` — keep libxml2's own depth/size guards on. They
      overlap with MAX_DEPTH above; overlapping guards are fine.
    - `recover=False` — a malformed document is refused, never silently
      repaired into something that then validates. A validator that quietly
      fixes its input is worse than no validator, because it reports success
      for a document the buyer's real supplier will reject.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )


class SafeDocument:
    """Both trees for one already-vetted document, plus the raw bytes.

    Two engines are exposed because they are good at different things and
    converting between them costs a reserialise: `element` (stdlib, via
    defusedxml) is what extraction code walks, and `tree` (lxml) is what
    `validation.py` validates. Holding `raw` alongside them keeps the
    "verify over bytes, before parsing" discipline available to callers
    without re-encoding a tree and hoping it round-trips."""

    __slots__ = ("raw", "element", "tree", "declared_dtd")

    def __init__(self, raw: bytes, element: Element, tree, declared_dtd: Optional[str]):
        self.raw = raw
        self.element = element
        self.tree = tree
        self.declared_dtd = declared_dtd


def parse(raw: bytes) -> SafeDocument:
    """The ONE front door. Returns a `SafeDocument`, or raises `XmlRejected` —
    never a partially-trusted result.

    Callers verify any shared secret over `raw` BEFORE calling this."""
    if not isinstance(raw, (bytes, bytearray)):
        raise XmlRejected(
            "expected raw bytes — decode/encode decisions belong here, not at the call site"
        )
    if not raw.strip():
        raise XmlRejected("empty document")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise XmlRejected(f"document is {len(raw)} bytes, over the {MAX_DOCUMENT_BYTES}-byte limit")

    # --- Stage 1: defusedxml decides whether this is hostile. -------------
    try:
        element = _defused_fromstring(raw)
    except Exception as exc:
        # EntitiesForbidden / DTDForbidden / ExternalReferenceForbidden for the
        # hostile cases, ParseError for merely malformed. All of them mean "we
        # are not processing this"; the distinction is not the caller's
        # business, but the type name is, for the log.
        raise XmlRejected(f"refused by the XML parser: {type(exc).__name__}") from exc

    depth = _depth(element)
    if depth > MAX_DEPTH:
        raise XmlRejected(f"document nests {depth} levels, over the {MAX_DEPTH} limit")

    # --- Stage 2: only now does lxml see it. ------------------------------
    try:
        tree = etree.fromstring(raw, parser=_hardened_lxml_parser())
    except etree.XMLSyntaxError as exc:
        # Reaching here means defusedxml accepted something lxml would not.
        # That is interesting enough to say plainly rather than fold into the
        # generic message above — the two parsers disagreeing is either a bug
        # in our understanding or a genuinely exotic document.
        raise XmlRejected(f"accepted by defusedxml but refused by lxml: {exc.__class__.__name__}") from exc

    declared = None
    docinfo = getattr(tree.getroottree(), "docinfo", None)
    if docinfo is not None:
        # Reported to the user as an observation only. NEVER dereferenced.
        declared = docinfo.system_url

    return SafeDocument(raw=bytes(raw), element=element, tree=tree, declared_dtd=declared)


def verify_shared_secret(
    raw: bytes,
    *,
    expected_secret: str,
    presented_secret: Optional[str],
) -> None:
    """Constant-time comparison of a presented cXML shared secret against the
    expected one. Raises `SharedSecretMismatch`; returns `None` on success, so
    a caller cannot accidentally treat a falsy return as a pass.

    `raw` is accepted and deliberately unused, to keep the call shape honest
    about ordering: this exists to be called with the raw body in hand, before
    `parse`.

    `hmac.compare_digest` rather than `==` — a secret compared with `==` leaks
    its prefix through timing. The cost of getting this right is one import.

    A NOTE SPECIFIC TO THIS PRODUCT. A sandbox is a place where people expect
    to be told what they got wrong, and it is tempting to answer a bad secret
    with "expected `abc123`, got `abc124`". Do not. The sandbox issues each
    tenant their own credentials, and echoing a *correct* secret back to
    whoever guesses near it turns a test harness into an oracle for other
    tenants' credentials. Tell the user their secret did not match, and show
    them their own secret on their own authenticated console page instead."""
    if presented_secret is None:
        raise SharedSecretMismatch("no shared secret presented")
    if not expected_secret:
        # An empty expected secret would make compare_digest pass against an
        # empty presented one. Refuse rather than authenticate nobody.
        raise SharedSecretMismatch("no shared secret configured for this tenant")
    if not hmac.compare_digest(presented_secret, expected_secret):
        raise SharedSecretMismatch("shared secret does not match")
