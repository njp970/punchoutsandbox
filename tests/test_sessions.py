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


def request(path, method="GET", body=b"", cookies=None, query=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": query or {}, "headers": {},
        # The account cookie rides alongside whatever the test supplies.
        "cookies": list(cookies or []) + [f"pst={_ACCOUNT}"],
        "body": base64.b64encode(body).decode(), "isBase64Encoded": True,
    }
    return handler(event)


def _body(result) -> str:
    raw = result["body"]
    return (base64.b64decode(raw).decode() if result.get("isBase64Encoded")
            else raw)


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

print("\n=== 5. Anonymous carts are per-browser, not per-container ===")
# THIS TEST REVERSED. It used to assert that anonymous browsing wrote NO
# session row, on the reasoning that a cart with nowhere to return to is not
# worth storing. That reasoning was right about storage and wrong about
# correctness: the cart then lived in a module-level dict, which in Lambda is
# shared by every request a warm container handles — so two strangers browsing
# at once saw each other's carts, and which stranger you got depended on which
# container answered.
#
# The write is the price of not leaking one person's shopping to another.
store = CopyingStore()
sessions.reset_store(store)
_account()

browse = request("/shop")
check("merely looking writes nothing", store.writes == 0,
      f"{store.writes} writes — a reader of /shop should mint no rows")

before = store.writes
first = request("/cart/add", "POST", b"sku=MSC-1001&quantity=1")
check("adding to an anonymous cart does write", store.writes > before,
      f"{store.writes - before} writes")

cookie = ""
for header in first.get("cookies", []):
    if header.startswith("pab="):
        cookie = header.split(";")[0]
check("...and hands the browser a cart cookie", bool(cookie), cookie or "none")
check("...marked Secure and HttpOnly",
      "Secure" in " ".join(first.get("cookies", []))
      and "HttpOnly" in " ".join(first.get("cookies", [])))

# The regression that matters: a SECOND browser, with no cookie, must not see
# the first one's cart.
second = request("/cart")
body = _body(second)
check("a different browser sees an empty cart", "MSC-1001" not in body,
      "if this fails, one visitor's cart is visible to another")

# ...while the first browser still has its own.
mine = _body(request("/cart", cookies=[cookie]))
check("the original browser still has its cart", "MSC-1001" in mine)

print("\n=== 5b. A punchout session survives navigation ===")
# The StartPage URL carries ?session=…; no link on the storefront does. Without
# a cookie being issued on first sight, the session lasted exactly one page
# view: the banner vanished on the first click and /cart/return answered 409.
sessions.reset_store(CopyingStore())
_account()
live = Session(session_id="sess-nav", buyer_name="Northgate",
               buyer_cookie="bc-1",
               return_url="https://buyer.example.com/return")
sessions.store().put(live)
landing = request("/shop", query={"session": "sess-nav"})
issued = [c for c in landing.get("cookies", []) if c.startswith("pos=")]
check("landing on the StartPage URL issues a session cookie", bool(issued),
      "; ".join(landing.get("cookies", [])) or "no cookies set")
if issued:
    token = issued[0].split(";")[0]
    onward = _body(request("/shop", cookies=[token]))
    check("...so the next click is still inside the punchout",
          "Punchout session active" in onward,
          "the banner is how a user knows whose transaction they are in")
    ret = request("/cart/return", "POST", b"mode=empty", [token])
    check("...and the cart can still be returned", ret["statusCode"] == 200,
          f"HTTP {ret['statusCode']} — 409 here is the bug this guards")

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
