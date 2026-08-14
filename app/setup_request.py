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
CREDENTIALS ARE RECORDED, NOT ENFORCED
=============================================================================
A real supplier checks the shared secret. This sandbox cannot: it has no
prior relationship with anyone, and demanding credentials it never issued
would make it unusable by the people it is for.

What it does instead is **report exactly what it saw** — the From, To and
Sender identities, their domains, and whether a SharedSecret was present.
That is more useful than an authentication failure, because "my credentials
are not arriving in the field I think they are" is itself one of the common
integration bugs, and no other tool will show you.

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
