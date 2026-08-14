"""Outbound document delivery — POSTing cXML to a buyer's inbox.

*This is the only place in the application that makes an outbound network
request on someone else's instruction, and that makes it the highest-risk
module in the repository. Read the threat model before changing anything.*

=============================================================================
WHAT THIS IS FOR
=============================================================================
Real cXML fulfilment is PUSH. The supplier POSTs `ConfirmationRequest`,
`ShipNoticeRequest` and `InvoiceDetailRequest` to a URL the buyer published,
and the buyer answers with a cXML `Response` carrying a `Status`.

A sandbox that only generated those documents would test half the problem. The
half that actually breaks is the receiving end: whether the buyer's inbox
accepts the document, whether it authenticates the credentials, whether it
returns a conformant response. None of that can be exercised by looking at
generated XML.

=============================================================================
THE THREAT: THIS IS AN SSRF PRIMITIVE UNLESS IT IS CONSTRAINED
=============================================================================
"Take a URL from a stranger and POST to it" is the definition of server-side
request forgery. Unconstrained, this endpoint would let anyone use our Lambda
to reach hosts they cannot reach themselves, and to send traffic that appears
to originate from an AWS account belonging to someone else.

Five constraints, each closing a specific hole:

1. **An account is required.** `handler.py` gates every delivery route, so a
   drive-by visitor cannot reach this at all. Not a strong control on its own —
   signup is free — but it makes abuse attributable and rate-limitable.

2. **HTTPS on port 443 only.** No `http://`, no `file://`, no `gopher://`, no
   alternative ports. This removes protocol-smuggling entirely and stops the
   classic "scan the internal network by port" use.

3. **The resolved address must be global unicast.** Every address the hostname
   resolves to is checked against loopback, private, link-local, multicast and
   reserved ranges, and ALL of them must pass — checking only the first answer
   lets a multi-record response through. This is what keeps the function away
   from `169.254.169.254`, from `127.0.0.1:9001` (the Lambda Runtime API), and
   from anything else that answers on a private address.

4. **The vetted address is pinned for the actual connection.** Checking DNS and
   then connecting by hostname leaves a rebinding window: an attacker serving a
   one-second TTL can answer the check with a public address and the connection
   with a private one. `_PinnedConnection` connects to the exact IP that was
   checked while still presenting the hostname for SNI and certificate
   validation, so the window does not exist rather than merely being narrow.

5. **No redirects are followed.** A 302 to `http://169.254.169.254/` would
   otherwise walk straight through every check above. A redirect is reported
   to the user as the response it is.

Two things bound the damage even if all of that failed: the Lambda is not in a
VPC, so it has no route to anything private in the account, and Lambda has no
instance metadata service to steal credentials from.

=============================================================================
NO AUTOMATIC RETRIES, WHICH REVERSES THE SPEC ON PURPOSE
=============================================================================
cXML tells a supplier to retry a transport failure ten times, hourly. That is
right for production and wrong here, for two reasons.

A person clicking "send" in a sandbox is debugging: they want to see the
failure, now, with the response body attached. A retry that eventually succeeds
hides the flakiness they are trying to find. And automatic retries turn any
delivery endpoint into a modest amplifier — one submission, ten outbound
requests — which is not a property to hand to the internet for free.

So: one attempt, the full result recorded, and a re-send button. The retry
policy stays where it belongs, in the hands of the person watching.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from . import telemetry

#: Per-attempt ceiling. The Lambda's own timeout is 30s and a user is watching
#: a browser tab, so a buyer endpoint that has not answered in 12 seconds is
#: reported as a timeout rather than waited on.
TIMEOUT_SECONDS = 12

#: How much of the buyer's response we read. A cXML Response is a few hundred
#: bytes; anything returning more is sending us an HTML error page, and the
#: first 32KB of it is more than enough to identify.
MAX_RESPONSE_BYTES = 32 * 1024

#: What we will send. Well above any real fulfilment document.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class DeliveryRefused(Exception):
    """The URL was refused before any connection was attempted.

    Distinct from a delivery that was attempted and failed: nothing left the
    building, so there is nothing for the user to debug at the far end."""


@dataclass
class DeliveryResult:
    url: str
    ok: bool
    #: HTTP status, or None when the connection never completed.
    status: Optional[int] = None
    reason: str = ""
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    #: Parsed out of the buyer's cXML response, when it sent one.
    cxml_status_code: Optional[str] = None
    cxml_status_text: str = ""
    #: Things worth telling the user about what came back.
    observations: list[str] = field(default_factory=list)


class _PinnedConnection(http.client.HTTPSConnection):
    """Connects to a vetted IP while presenting the original hostname.

    This is the whole defence against DNS rebinding. `self.host` stays the
    hostname — so the `Host` header, SNI and certificate validation are all
    correct — while the socket is opened to the address that was actually
    checked."""

    def __init__(self, hostname: str, address: str, **kwargs) -> None:
        super().__init__(hostname, **kwargs)
        self._pinned = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned, self.port),
                                             self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _resolve_and_vet(hostname: str) -> str:
    """Resolve, refuse anything that is not globally routable, return one
    address to pin.

    EVERY answer must pass. A hostname resolving to one public and one private
    address is refused outright — accepting it and picking the public one would
    mean the refusal depends on resolver ordering."""
    try:
        answers = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DeliveryRefused(
            f"{hostname} does not resolve ({exc.strerror or exc}). Nothing was "
            "sent.") from exc

    if not answers:
        raise DeliveryRefused(f"{hostname} resolved to no addresses.")

    addresses = []
    for *_, sockaddr in answers:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise DeliveryRefused(
                f"{hostname} resolves to {address}, which is not a public "
                "address. This sandbox will not POST to private, loopback or "
                "link-local addresses — it runs in someone else's AWS account, "
                "and an endpoint that reaches internal hosts on request is an "
                "SSRF primitive. Nothing was sent.")
        addresses.append(str(address))

    return addresses[0]


def vet_url(url: str) -> tuple[str, str]:
    """Check a buyer endpoint without sending anything.

    Returns `(hostname, pinned_address)`. Raises `DeliveryRefused` with a
    message written for the person who typed the URL. Used both before a send
    and when someone saves an endpoint in their settings, so the refusal
    arrives while they are still looking at the field."""
    parsed = urlparse((url or "").strip())

    if parsed.scheme != "https":
        raise DeliveryRefused(
            f'Endpoint must be https. Got "{parsed.scheme or "no scheme"}". '
            "cXML credentials travel in the document body, so plain HTTP would "
            "publish your shared secret to every hop on the path.")
    if not parsed.hostname:
        raise DeliveryRefused("No hostname in that URL.")
    if parsed.port not in (None, 443):
        raise DeliveryRefused(
            f"Port {parsed.port} is not allowed; only 443. Arbitrary ports turn "
            "this into a port scanner for whoever asks.")
    if parsed.username or parsed.password:
        raise DeliveryRefused(
            "Credentials in the URL are not accepted. cXML authenticates in "
            "the document, not in the URL.")

    return parsed.hostname, _resolve_and_vet(parsed.hostname)


def _read_cxml_status(body: str) -> tuple[Optional[str], str]:
    """Pull `Status/@code` and `@text` out of a buyer's response.

    Deliberately does NOT use `xml_safe.parse`. A buyer's reply is untrusted
    input, and running the full DTD-aware parser over it to extract two
    attributes would be a much larger surface than a substring search. If the
    user wants the response judged properly, `/validate` is one paste away.

    Returns `(None, "")` when there is no Status element, which includes the
    common case of an HTML error page."""
    marker = body.find("<Status")
    if marker == -1:
        return None, ""
    end = body.find(">", marker)
    if end == -1:
        return None, ""
    attrs = body[marker:end]

    def pick(name: str) -> str:
        key = f'{name}="'
        start = attrs.find(key)
        if start == -1:
            return ""
        start += len(key)
        stop = attrs.find('"', start)
        return attrs[start:stop] if stop != -1 else ""

    return (pick("code") or None), pick("text")


def _observe(result: DeliveryResult, body: str) -> None:
    """Things worth saying about the response, beyond its status code."""
    if result.status in (301, 302, 303, 307, 308):
        result.observations.append(
            f"The endpoint redirected ({result.status} to "
            f"{result.headers.get('location', 'an unspecified location')}). "
            "Redirects are NOT followed here — a redirect to a private address "
            "would walk straight past the address checks — so nothing was "
            "delivered. Configure the final URL directly.")

    content_type = result.headers.get("content-type", "")
    if result.status == 200 and "xml" not in content_type.lower():
        result.observations.append(
            f'The endpoint answered 200 with content-type "{content_type or "none"}". '
            "cXML expects text/xml. A 200 carrying HTML usually means the "
            "request reached a web server rather than a cXML inbox.")

    if result.status == 200 and result.cxml_status_code is None:
        result.observations.append(
            "HTTP 200 but no cXML Status element in the response. The spec "
            "treats any reply without valid cXML content as a TRANSPORT error, "
            "which a real supplier would retry hourly for ten hours — so a "
            "buyer that silently returns an empty 200 generates retry storms "
            "rather than the clean rejection it intends.")

    if result.cxml_status_code and not result.cxml_status_code.startswith("2"):
        result.observations.append(
            f"The buyer accepted the transport and rejected the document: cXML "
            f"Status {result.cxml_status_code} {result.cxml_status_text}. "
            "That is a business-level refusal, and it is the correct shape — "
            "the document arrived and was understood.")

    if result.status in (401, 403):
        result.observations.append(
            f"The endpoint rejected the request at the HTTP layer ({result.status}). "
            "cXML authenticates INSIDE the document, in the Sender Credential — "
            "so an endpoint demanding HTTP auth as well needs that configured "
            "separately, and this sandbox has nowhere to put it.")

    if result.status in (404, 405):
        result.observations.append(
            f"{result.status} — the URL is reachable but is not accepting a POST "
            "here. A cXML inbox is almost always a distinct path from the "
            "application's own pages; check you have the inbox URL rather than "
            "the site root.")

    if result.status and 400 <= result.status < 500 and result.status not in (401, 403, 404, 405):
        result.observations.append(
            f"A {result.status} is a permanent refusal, so cXML's retry guidance "
            "does NOT apply — re-sending the same document unchanged will fail "
            "the same way.")

    if result.status and result.status >= 500:
        result.observations.append(
            "A 5xx is the one case where cXML's retry guidance applies: the "
            "spec says treat it as transient and retry. This sandbox does not "
            "retry on your behalf, so re-send when the far end recovers.")


def deliver(url: str, document: bytes, *,
            content_type: str = "text/xml; charset=utf-8") -> DeliveryResult:
    """POST one document. One attempt. Never raises for a far-end failure.

    `DeliveryRefused` is raised only when we declined to send at all — that is
    a different thing from a failed delivery and the caller should present it
    differently."""
    if len(document) > MAX_DOCUMENT_BYTES:
        raise DeliveryRefused(
            f"Document is {len(document):,} bytes; the ceiling is "
            f"{MAX_DOCUMENT_BYTES:,}.")

    hostname, address = vet_url(url)
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    result = DeliveryResult(url=url, ok=False)
    started = time.time()
    connection = None
    try:
        context = ssl.create_default_context()
        connection = _PinnedConnection(hostname, address,
                                       timeout=TIMEOUT_SECONDS, context=context)
        connection.request(
            "POST", path, body=document,
            headers={
                "content-type": content_type,
                "content-length": str(len(document)),
                # Identifies us honestly. A buyer looking at their access log
                # should be able to tell what this traffic is and where it
                # came from without asking.
                "user-agent": "PunchOutSandbox/1.0 (+https://punchoutsandbox.com)",
                "accept": "text/xml",
                "connection": "close",
            })
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")

        result.status = response.status
        result.reason = response.reason or ""
        result.headers = {k.lower(): v for k, v in response.getheaders()}
        result.body = body
        result.cxml_status_code, result.cxml_status_text = _read_cxml_status(body)
        # "ok" means the transport worked. Whether the BUYER accepted the
        # document is a separate question with its own field — conflating them
        # is how a rejected invoice gets reported as delivered.
        result.ok = 200 <= response.status < 300
        _observe(result, body)
    except ssl.SSLCertVerificationError as exc:
        result.reason = (
            f"TLS certificate verification failed: {exc.verify_message or exc}. "
            "Self-signed certificates are not accepted — a sandbox that skipped "
            "verification would be teaching the wrong lesson about a channel "
            "carrying shared secrets.")
    except socket.timeout:
        result.reason = (f"No response within {TIMEOUT_SECONDS}s. The document "
                         "may or may not have been processed.")
    except OSError as exc:
        result.reason = f"Connection failed: {exc}"
    finally:
        if connection is not None:
            connection.close()
        result.duration_ms = int((time.time() - started) * 1000)

    telemetry.event("delivery", host=hostname, status=result.status,
                    ok=result.ok, ms=result.duration_ms,
                    cxml_status=result.cxml_status_code)
    return result
