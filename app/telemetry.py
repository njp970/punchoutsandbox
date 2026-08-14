"""One-line JSON events on stdout, which is CloudWatch Logs in Lambda.

=============================================================================
WHY THIS EXISTS AT ALL
=============================================================================
Until now this application logged nothing. That was survivable while every
limit was a per-account counter a signed-up human could tell us about, and
stopped being survivable the moment `/validate` opened to anonymous traffic
with a 25/day cap: a stranger who hits that limit sees a page, goes away, and
we never learn it happened. A limit you cannot observe is a limit you cannot
tune, and `ANON_DAILY_QUOTA` is explicitly a guess.

=============================================================================
JSON, BECAUSE OF WHAT READS IT
=============================================================================
A CloudWatch Logs metric filter can pattern-match `{ $.event = "..." }` and
extract a numeric field as a metric value. Prose log lines cannot do that
without a regex that breaks the first time the wording changes. So: one JSON
object per line, a stable `event` name, and no interpolation into the name.

=============================================================================
WHAT MUST NEVER GO IN HERE
=============================================================================
**Raw IP addresses.** An IP is personal data, `signup.html` tells people we
store an email, a company and a counter, and quietly accumulating addresses in
a log would make that page a lie. `ip_tag` hashes instead — enough to answer
"is this one persistent visitor or twenty different ones", useless for
identifying anybody, and unstable across deploys by design.

Nor document bodies. People paste real purchase orders into `/validate`;
that content belongs to them and is already discarded within the hour.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

#: Salts the IP hash. Absent in local dev, which is fine — the tag is only
#: meaningful within one deployment anyway.
_SALT = os.environ.get("EDGE_SHARED_SECRET", "local")


def ip_tag(ip: str) -> str:
    """A short, salted, one-way tag for an IP address.

    Not reversible and not intended to be. Two requests from the same address
    on the same deployment share a tag; that is the entire capability, and it
    is all the question "did one person hit the limit or did twenty" needs."""
    if not ip:
        return "none"
    digest = hashlib.sha256(f"{_SALT}|{ip}".encode()).hexdigest()
    return digest[:12]


def event(name: str, **fields) -> None:
    """Emit one structured event. Never raises — telemetry that can break the
    request it is describing is worse than no telemetry."""
    try:
        payload = {"event": name}
        payload.update(fields)
        print(json.dumps(payload, default=str), file=sys.stdout, flush=True)
    except Exception:  # pragma: no cover - defensive
        pass
