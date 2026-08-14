"""The punchout entry point — `PunchOutSetupRequest` in, `StartPage` out.

*This is the front door for a buyer system. Everything else in the sandbox is
reachable by a human with a browser; this is the only route a machine talks
to, and it is what makes the service a supplier rather than a website.*

=============================================================================
WE ACCEPT DOCUMENTS WE DISAGREE WITH, AND SAY SO
=============================================================================
The tempting design is to reject anything non-conformant: we own a DTD
validator, so why not enforce it at the door?

Because that would make the sandbox useless for its actual purpose. Someone
arrives here precisely BECAUSE their document is wrong and they cannot find
out why. A 400 that says "invalid" teaches them nothing; a working session
plus a full conformance report teaches them everything. Real buyer platforms
are also famously tolerant, so refusing what Ariba accepts would make us
stricter than production and train people for a world that does not exist.

So: **every syntactically parseable document gets a session**, and the
validation report is attached to that session for the user to read. Only
documents `xml_safe` refuses outright — hostile or unparseable — are turned
away, and then with a cXML `Status` rather than a bare HTTP error.

=============================================================================
CREDENTIALS ARE NOW ENFORCED — AND THIS REVERSED AN EARLIER DECISION
=============================================================================
This endpoint originally accepted any credentials and merely reported what it
saw, on the argument that a sandbox has no prior relationship with anyone.
The signup gate changed that: an account is issued an identity and a shared
secret, and `handler._authenticate_machine` requires them here.

The earlier argument was not wrong so much as incomplete. Demanding
credentials nobody had been issued would indeed have been useless — but
ISSUING them first, free and instantly, removes the objection entirely. And
exchanging a shared secret out of band before anything connects is exactly
how real punchout works, so requiring it makes the sandbox more faithful
rather than less.

What survives from the original design is the reporting. A request that
authenticates still gets back **exactly what we saw** in the `Status` text —
which identity arrived, in which element, and whether a SharedSecret was
present at all. "My credentials are not arriving in the field I think they
are" is itself one of the common integration bugs, and no other tool shows
you.

The one thing worth flagging back is a secret sent in a document that will
later travel through a browser (see `cxml/punchout.py` on one-way transport).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from .http import Request, Response
from .sessions import Session, store
from .validation import validate
from .xml_safe import XmlRejected, parse


def _text(root, path: str) -> Optional[str]:
    node = root.find(path)
    if node is None:
        return None
    value = "".join(node.itertext()).strip()
    return value or None


def _status_response(code: int, text: str, detail: str = "") -> Response:
    """A cXML `Status`-only response.

    Returned with **HTTP 200**, deliberately. The spec is explicit that any
    HTTP reply without valid cXML content is a TRANSPORT error, which clients
    must treat as transient and retry — ten times, hourly. Returning HTTP 400
    with an explanation would therefore turn a permanent, actionable refusal
    into an hours-long retry storm against us. Business-level errors ride
    inside a 200."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
        f'<cXML payloadID="{secrets.token_hex(8)}@punchoutsandbox.com" '
        f'timestamp="{datetime.now(timezone.utc).astimezone().isoformat()}">'
        f'<Response><Status code="{code}" text="{text}">'
        f"{detail}</Status></Response></cXML>"
    )
    return Response(status=200, body=body,
                    content_type="text/xml; charset=utf-8")


def handle_setup(request: Request, *, site_url: str) -> Response:
    """Accept a `PunchOutSetupRequest` and hand back a `StartPage` URL."""
    raw = request.body

    try:
        doc = parse(raw)
    except XmlRejected as exc:
        # 406 rather than 400: the spec's guidance is that a parse failure is
        # "Not Acceptable", and it reserves 400 for documents that parsed
        # correctly but are unacceptable for another reason.
        return _status_response(406, "Not Acceptable", str(exc))

    report = validate(doc, expected_type="PunchOutSetupRequest")
    if report.document_type != "PunchOutSetupRequest":
        return _status_response(
            400, "Bad Request",
            f"This endpoint expects a PunchOutSetupRequest; received "
            f"{report.document_type or 'an unrecognised document'}.")

    tree = doc.tree
    buyer_cookie = _text(tree, ".//BuyerCookie") or ""
    return_url = _text(tree, ".//BrowserFormPost/URL") or ""
    setup = tree.find(".//PunchOutSetupRequest")
    operation = (setup.get("operation") if setup is not None else None) or "create"

    # The BrowserFormPost URL is where the cart goes back to. Without it there
    # is no round trip at all, so this is the one thing worth refusing over —
    # and refusing now is far kinder than letting someone shop for ten minutes
    # and discover there is nowhere to return to.
    if not return_url:
        return _status_response(
            400, "Bad Request",
            "No BrowserFormPost/URL — there would be nowhere to return the "
            "cart to. This is the one field this sandbox insists on.")

    from_identity = _text(tree, ".//From/Credential/Identity")
    to_identity = _text(tree, ".//To/Credential/Identity")
    sender_identity = _text(tree, ".//Sender/Credential/Identity")
    has_secret = tree.find(".//SharedSecret") is not None

    session = Session(
        session_id=secrets.token_urlsafe(18),
        buyer_name=from_identity or "your procurement system",
        protocol="cXML",
        buyer_cookie=buyer_cookie,
        return_url=return_url,
        operation=operation,
    )
    store().put(session)

    # The StartPage URL carries the session token. The spec warns that this
    # URL "should refer to the state information rather than including it
    # all", because URL length limits bite — so it is an opaque token, not the
    # buyer cookie and identity inlined.
    start_page = f"{site_url}/shop?session={session.session_id}"

    observed = (
        f"operation={operation}; "
        f"from={from_identity or '(absent)'}; to={to_identity or '(absent)'}; "
        f"sender={sender_identity or '(absent)'}; "
        f"sharedSecret={'present' if has_secret else 'absent'}; "
        f"buyerCookie={'present' if buyer_cookie else 'ABSENT — the spec requires it'}; "
        f"conformant={report.conformant}; "
        f"errors={len(report.errors)}; advisories={len(report.advisories)}"
    )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">'
        f'<cXML payloadID="{secrets.token_hex(8)}@punchoutsandbox.com" '
        f'timestamp="{datetime.now(timezone.utc).astimezone().isoformat()}">'
        "<Response>"
        # The spec forbids returning a payload element alongside a non-2xx
        # status, so a PunchOutSetupResponse only ever accompanies a 200.
        f'<Status code="200" text="OK">{observed}</Status>'
        "<PunchOutSetupResponse><StartPage>"
        f"<URL>{start_page}</URL>"
        "</StartPage></PunchOutSetupResponse>"
        "</Response></cXML>"
    )
    return Response(status=200, body=body, content_type="text/xml; charset=utf-8")


def extract_credentials(raw: bytes) -> tuple[str, str]:
    """Pull the identity and shared secret out of a PunchOutSetupRequest.

    Used by the signup gate BEFORE the document is otherwise processed, so it
    goes through the same hardened parser as everything else — there is no
    "quick peek at the XML" path, because a quick peek at hostile XML is
    exactly what `xml_safe` exists to prevent.

    Returns `("", "")` on anything unparseable rather than raising: the caller
    is deciding whether to authenticate, and an unparseable body is simply not
    authenticated. The parse failure is reported properly later, by
    `handle_setup`, which is where a user-facing explanation belongs.

    Identity is looked for in `To` first, then `From`. `To` is the supplier —
    us — which is where a buyer configures the identity we issued them. Some
    buyer systems put it in `From` instead, so both are accepted."""
    try:
        doc = parse(raw)
    except XmlRejected:
        return "", ""
    tree = doc.tree
    identity = ""
    for path in (".//To/Credential/Identity", ".//From/Credential/Identity",
                 ".//Sender/Credential/Identity"):
        found = _text(tree, path)
        if found:
            identity = found
            break
    secret = _text(tree, ".//Sender/Credential/SharedSecret") or ""
    return identity, secret


def unauthorised_response() -> Response:
    """cXML 401 for an unrecognised credential.

    HTTP 200 carrying a cXML `Status`, per the spec: any HTTP reply without
    valid cXML content is a TRANSPORT error that clients retry ten times
    hourly. A 401 at the HTTP layer would turn "your credentials are wrong"
    into an all-day retry storm."""
    return _status_response(
        401, "Unauthorized",
        "Credentials not recognised. This sandbox issues a free identity and "
        "shared secret at https://punchoutsandbox.com/signup — put the "
        "identity in To/Credential/Identity and the secret in "
        "Sender/Credential/SharedSecret.")
