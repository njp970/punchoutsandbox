"""Does session state actually survive?

The bug this guards against is invisible with `MemoryStore`, because it hands
back the same object every time — so a handler that mutates a cart and forgets
to write it back appears to work perfectly, and then silently loses every
change on Lambda.

`CopyingStore` below reproduces DynamoDB's actual semantics: **every `get`
returns a fresh copy.** A handler that does not write back will fail here
exactly as it would in production. That is the whole point of this file, and
it is why the fake is not simply `MemoryStore` under another name.
"""
import base64
import copy
import sys
import time

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import sessions, tenants
from app.handler import handler
from app.tenants import MemoryTenants, Tenant
from app.sessions import DEFAULT_TTL_SECONDS, MemoryStore, Session

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


class CopyingStore(MemoryStore):
    """A store with DynamoDB's semantics: reads return copies, not aliases."""

    def __init__(self):
        super().__init__()
        self.writes = 0

    def get(self, session_id):
        found = super().get(session_id)
        return copy.deepcopy(found) if found is not None else None

    def put(self, session):
        self.writes += 1
        super().put(copy.deepcopy(session))


# The signup gate means every gated route needs an account. Established once
# here rather than per-test: the gate is not what this file is testing, and a
# missing cookie would fail these assertions for entirely the wrong reason.
_ACCOUNT = "test-account"


def _account():
    store = MemoryTenants()
    store.put(Tenant(tenant_id=_ACCOUNT, email="tests@example.com"))
    tenants.reset_store(store)


def request(path, method="GET", body=b"", cookies=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": {}, "headers": {},
        # The account cookie rides alongside whatever the test supplies.
        "cookies": list(cookies or []) + [f"pst={_ACCOUNT}"],
        "body": base64.b64encode(body).decode(), "isBase64Encoded": True,
    }
    return handler(event)


print("\n=== 1. Expiry is enforced on read, not left to TTL ===")
store = CopyingStore()
sessions.reset_store(store)
_account()
stale = Session(session_id="old", buyer_cookie="c", return_url="https://b/r")
stale.expires_at = time.time() - 1          # past, but the row still exists
store.put(stale)
check("an expired session reads as absent", store.get("old") is None,
      "DynamoDB TTL deletion is eventual — up to 48h late — so a session past "
      "its time but not yet swept must not be usable")

live = Session(session_id="live", buyer_cookie="c", return_url="https://b/r")
store.put(live)
check("a live session reads back", store.get("live") is not None)
check("default TTL is an hour",
      abs(live.expires_at - live.started_at - DEFAULT_TTL_SECONDS) < 1)

print("\n=== 2. Cart mutations survive a copying store ===")
store = CopyingStore()
sessions.reset_store(store)
_account()
store.put(Session(session_id="s1", buyer_name="Northgate",
                  buyer_cookie="COOKIE", return_url="https://buyer.example/r"))

request("/cart/add", "POST", b"sku=MSC-1001&quantity=20", ["pos=s1"])
after_one = store.get("s1")
check("first add persisted", after_one.cart == {"MSC-1001": 20},
      f"cart={after_one.cart}")

request("/cart/add", "POST", b"sku=MSC-3010&quantity=2", ["pos=s1"])
after_two = store.get("s1")
check("second add persisted alongside the first",
      after_two.cart == {"MSC-1001": 20, "MSC-3010": 2}, f"cart={after_two.cart}")

request("/cart/remove", "POST", b"sku=MSC-1001", ["pos=s1"])
after_remove = store.get("s1")
check("removal persisted", after_remove.cart == {"MSC-3010": 2},
      f"cart={after_remove.cart}")

print("\n=== 3. The cart renders from the STORE, not a stale local ===")
result = request("/cart", cookies=["pos=s1"])
page = base64.b64decode(result["body"]).decode() if result.get("isBase64Encoded") else result["body"]
check("cart page shows the persisted line", "MSC-3010" in page)
check("cart page does not show the removed line", "MSC-1001" not in page)

print("\n=== 4. Returning a cart clears it durably ===")
before = store.writes
result = request("/cart/return", "POST", b"mode=cart&encoding=cxml-base64", ["pos=s1"])
check("return succeeded", result["statusCode"] == 200)
check("cart is empty IN THE STORE afterwards", store.get("s1").cart == {},
      f"cart={store.get('s1').cart} — if this is non-empty, a back-button "
      "resubmit would double the buyer's requisition")
check("the clear was actually written", store.writes > before,
      f"writes {before} -> {store.writes}")

print("\n=== 5. Anonymous browsing needs no session write ===")
store = CopyingStore()
sessions.reset_store(store)
_account()
request("/cart/add", "POST", b"sku=MSC-1001&quantity=1")
check("no session row created for anonymous browsing", store.writes == 0,
      f"{store.writes} writes — anonymous carts have nowhere to return to, "
      "so persisting them buys nothing")

print("\n=== 6. An unknown or expired token degrades to anonymous ===")
result = request("/shop", cookies=["pos=does-not-exist"])
check("unknown token still serves the shop", result["statusCode"] == 200,
      "a user whose session timed out should see the shop, not a stack trace")
result = request("/cart/return", "POST", b"mode=cart", ["pos=does-not-exist"])
check("but returning a cart without a session is refused",
      result["statusCode"] == 409)

sessions.reset_store(None)
tenants.reset_store(None)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("session state survives DynamoDB's copy-on-read semantics")
