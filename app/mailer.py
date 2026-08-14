"""Outbound email, via SES.

*Two callers: the contact form (`app/contact.py`) and the anonymous-quota
alert (`app/handler.py`). Both send to the operator and only to the operator.*

=============================================================================
THE RECIPIENT IS AN ENVIRONMENT VARIABLE, NEVER AN ARGUMENT
=============================================================================
This is the single most important line in the module and the reason `send()`
takes no `to` parameter. A contact form that can be told where to deliver is
an open relay wearing a disguise: a spammer POSTs their own recipient and
their own body, and your domain sends it with your reputation attached.

`CONTACT_TO` is set by the CDK stack. Nothing a visitor submits can influence
where a message goes — only what a message says, and only inside a body that
is clearly marked as untrusted input.

=============================================================================
WE SHARE XENIA'S SES ACCOUNT, SO REPUTATION IS SHARED TOO
=============================================================================
Sending identity, reputation metrics and the bounce/complaint rates that
govern them are ACCOUNT-level in SES. Xenia sends real transactional mail from
this account, so anything careless here would land on Xenia's deliverability
rather than on a free sandbox's.

What makes that safe is the constraint above: we never send to an address a
stranger supplied, so a stranger can never generate a bounce or a complaint.
Every message this module sends goes to one mailbox that has explicitly asked
for it. Their address travels in `Reply-To`, which delivers nothing until a
human chooses to answer.

The `From` identity is `punchoutsandbox.com`, DKIM-signed in its own right
(`infra/scripts/setup_ses_domain.py`) rather than borrowed from a Xenia
domain — so the sandbox neither wears Xenia's branding nor spends its DMARC
alignment.

=============================================================================
FAILURE IS SILENT TO THE VISITOR, LOUD IN THE LOG
=============================================================================
`send()` returns a bool and never raises. A contact form that 500s because SES
throttled has lost the message AND told the sender it was their fault. Instead
the caller thanks them, and the failure is recorded as an event so the loss is
visible to us rather than to them.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from . import telemetry

#: Header injection defence. An address containing CR, LF or a comma could
#: otherwise smuggle extra headers or extra recipients through `Reply-To`.
#: boto3 builds the message structurally rather than by string concatenation
#: so this is belt-and-braces — but the belt is one regex and the cost of
#: being wrong is sending mail on someone else's behalf.
_SAFE_ADDRESS = re.compile(r"^[^@\s,;:<>\r\n]+@[^@\s,;:<>\r\n.]+\.[^@\s,;:<>\r\n]{2,}$")

#: Hard ceiling on a single message. The contact form caps its own fields
#: well below this; the cap here is so no future caller can hand SES a
#: multi-megabyte body.
MAX_BODY_CHARS = 20_000

#: Populated instead of sending when SES is not configured — i.e. in tests and
#: local development. Lets a test assert on what WOULD have been sent without
#: mocking boto3 or reaching the network.
outbox: list[dict] = []


def configured() -> bool:
    return bool(os.environ.get("CONTACT_TO") and os.environ.get("MAIL_FROM"))


def safe_address(value: str) -> Optional[str]:
    """Return the address if it is safe to put in a header, else None."""
    value = (value or "").strip()
    return value if _SAFE_ADDRESS.match(value) else None


def send(*, subject: str, body: str, reply_to: Optional[str] = None,
         kind: str = "message") -> bool:
    """Send to the configured operator mailbox. Returns True on success.

    `kind` is a short label used only for telemetry, so that a failure can be
    attributed to the contact form or to the quota alert without logging
    either one's contents."""
    # Subjects are ours, not a visitor's, but a newline in one would split the
    # header regardless of where it came from.
    subject = " ".join((subject or "PunchOut Sandbox").split())[:180]
    body = (body or "")[:MAX_BODY_CHARS]
    reply = safe_address(reply_to) if reply_to else None

    if not configured():
        outbox.append({"subject": subject, "body": body, "reply_to": reply,
                       "kind": kind})
        telemetry.event("mail_unconfigured", kind=kind)
        return False

    try:
        import boto3
        message = {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        }
        kwargs = {
            "Source": os.environ["MAIL_FROM"],
            "Destination": {"ToAddresses": [os.environ["CONTACT_TO"]]},
            "Message": message,
        }
        if reply:
            kwargs["ReplyToAddresses"] = [reply]
        boto3.client("ses").send_email(**kwargs)
        telemetry.event("mail_sent", kind=kind)
        return True
    except Exception as exc:
        # Deliberately broad: SES throttling, a revoked identity and a network
        # blip all mean the same thing to the caller, and none of them should
        # reach a visitor as a 500.
        telemetry.event("mail_failed", kind=kind, error=type(exc).__name__)
        return False
