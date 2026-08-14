"""The indexable surface, and the Markdown renderer behind it.

Two things are being proved.

**That a crawler sees a page rather than a wall.** The site's front door used
to be a 303 into `/shop`, which is gated — so the most important request any
search engine makes landed on "Sign up to continue", and that is what the site
looked like to everyone who had not already found it. Nothing else about SEO
matters while that is true, so it is asserted first.

**That the Markdown renderer cannot emit markup from its source.** It renders
files from this repository, not user input, so this is defence in depth rather
than the last line — but a renderer whose escaping is only true by convention
is one edit away from not being true.
"""
import base64
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import markdown, reference, sessions, tenants
from app.handler import handler
from app.sessions import MemoryStore
from app.tenants import MemoryTenants

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


def get(path, query=None):
    event = {"requestContext": {"http": {"method": "GET", "path": path}},
             "queryStringParameters": query or {}, "headers": {},
             "cookies": [], "body": "", "isBase64Encoded": False}
    result = handler(event)
    raw = result["body"]
    body = (base64.b64decode(raw).decode() if result.get("isBase64Encoded")
            else raw)
    return result["statusCode"], body, result.get("headers", {})


sessions.reset_store(MemoryStore())
tenants.reset_store(MemoryTenants())

GATE = "Sign up to continue"

print("\n1. The front door is a page, not a redirect into the gate")
status, body, headers = get("/")
check("GET / is 200, not a redirect", status == 200, f"HTTP {status}")
check("...and is not the signup gate", GATE not in body)
check("...and says what the product is in an h1",
      "<h1>" in body and "punchout" in body.lower())
check("...and links to the reference pages", "/reference/" in body)
check("...and carries a canonical URL",
      '<link rel="canonical" href="https://punchoutsandbox.com/">' in body)
check("...and a description that is not the default",
      'name="description" content="A free hosted virtual supplier' in body,
      "otherwise Google writes the snippet from the nav menu")
check("...and structured data", '"@type": "SoftwareApplication"' in body)

print("\n2. robots.txt")
status, body, headers = get("/robots.txt")
check("is served as text/plain", status == 200
      and "text/plain" in headers.get("content-type", ""),
      f"HTTP {status} {headers.get('content-type')}")
check("...not as an HTML page", "<html" not in body.lower(),
      "it used to fall through to the gate and answer 200 with a signup form")
check("...pointing at the sitemap",
      "Sitemap: https://punchoutsandbox.com/sitemap.xml" in body)
for gated in ("/shop", "/cart", "/orders", "/settings"):
    check(f"...disallowing {gated}", f"Disallow: {gated}" in body,
          "a crawler that follows a gated path indexes the same signup form "
          "under a dozen URLs")

print("\n3. sitemap.xml lists only pages a crawler can read")
status, body, headers = get("/sitemap.xml")
check("is served as XML", status == 200 and "xml" in headers.get("content-type", ""),
      f"HTTP {status} {headers.get('content-type')}")
urls = re.findall(r"<loc>([^<]+)</loc>", body)
check("lists the landing page and every reference page",
      len(urls) >= 6 + len(reference.PAGES), f"{len(urls)} urls")
check("every URL is absolute",
      all(u.startswith("https://punchoutsandbox.com") for u in urls))

for url in urls:
    path = url.replace("https://punchoutsandbox.com", "") or "/"
    status, page, _ = get(path)
    check(f"{path} is reachable and open",
          status == 200 and (GATE not in page or path == "/signup"),
          f"HTTP {status} — a sitemap entry behind the gate asks Google to "
          "index the signup form")

print("\n4. Reference pages")
for page in reference.PAGES:
    status, body, _ = get(f"/reference/{page.slug}")
    check(f"/reference/{page.slug} renders", status == 200, f"HTTP {status}")
    check("...with the curated title as the h1", page.title in body)
    # Compare the UNESCAPED attribute against the source string. Building the
    # escaped form to compare against failed twice — first on an apostrophe,
    # then because html.escape writes &#x27; where Jinja writes &#39;. Decode
    # the output rather than trying to predict it.
    import html as _html
    found = re.search(r'name="description" content="([^"]*)"', body)
    check("...with its own meta description",
          bool(found) and _html.unescape(found.group(1)) == page.description,
          (_html.unescape(found.group(1))[:60] if found else "absent"))
    check("...and real content, not an empty shell", len(body) > 8000,
          f"{len(body)} bytes")
    check("...linking to the validator", "/validate" in body)

status, _, _ = get("/reference/no-such-page")
check("an unknown reference slug is 404", status == 404, f"HTTP {status}")

print("\n5. The Markdown renderer cannot emit markup from its source")
HOSTILE = """# Title

<script>alert(1)</script>

Some **bold** and an <img src=x onerror=alert(2)> tag.

| a | b |
|---|---|
| <b>x</b> | `y` |

[click](javascript:alert(3)) and [ok](https://example.com/)
"""
out = markdown.render(HOSTILE)
check("a script tag is escaped", "<script>" not in out, out[:120])
check("an img tag is escaped", "<img" not in out)
check("markup inside a table cell is escaped", "<b>x</b>" not in out)
check("a javascript: link does not become a link",
      'href="javascript:' not in out)
check("...but is still shown to the reader", "javascript:alert(3)" in out)
check("an https link does become one",
      '<a href="https://example.com/"' in out and 'rel="noopener' in out)
check("bold still works", "<strong>bold</strong>" in out)
check("the table still renders", "<table>" in out and "<td>" in out)

print("\n6. Inline formatting runs in the right order")
out = markdown.render("Use `a ** b` not **a ** b**.")
check("asterisks inside a code span stay literal",
      "<code>a ** b</code>" in out, out)
out = markdown.render("A `<div>` in code.")
check("markup inside a code span is escaped",
      "<code>&lt;div&gt;</code>" in out, out)
out = markdown.render("## A Heading Here")
check("headings get a stable id for deep linking",
      'id="a-heading-here"' in out, out)
check("...and start at h3 so the page h1 stays unique",
      "<h3" in out, out)

print("\n7. Nothing cookie-dependent is cacheable at the edge")
# Cloudflare cached the signup gate under /robots.txt for four hours because
# `.txt` is on its default-cacheable list, and it kept serving that to
# crawlers after the real file shipped. Everything now opts out by default.
for path in ("/", "/docs", "/validate", "/shop", "/reference"):
    _, _, headers = get(path)
    check(f"{path} is no-store",
          "no-store" in headers.get("cache-control", ""),
          headers.get("cache-control", "absent") + " — this page varies by "
          "cookie, so a shared cache would serve one visitor's chrome to "
          "another")
for path, why in (("/robots.txt", "identical for everyone"),
                  ("/sitemap.xml", "identical for everyone"),
                  ("/static/app.css", "a static asset")):
    _, _, headers = get(path)
    check(f"{path} opts back in to caching",
          "public" in headers.get("cache-control", ""),
          f"{headers.get('cache-control', 'absent')} — {why}")

_, _, headers = get("/static/favicon.svg")
check("an SVG is served as image/svg+xml",
      headers.get("content-type") == "image/svg+xml",
      f"{headers.get('content-type')} — octet-stream downloads it instead of "
      "rendering it, so the favicon never appears")


print("\n8. Every published page has a description and a canonical")
for path in ("/", "/docs", "/validate", "/contact", "/reference"):
    _, body, _ = get(path)
    description = re.search(r'name="description" content="([^"]*)"', body)
    check(f"{path} has a description", bool(description) and len(description.group(1)) > 50,
          (description.group(1)[:60] if description else "absent"))
    check(f"{path} has a canonical", f'rel="canonical"' in body)
    check(f"{path} has an og:title", 'property="og:title"' in body)

print("\n9. The CSP's central claim is actually true")
# `script-src 'self'` blocks inline event handlers exactly as it blocks a
# <script> block. The first version of the policy said 'none' on the claim
# that no inline script existed — while two templates carried onclick
# handlers, which then failed silently in every browser. A policy is only as
# good as the claim underneath it, so the claim is now a test.
TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "ui" / "templates"
offenders = []
for template in TEMPLATES.glob("*.html"):
    text = template.read_text()
    for handler_attr in ("onclick=", "onsubmit=", "onchange=", "onload=",
                         "onerror=", "onfocus=", "onmouseover="):
        if handler_attr in text:
            offenders.append(f"{template.name}:{handler_attr}")
    for block in re.findall(r"<script([^>]*)>", text):
        if "src=" not in block and "ld+json" not in block:
            offenders.append(f"{template.name}: inline <script>")
check("no template carries an inline event handler or script block",
      not offenders, "; ".join(offenders) + " — these are silently dead under "
      "script-src 'self'")

_, body, _ = get("/")
check("the one script file is loaded from our own origin",
      'src="/static/app.js"' in body)
_, _, headers = get("/static/app.js")
check("...and served as JavaScript",
      "javascript" in headers.get("content-type", ""),
      headers.get("content-type", "absent"))
csp = get("/")[2].get("content-security-policy", "")
check("the policy permits it and nothing else",
      "script-src 'self'" in csp, csp[:80])

print("\n10. 'Try a sample' actually loads the sample")
# It used to be a submit button named "document" carrying the sample as its
# value — but the textarea is also named "document" and comes first in
# document order, so the browser sent both and Request.form(), which takes
# the first, kept the empty one. The button had never worked once.
event = {"requestContext": {"http": {"method": "POST", "path": "/validate"}},
         "queryStringParameters": {}, "headers": {}, "cookies": [],
         "body": base64.b64encode(b"document=&sample=1").decode(),
         "isBase64Encoded": True}
result = handler(event)
page = (base64.b64decode(result["body"]).decode()
        if result.get("isBase64Encoded") else result["body"])
check("the sample is loaded, not the empty textarea",
      "PunchOutSetupRequest" in page and "Nothing to validate" not in page,
      "the browser posts BOTH fields; the first one wins")


print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("The indexable surface is a site, not a signup form.")
