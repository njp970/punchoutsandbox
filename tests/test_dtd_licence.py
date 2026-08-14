"""The vendored DTDs, and the two conditions our right to ship them depends on.

=============================================================================
WHY THIS IS A TEST AND NOT A NOTE IN A README
=============================================================================
The cXML licence (Ariba, Inc. — `app/cxml/dtd/LICENSE-cXML.txt`) grants a
perpetual, royalty-free right to copy and distribute the Specification, on two
conditions:

  1. it must be the **unmodified** Specification;
  2. **the licence must be attached** when you distribute it.

Both are the sort of thing that stays true right up until somebody fixes a
typo in a DTD to make a test pass. A README saying "do not edit these" is a
request. This is a check.

The checksums below are the ones recorded in `app/cxml/dtd/README.md` as
retrieved from `https://xml.cxml.org/current/cXML_DTDs.zip` on 2026-08-14
(archive version 1.2.071). `scripts/fetch_dtds.sh` re-verifies against the
live upstream, which this deliberately does not — a test that reaches the
network fails when someone else's webserver is down, and the question here is
"has anything in this repository changed", not "has upstream changed".

If a DTD legitimately needs updating, run the fetch script, review the diff,
update the README's checksum block AND the table below, and re-run every
conformance suite — a DTD revision changes which documents we call valid, and
that is the product.
"""
import hashlib
import pathlib
import re
import sys

DTD_DIR = pathlib.Path("/Users/neilparkes/punchout/app/cxml/dtd")

#: As retrieved, 2026-08-14, cXML 1.2.071.
EXPECTED = {
    "Catalog.dtd": "113a82e3f7e86c9503a1d1a228735b7e7f453749e7490d71380b758c5ac30ba2",
    "Contract.dtd": "1a9cea79eff811e909f0c2d696505d5373d7795d5bae78ff22b1a5fb240ee8fb",
    "Fulfill.dtd": "1bdb5394eac2ea243dcb124e7cb149603a53848a75d89018ee06bdc6e699d21c",
    "InvoiceDetail.dtd": "4351e2b54d919e8b89c5e1dd6791347649334acec4ec3e0b31180b17163336c1",
    "Logistics.dtd": "e36913216f99e88ca731f7e65b613dde49830a10961ae5b5493cc3ecbafceafa",
    "PaymentRemittance.dtd": "4068ed75161e6bf3625062838c9aa96d7ecd93cbe68d2de36e7dd6dbbcf58050",
    "Private.dtd": "6dac214b0bfc45e88e4574897ec811fb103cf7ffdca3322073c84c107e41338a",
    "Quote.dtd": "01b084f04a9f6a0ff047c6feb111a08f768abb702c8648d3e40676783e951414",
    "cXML.dtd": "d267ad7b19cbd6608b972821daacab0f4d94a8ec78610b7127dba23222198a64",
}

failures: list[str] = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not condition:
        failures.append(name)


print("\n1. Condition one: the Specification is unmodified")
on_disk = {p.name for p in DTD_DIR.glob("*.dtd")}
check("the expected set of DTDs is present", on_disk == set(EXPECTED),
      f"unexpected: {sorted(on_disk - set(EXPECTED))}; "
      f"missing: {sorted(set(EXPECTED) - on_disk)}")

for name, expected in sorted(EXPECTED.items()):
    path = DTD_DIR / name
    if not path.exists():
        check(f"{name} present", False)
        continue
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    check(f"{name} is byte-identical to upstream", actual == expected,
          "" if actual == expected else
          f"expected {expected[:16]}…, got {actual[:16]}… — editing a vendored "
          "DTD forfeits the grant, which covers the UNMODIFIED Specification")

print("\n2. Condition two: the licence is attached")
licence = DTD_DIR / "LICENSE-cXML.txt"
check("LICENSE-cXML.txt sits alongside the DTDs", licence.exists())

if licence.exists():
    # Whitespace-normalised: the file is hard-wrapped at 78 columns, so every
    # phrase worth asserting on spans a line break. Searching the raw text
    # tests the line wrapping, not the content.
    text = " ".join(licence.read_text(encoding="utf-8").split())
    check("it carries the attachment requirement it exists to satisfy",
          "this License must be attached" in text)
    check("it names the licensor", "Ariba, Inc." in text)
    check("it records where and when it was retrieved",
          "https://www.cxml.org/license.html" in text and "2026-08-14" in text,
          "clause 1 makes our rights depend on the version in effect when we "
          "accessed it, so the date is not decoration")
    check("it grants what we actually rely on",
          "royalty-free" in text and "distribute" in text)
    check("it scopes itself to this directory",
          "THIS DIRECTORY ONLY" in text,
          "otherwise a reader assumes the whole repository is under it")
    check("it flags that the URL inside the DTDs is dead",
          "home/license.asp" in text and "404" in text,
          "that stale pointer is why a full copy is attached rather than a "
          "one-line reference")

print("\n3. The README agrees with the checksums we just verified")
readme = (DTD_DIR / "README.md").read_text(encoding="utf-8")
recorded = dict(re.findall(r"([0-9a-f]{64})\s+(\S+\.dtd)", readme))
recorded = {name: digest for digest, name in recorded.items()}
check("every DTD's checksum is recorded in the README",
      set(recorded) == set(EXPECTED),
      f"README lists {sorted(recorded)}")
check("...and matches this suite",
      all(recorded.get(n) == h for n, h in EXPECTED.items()),
      "two records of the same fact that disagree are worse than one")

print("\n4. The repository declares its own licence separately")
root = pathlib.Path("/Users/neilparkes/punchout")
check("/LICENSE exists", (root / "LICENSE").exists(),
      "a public repo with no licence grants nobody anything")
if (root / "LICENSE").exists():
    root_licence = " ".join((root / "LICENSE").read_text(encoding="utf-8").split())
    check("...covering this project's own code", "MIT License" in root_licence)
    # It SHOULD mention Ariba — in a section saying the MIT grant stops at the
    # DTDs. The failure mode being guarded against is a repository that
    # silently implies MIT covers vendored third-party material.
    check("...and says explicitly that it does not cover the DTDs",
          "app/cxml/dtd" in root_licence
          and "NOT under the MIT licence" in root_licence,
          "silence here reads as 'all of this is MIT', which would be a claim "
          "we have no right to make")

print("\n" + "=" * 70)
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("The vendored DTDs are unmodified and the licence is attached.")
