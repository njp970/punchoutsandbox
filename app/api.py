"""The machine path — JSON in, JSON out, credentials in headers.

=============================================================================
WHY THIS EXISTS
=============================================================================
Everything useful here was reachable only through an HTML form, and the
audience is machines. Three separate complaints turned out to be the same
missing piece:

  * "the anonymous cap appears keyed on address, not identity" — because
    `current_tenant` reads a browser COOKIE, so a client holding perfectly
    good issued credentials was metered as an anonymous stranger;
  * "an agent can't sign itself in" — because the only way to obtain that
    cookie was to submit a form;
  * "errors=3 with no enumeration" — a `Status` string is a poor container for
    structured findings, and there was no other shape on offer.

So: credentials in headers, findings as JSON.

=============================================================================
AUTHENTICATION — THE SAME CREDENTIALS, A THIRD TRANSPORT
=============================================================================
No new secret and no API key. The identity and shared secret issued at
`/signup` already authenticate the cXML and OCI endpoints; this adds a third
way to present them, for callers that are neither a browser nor a cXML
document.

    X-Sandbox-Identity: PSB123456789
    X-Sandbox-Secret:   <shared secret>

HTTP Basic is accepted too — identity as username, secret as password —
because a great many HTTP clients can do Basic with one argument and headers
with none.

The secret is compared in constant time, as everywhere else.

=============================================================================
WHY 401 HERE AND 200-WITH-A-STATUS ON THE cXML ENDPOINTS
=============================================================================
The cXML endpoints answer HTTP 200 carrying a cXML `Status` even when they
refuse, because the spec treats any reply without valid cXML as a transport
error that clients retry for ten hours. That reasoning is specific to cXML
clients and does not apply to a JSON API, where 401 means what it says and no
retry storm follows. Copying the cXML convention here would be cargo cult.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from . import platforms, signup, telemetry, tenants
from .differ import extract_lines
from .http import Request, Response, json_response
from .validation import validate
from .xml_safe import XmlRejected, parse

#: Generous for a document, small enough that no single call is a denial of
#: service on its own. `xml_safe` caps the parse at 4MB regardless.
MAX_BODY_BYTES = 1024 * 1024


def authenticate(request: Request):
    """Resolve a Tenant from headers, or None.

    Checked in addition to — never instead of — the browser cookie, so the
    same route serves a person and a script."""
    identity = request.headers.get("x-sandbox-identity", "").strip()
    secret = request.headers.get("x-sandbox-secret", "").strip()

    if not identity:
        raw = request.headers.get("authorization", "")
        if raw.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(raw[6:]).decode("utf-8", "replace")
                identity, _, secret = decoded.partition(":")
            except Exception:
                return None

    if not identity or not secret:
        return None
    tenant = tenants.store().by_sandbox_id(identity.strip())
    if tenant is None:
        return None
    return tenant if tenants.verify_secret(secret, tenant.shared_secret) else None


def _unauthorised() -> Response:
    return json_response({
        "error": "unauthenticated",
        "message": ("Present the identity and shared secret issued at "
                    "/signup, either as X-Sandbox-Identity and "
                    "X-Sandbox-Secret headers or as HTTP Basic "
                    "(identity as the username)."),
        "signup": "https://punchoutsandbox.com/api/signup",
    }, status=401)


def _document_from(request: Request) -> tuple[Optional[str], Optional[Response]]:
    """Accept the document as a raw body or as JSON `{"document": "..."}`.

    Both, because a caller with a file reaches for `--data-binary` and a
    caller assembling a request reaches for JSON, and refusing either would be
    a pointless thing to make somebody read the docs about."""
    if len(request.body) > MAX_BODY_BYTES:
        return None, json_response({
            "error": "too-large",
            "message": f"{len(request.body):,} bytes; the limit is "
                       f"{MAX_BODY_BYTES:,}.",
        }, status=413)

    raw = request.body.decode("utf-8", "replace").strip()
    if not raw:
        return None, json_response(
            {"error": "empty", "message": "Send the document as the request "
             "body, or as JSON {\"document\": \"...\"}."}, status=400)

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, json_response(
                {"error": "bad-json", "message": str(exc)}, status=400)
        document = (payload.get("document") or "").strip()
        if not document:
            return None, json_response(
                {"error": "empty", "message": 'JSON body needs a "document" key.'},
                status=400)
        return document, None
    return raw, None


def view_validate(request: Request) -> Response:
    """`POST /api/validate` — the conformance report, as data.

    Returns the WHOLE report: every error with its line, element and hint.
    The cXML endpoints can only offer a `Status` string, and reporting
    `errors=3` there without enumerating them made people bisect their own
    document to find what we already knew."""
    tenant = authenticate(request)
    if tenant is None:
        return _unauthorised()

    allowed, remaining = tenant.check_quota(today=signup.today())
    tenants.store().put(tenant)
    if not allowed:
        return json_response({
            "error": "quota-exceeded",
            "message": f"This account has used its {tenants.DAILY_QUOTA} "
                       "operations for today. It resets at midnight UTC.",
        }, status=429)

    document, refusal = _document_from(request)
    if refusal is not None:
        return refusal

    try:
        parsed = parse(document.encode("utf-8"))
    except XmlRejected as exc:
        # Refused at the door, which is a different thing from failing
        # validation: this document was never processed at all.
        return json_response({
            "error": "refused",
            "message": str(exc),
            "note": ("Refusal happens before parsing — the document was "
                     "rejected as malformed or hostile, not judged."),
        }, status=422)

    report = validate(parsed)
    telemetry.event("api_validate", document_type=report.document_type,
                    errors=len(report.errors))
    payload = report.as_dict()
    payload["quotaRemaining"] = remaining
    return json_response(payload)


def view_ingest(request: Request) -> Response:
    """`POST /api/ingest` — what each buyer platform would do to this cart."""
    tenant = authenticate(request)
    if tenant is None:
        return _unauthorised()

    document, refusal = _document_from(request)
    if refusal is not None:
        return refusal
    try:
        lines = extract_lines(parse(document.encode("utf-8")))
    except XmlRejected as exc:
        return json_response({"error": "refused", "message": str(exc)}, status=422)
    if not lines:
        return json_response({
            "error": "no-lines",
            "message": ("No ItemIn or ItemOut elements found. This endpoint "
                        "needs a PunchOutOrderMessage or an OrderRequest."),
        }, status=400)

    results = []
    for profile in platforms.PROFILES:
        outcome = platforms.ingest(lines, profile.key)
        results.append({
            "platform": profile.key,
            "name": profile.name,
            "verdict": outcome.verdict,
            "caveat": profile.caveat or None,
            "effects": [
                {"line": e.line_index, "field": e.field, "outcome": e.outcome,
                 "detail": e.detail, "verified": e.verified}
                for e in outcome.effects
            ],
        })
    return json_response({"lineCount": len(lines), "platforms": results})


def view_signup(request: Request) -> Response:
    """`POST /api/signup` — credentials without a browser.

    An agent integrating against this cannot fill in a form, and the whole
    audience is machines. Same store, same one-field contract, same absence of
    a password as the HTML page — only the shape differs."""
    raw = request.body.decode("utf-8", "replace").strip()
    email = company = ""
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return json_response({"error": "bad-json", "message": str(exc)},
                                 status=400)
        email = (payload.get("email") or "").strip()
        company = (payload.get("company") or "").strip()
    else:
        form = request.form()
        email = (form.get("email") or "").strip()
        company = (form.get("company") or "").strip()

    if not tenants.valid_email(email):
        return json_response({
            "error": "bad-email",
            "message": "Send {\"email\": \"you@company.com\"}. It is the only "
                       "required field, and it is not verified — it is how we "
                       "reach you if something you depend on changes.",
        }, status=400)

    tenant = signup.create_tenant(email, company)
    telemetry.event("api_signup")
    return json_response({
        "identity": tenant.sandbox_id,
        "sharedSecret": tenant.shared_secret,
        "dailyQuota": tenants.DAILY_QUOTA,
        "usage": {
            "headers": {"X-Sandbox-Identity": tenant.sandbox_id,
                        "X-Sandbox-Secret": tenant.shared_secret},
            "cxml": ("Put the identity in To/Credential/Identity and the "
                     "secret in Sender/Credential/SharedSecret."),
            "oci": "Send them as USERNAME and PASSWORD.",
        },
        "endpoints": {
            "validate": "POST /api/validate",
            "ingest": "POST /api/ingest",
            "punchoutSetup": "POST /punchout/setup",
            "orderInbox": "POST /order",
            "ociSetup": "POST /oci/setup",
        },
    }, status=201)
