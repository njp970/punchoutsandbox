"""The contact form.

=============================================================================
WHY A FORM AND NOT A MAILTO: LINK
=============================================================================
A `mailto:` costs nothing to add and works for roughly the half of visitors
who have a desktop mail client wired up. For everyone else it opens an app
they do not use, and they give up. This tool's audience is often behind a
corporate desktop where the mail client is webmail in another tab.

It also publishes the address, which is how you acquire spam forever.

=============================================================================
WHAT KEEPS THIS FROM BECOMING A SPAM RELAY
=============================================================================
Four things, in descending order of how much they matter:

1. **The recipient is fixed in the environment** (`mailer.send` takes no `to`).
   Everything else on this list is hardening; that one is the property that
   makes abuse pointless rather than merely inconvenient.
2. **Three submissions per IP per day** (`tenants.CONTACT_DAILY_LIMIT`),
   sharing the counter that meters anonymous validations.
3. **A honeypot field** that a human never sees and a naive bot always fills.
   Not a serious defence against anyone targeting this specifically; a
   completely effective one against the indiscriminate form-stuffing that is
   99% of what a public form receives.
4. **Length caps and a minimum**, so a one-character message is refused.

Deliberately NOT here: a CAPTCHA. It would be the only thing on the site that
treats the visitor as a suspect, for a form that reaches one mailbox with a
three-a-day cap on it.

=============================================================================
A MESSAGE IS NEVER LOST QUIETLY
=============================================================================
If SES fails, the visitor is still thanked — the failure is ours, not theirs,
and telling them to try again would only produce a duplicate that fails too.
But the message is then written to the log as a `contact_undelivered` event
INCLUDING its text, so it can be recovered from CloudWatch rather than
vanishing. That is a deliberate exception to `telemetry`'s rule about not
logging user content: this content was written expressly to be read by us, and
the alternative to logging it is losing it.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Optional

from . import mailer, signup, telemetry, tenants
from .http import Request, Response, html
from .ui.render import render

#: How long a message is kept. Thirty days rather than the seven orders get:
#: the point of keeping it at all is to survive a missed or filtered weekly
#: digest, and a week only just covers one digest cycle.
MESSAGE_TTL_SECONDS = 30 * 24 * 3600

#: Local development only, exactly as `sessions.MemoryStore` is. Never
#: selected when a table is configured.
_local_messages: list[dict] = []


MAX_NAME = 120
MAX_EMAIL = 200
MAX_MESSAGE = 4_000
MIN_MESSAGE = 10

#: The honeypot. Named for something a form-filling bot expects to find and a
#: person never sees, and left out of the visible layout entirely.
HONEYPOT_FIELD = "website"

#: Offered as a dropdown so most messages arrive pre-sorted, and because a
#: blank textarea is harder to start writing into than a labelled one.
TOPICS = [
    ("bug", "Something is wrong with the sandbox"),
    ("conformance", "A platform limit you should know about"),
    ("help", "I cannot get my punchout working"),
    ("other", "Something else"),
]
_TOPIC_KEYS = {key for key, _ in TOPICS}


def save_message(record: dict) -> None:
    """Keep a copy of what somebody wrote.

    =========================================================================
    WHY A MESSAGE IS STORED AS WELL AS EMAILED
    =========================================================================
    A message really was delivered to the operator's mail server, accepted
    without a bounce, and never seen — filtered into a junk folder by a
    two-week-old sending domain. All that survived on our side was
    `topic=help`, because the body is only written to the log when sending
    FAILS, and this one succeeded.

    For a service whose entire feedback channel is this one form, that is a
    bad place to have a single point of failure. The mail is still the primary
    route; this is the copy that makes a filtered mail recoverable, and the
    weekly digest reads from it.

    Never raises. A message that reached the inbox but could not be filed is
    not worth failing the request over — the sender would be told to try
    again, and would send a duplicate that failed the same way."""
    try:
        table_name = os.environ.get("SANDBOX_TABLE")
        if not table_name:
            _local_messages.append(record)
            return
        import boto3
        boto3.resource("dynamodb").Table(table_name).put_item(Item={
            "pk": f"MESSAGE#{record['id']}", "sk": "META",
            **{k: v for k, v in record.items() if k != "id"},
            "expires_at": int(time.time()) + MESSAGE_TTL_SECONDS,
        })
    except Exception as exc:
        telemetry.event("contact_store_failed", error=type(exc).__name__)


def _page(*, request: Request, error: Optional[str] = None, sent: bool = False,
          values: Optional[dict] = None, status: int = 200) -> Response:
    tenant = signup.current_tenant(request)
    values = values or {}
    if tenant is not None and not values.get("email"):
        values = {**values, "email": tenant.email}
    return html(render("contact.html", nav="contact", canonical="/contact",
                       error=error, sent=sent,
                       values=values, topics=TOPICS,
                       honeypot=HONEYPOT_FIELD,
                       signed_in=tenant is not None), status=status)


def view_contact(request: Request) -> Response:
    if request.method == "GET":
        return _page(request=request)

    form = request.form()

    # The honeypot is checked before anything else and answered with the same
    # success page a human gets. A bot that is told it failed will retry with
    # the field cleared; a bot that is told it succeeded goes away.
    if (form.get(HONEYPOT_FIELD) or "").strip():
        telemetry.event("contact_honeypot",
                        ip=telemetry.ip_tag(tenants.client_ip(request.headers)))
        return _page(request=request, sent=True)

    name = (form.get("name") or "").strip()[:MAX_NAME]
    email = (form.get("email") or "").strip()[:MAX_EMAIL]
    topic = form.get("topic") if form.get("topic") in _TOPIC_KEYS else "other"
    message = (form.get("message") or "").strip()[:MAX_MESSAGE]
    values = {"name": name, "email": email, "topic": topic, "message": message}

    if not mailer.safe_address(email):
        return _page(request=request, values=values, status=400,
                     error="That email address does not look right, and it is "
                           "the only way to reply to you.")
    if len(message) < MIN_MESSAGE:
        return _page(request=request, values=values, status=400,
                     error="Tell us a little more than that.")

    ip = tenants.client_ip(request.headers)
    allowed, _ = tenants.contact_check_quota(ip, today=signup.today())
    if not allowed:
        return _page(request=request, values=values, status=429,
                     error=f"That is {tenants.CONTACT_DAILY_LIMIT} messages "
                           "from here today, which is the limit. If it is "
                           "urgent, it is already in the inbox.")

    tenant = signup.current_tenant(request)
    body = _compose(name=name, email=email, topic=topic, message=message,
                    tenant=tenant, ip=ip)
    delivered = mailer.send(
        subject=f"[PunchOut Sandbox] {topic}: {(name or email)}",
        body=body,
        # Their address, so a reply goes to them — but the message itself was
        # only ever sent to us, so nothing reaches this address unless a human
        # decides to write back. See mailer.py on why that distinction is what
        # keeps SES reputation safe.
        reply_to=email,
        kind="contact",
    )
    # Filed whether or not the mail went out. A delivered message that lands
    # in a junk folder is exactly as lost as one that never sent.
    save_message({
        "id": f"{int(time.time())}-{secrets.token_urlsafe(6)}",
        "received_at": int(time.time()),
        "topic": topic, "name": name, "email": email, "message": message,
        "delivered": delivered,
        "signed_in": tenant is not None,
    })

    if not delivered:
        telemetry.event("contact_undelivered", topic=topic, email=email,
                        message=message)

    telemetry.event("contact_received", topic=topic, delivered=delivered,
                    signed_in=tenant is not None, ip=telemetry.ip_tag(ip))
    return _page(request=request, sent=True)


def _compose(*, name: str, email: str, topic: str, message: str,
             tenant, ip: str) -> str:
    """Build the email body.

    Everything a visitor typed goes below a marker line and nothing above it,
    so that no submitted text can be mistaken for a field we generated. It is
    a plain-text message to one known mailbox, so this is presentation rather
    than security — but the habit is worth keeping."""
    known = "not signed in"
    if tenant is not None:
        known = f"account {tenant.tenant_id} ({tenant.email})"

    return (
        f"Topic   : {topic}\n"
        f"From    : {name or '(no name)'} <{email}>\n"
        f"Account : {known}\n"
        f"Source  : {telemetry.ip_tag(ip)} (hashed)\n"
        "\n"
        "-- message below is visitor-supplied, treat as untrusted --\n\n"
        f"{message}\n"
    )
