"""Document validation — paste a cXML document, get a verdict.

*This is the product's front door. Everything else in the sandbox exists so
that this screen has something to judge, and it is also the only page that
proves `lxml` and the vendored DTDs actually loaded in the deployed
environment — a fact no other route exercises.*

The report deliberately shows THREE things and keeps them apart, because
conflating them is how conformance tools become untrustworthy:

  errors      the DTD says the document is wrong. Not a matter of opinion.
  advisories  the document is valid and will still cause trouble. Judgement,
              and labelled as such.
  observed    facts we noticed and are not judging at all (declared version,
              which DTD we used, document type).

`validation.py`'s docstring has the long argument for that split. The short
version is that the value of this tool rests entirely on its errors being
trustworthy, and the moment opinions get reported as errors they are not.
"""
from __future__ import annotations

import html as _html

from .http import Request, Response, html
from .signup import current_tenant
from .ui.render import render
from .validation import validate
from .xml_safe import XmlRejected, parse

#: Generous, and much smaller than xml_safe's 4MB parse ceiling. A human
#: pasting into a textarea is not sending a 4MB document, and the browser
#: would struggle to render the highlighted result if they did.
MAX_PASTE_BYTES = 512 * 1024

_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.071/cXML.dtd">
<cXML payloadID="1234@buyer.example.com"
      timestamp="2026-08-14T10:00:00+01:00">
 <Header>
  <From><Credential domain="NetworkID">
    <Identity>buyer</Identity></Credential></From>
  <To><Credential domain="NetworkID">
    <Identity>supplier</Identity></Credential></To>
  <Sender><Credential domain="NetworkID">
    <Identity>buyer</Identity>
    <SharedSecret>secret</SharedSecret></Credential>
   <UserAgent>Example Procurement 1.0</UserAgent></Sender>
 </Header>
 <Request deploymentMode="test">
  <PunchOutSetupRequest operation="create">
   <BuyerCookie>abc123</BuyerCookie>
   <BrowserFormPost>
     <URL>https://buyer.example.com/punchout/return</URL>
   </BrowserFormPost>
  </PunchOutSetupRequest>
 </Request>
</cXML>"""


def _numbered(document: str, error_lines: set[int]) -> list[dict]:
    """Split into numbered lines, marking the ones an error points at.

    Escaped here rather than relying on the template, because the result is
    assembled into a structure the template renders per line — and the input
    is, by design, whatever hostile thing someone chose to paste."""
    out = []
    for index, text in enumerate(document.splitlines(), start=1):
        out.append({
            "n": index,
            "text": _html.escape(text) or "&nbsp;",
            "bad": index in error_lines,
        })
    return out


def view_validate(request: Request) -> Response:
    if request.method == "GET":
        return html(render("validate.html", nav="validate", sample=_SAMPLE,
                           document="", report=None, lines=None, rejected=None,
                           signed_in=current_tenant(request) is not None))

    document = request.form().get("document", "").strip()
    if not document:
        return html(render("validate.html", nav="validate", sample=_SAMPLE,
                           document="", report=None, lines=None,
                           signed_in=current_tenant(request) is not None,
                           rejected="Nothing to validate — paste a document first."))

    raw = document.encode("utf-8")
    if len(raw) > MAX_PASTE_BYTES:
        return html(render(
            "validate.html", nav="validate", sample=_SAMPLE, document="",
            report=None, lines=None,
            signed_in=current_tenant(request) is not None,
            rejected=(f"That document is {len(raw):,} bytes; this page accepts "
                      f"up to {MAX_PASTE_BYTES:,}.")))

    try:
        doc = parse(raw)
    except XmlRejected as exc:
        # Refused at the front door — hostile or malformed. Deliberately
        # distinct from a validation failure: this document was never
        # processed, whereas a non-conformant one was parsed and then judged.
        return html(render(
            "validate.html", nav="validate", sample=_SAMPLE, document=document,
            report=None, lines=None, rejected=str(exc),
            signed_in=current_tenant(request) is not None))

    report = validate(doc)
    error_lines = {f.line for f in report.errors if f.line}
    return html(render(
        "validate.html", nav="validate", sample=_SAMPLE, document=document,
        report=report, lines=_numbered(document, error_lines), rejected=None,
        signed_in=current_tenant(request) is not None))
