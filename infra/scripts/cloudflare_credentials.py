"""One place that answers "how do I authenticate to Cloudflare".

Every deploy script here used to carry its own copy of this logic, which is
how they drifted: the same `_auth_headers()` appeared five times and each
grew slightly different error messages. Worse, they all read the credential
from `infra/.env`, so rotating it meant editing a file on one laptop and
nowhere else.

=============================================================================
RESOLUTION ORDER, AND WHY
=============================================================================
1. **`CLOUDFLARE_API_TOKEN`** in the environment. An explicit override beats
   a lookup, and it is how you test a new token before storing it.
2. **AWS Secrets Manager** — `xenia/dev/cloudflare/dns-token`, the same secret
   Xenia's own tooling reads. This is the normal path. The credential lives in
   one place, rotates in one place, and never touches a developer's disk.
3. **`CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY`** — the legacy Global API Key,
   last and deprecated. It cannot be scoped, it grants everything on the
   account, and this project already lost an afternoon to it: the value in
   `infra/.env` stopped working with `9103 Unknown X-Auth-Key`, which looks
   identical to a *scoped token stored in the wrong variable* because the two
   are sent as different headers.

=============================================================================
THE SECRET'S DESCRIPTION IS NOT ITS SCOPE
=============================================================================
That secret is described as covering `onxenia.com`. It actually reaches six
zones including `punchoutsandbox.com`, which we established by asking
Cloudflare rather than by reading the label. `describe()` below does the same
check on demand, because a token that silently cannot see a zone produces a
"zone not found" error that reads like the zone is missing.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests

CF_API = "https://api.cloudflare.com/client/v4"
SECRET_ID = "xenia/dev/cloudflare/dns-token"
SECRET_REGION = "eu-west-2"


def _from_secrets_manager() -> Optional[str]:
    """Fetch the token, or None if it is unreachable for any reason.

    Deliberately swallows every exception: no credentials, no permission, no
    such secret and no network all mean the same thing to the caller — this
    source is unavailable, try the next one. The final failure message comes
    from `auth_headers`, which can describe all three sources at once."""
    try:
        import boto3
        raw = boto3.client("secretsmanager", region_name=SECRET_REGION) \
            .get_secret_value(SecretId=SECRET_ID)["SecretString"]
    except Exception:
        return None
    raw = raw.strip()
    if not raw.startswith("{"):
        return raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    # Stored as a bare string by some tools and as {"token": …} by others.
    for key in ("token", "api_token", "CLOUDFLARE_API_TOKEN", "value"):
        if payload.get(key):
            return str(payload[key])
    return str(next(iter(payload.values()))) if payload else None


def auth_headers() -> tuple[dict[str, str], str]:
    """Return `(headers, human description)`.

    The description is printed by every script so the operator can see WHICH
    credential was used — silently picking one of three is how you end up
    debugging a 403 against the wrong one."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}, "scoped token (environment)"

    token = _from_secrets_manager()
    if token:
        return ({"Authorization": f"Bearer {token}"},
                f"scoped token (Secrets Manager {SECRET_ID})")

    email = os.environ.get("CLOUDFLARE_EMAIL")
    key = os.environ.get("CLOUDFLARE_API_KEY")
    if email and key:
        return ({"X-Auth-Email": email, "X-Auth-Key": key},
                f"legacy Global API Key ({email}) — DEPRECATED, prefer a token")

    raise SystemExit(
        "No usable Cloudflare credentials. In order of preference:\n\n"
        f"  1. AWS Secrets Manager {SECRET_ID} in {SECRET_REGION}\n"
        "     (needs AWS credentials — try AWS_PROFILE=xenia)\n"
        "  2. CLOUDFLARE_API_TOKEN in the environment\n"
        "  3. CLOUDFLARE_EMAIL + CLOUDFLARE_API_KEY (legacy, deprecated)\n\n"
        "  Note that a scoped TOKEN placed in CLOUDFLARE_API_KEY fails with\n"
        "  '9103 Unknown X-Auth-Key' — they are sent as different headers.")


def session() -> requests.Session:
    http = requests.Session()
    headers, description = auth_headers()
    http.headers.update(headers)
    print(f"auth    : {description}")
    return http


def zone_id(http: requests.Session, name: str) -> str:
    """Resolve a zone, distinguishing "not on the account" from "not visible
    to this token" — which are different problems with the same error."""
    body = http.get(f"{CF_API}/zones", params={"name": name}, timeout=30).json()
    if body.get("result"):
        return body["result"][0]["id"]

    visible = http.get(f"{CF_API}/zones", params={"per_page": 50},
                       timeout=30).json().get("result") or []
    raise SystemExit(
        f"zone {name} not found.\n\n"
        f"  This credential can see: {', '.join(z['name'] for z in visible) or '(none)'}\n"
        f"  If {name} is not in that list, the token is scoped to other zones "
        "and needs this one added — the secret's description is not its scope.")
