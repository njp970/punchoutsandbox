"""A small Markdown renderer for the reference pages.

*Deliberately not a Markdown library. This renders **our own files**, checked
into this repository, using a subset we control — headings, tables, lists,
blockquotes, fenced code, rules, and inline bold/italic/code/links. Nothing
else appears in `docs/reference/`, and a survey of those files is what decided
the feature list.*

=============================================================================
WHY NOT JUST ADD `markdown` OR `mistune`
=============================================================================
Both are perfectly good and either would work. Two reasons not to:

1. **Escaping.** A general library's job is to let authors write raw HTML, so
   its safe mode is a bolt-on and its default is to pass HTML through. Here the
   requirement is the opposite: nothing in the source may ever become live
   markup. `_escape` runs FIRST on every line and the only tags in the output
   are ones this module emits itself, which is a much easier property to check
   than "did we configure the library's sanitiser correctly".

2. It is one runtime dependency in a Lambda bundle that currently has three,
   for a subset that fits in a page.

The trade is real and worth naming: an unsupported construct renders as
literal text rather than failing loudly. `tests/test_markdown.py` covers what
the reference files actually use; a new construct needs a test and a branch.

=============================================================================
INLINE FORMATTING RUNS ON ESCAPED TEXT, AND CODE SPANS ARE PROTECTED FIRST
=============================================================================
The order below is the whole correctness argument:

    escape  ->  pull code spans out  ->  bold/italic/links  ->  put them back

Pulling code spans out before the emphasis pass is what stops `**` inside
`` `a ** b` `` becoming a tag, and putting them back last is what stops the
emphasis pass seeing the placeholder markers. Getting this order wrong is the
classic Markdown bug and it is invisible until someone writes about pointers.
"""
from __future__ import annotations

import re

_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED = re.compile(r"^(\d+)\.\s+(.*)$")

#: Placeholder for an extracted code span. Uses characters that cannot survive
#: `_escape`, so no source text can forge one.
_MARK = "\x00CODE{}\x00"


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _inline(text: str) -> str:
    """Escape, then apply inline formatting. See the module docstring on why
    the order is not negotiable."""
    text = _escape(text)

    spans: list[str] = []

    def stash(match: re.Match) -> str:
        spans.append(match.group(1))
        return _MARK.format(len(spans) - 1)

    text = _CODE_SPAN.sub(stash, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)

    def link(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        # Only http(s), mailto and site-relative targets become links. A
        # `javascript:` href in one of our own files would be a mistake rather
        # than an attack, and it still must not become a live link.
        if not re.match(r"^(https?://|mailto:|/|#)", href):
            return f"{label} ({href})"
        external = href.startswith("http")
        extra = ' target="_blank" rel="noopener noreferrer"' if external else ""
        return f'<a href="{href}"{extra}>{label}</a>'

    text = _LINK.sub(link, text)

    for index, code in enumerate(spans):
        text = text.replace(_MARK.format(index), f"<code>{code}</code>")
    return text


def _table(rows: list[str]) -> str:
    """Render a pipe table. The separator row is what identifies one, and it
    is consumed rather than rendered."""
    cells = [[c.strip() for c in row.strip().strip("|").split("|")]
             for row in rows]
    header, body = cells[0], cells[2:]      # cells[1] is the --- separator
    out = ['<div class="table-wrap"><table><thead><tr>']
    out += [f"<th>{_inline(c)}</th>" for c in header]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render(source: str) -> str:
    """Markdown subset to HTML. Every character of `source` is escaped."""
    lines = source.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            flush()
            index += 1
            continue

        if stripped.startswith("```"):
            flush()
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            out.append("<pre><code>" + _escape("\n".join(block)) + "</code></pre>")
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = min(len(heading.group(1)) + 1, 6)   # h1 belongs to the page
            text = _inline(heading.group(2))
            # A stable id per heading, so a reference page can be linked to a
            # specific rule — which is how someone arrives from a search
            # result for one error message.
            slug = re.sub(r"[^a-z0-9]+", "-",
                          re.sub(r"<[^>]+>", "", text).lower()).strip("-")
            out.append(f'<h{level} id="{slug}">{text}</h{level}>')
            index += 1
            continue

        if set(stripped) <= {"-"} and len(stripped) >= 3:
            flush()
            out.append("<hr>")
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) \
                and set(lines[index + 1].strip()) <= set("|-: "):
            flush()
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index])
                index += 1
            out.append(_table(rows))
            continue

        if stripped.startswith("> "):
            flush()
            quote = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append("<blockquote><p>" + _inline(" ".join(quote)) + "</p></blockquote>")
            continue

        if stripped.startswith(("- ", "* ")) or _ORDERED.match(stripped):
            flush()
            ordered = bool(_ORDERED.match(stripped))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                current = lines[index].strip()
                match = _ORDERED.match(current)
                if current.startswith(("- ", "* ")):
                    items.append(current[2:])
                elif match:
                    items.append(match.group(2))
                elif current and lines[index].startswith(("  ", "\t")) and items:
                    # A continuation line belongs to the item above it.
                    items[-1] += " " + current
                else:
                    break
                index += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items)
                       + f"</{tag}>")
            continue

        paragraph.append(stripped)
        index += 1

    flush()
    return "\n".join(out)


def first_heading(source: str) -> str:
    """The document's `# Title`, for the page title and the index."""
    for line in source.split("\n"):
        match = _HEADING.match(line.strip())
        if match and len(match.group(1)) == 1:
            return re.sub(r"[*`]", "", match.group(2)).strip()
    return ""


def summary(source: str, limit: int = 155) -> str:
    """First real paragraph, flattened — the meta description.

    Skips the title, any italic dek immediately under it, and anything inside
    a fence, because a description that opens mid-code-block is worse than
    none."""
    for block in re.split(r"\n\s*\n", source):
        block = block.strip()
        if (not block or block.startswith(("#", "|", ">", "```", "-", "*"))
                or block.startswith("<")):
            continue
        text = re.sub(r"\s+", " ", re.sub(r"[*`\[\]]|\(https?://[^)]*\)", "", block))
        text = text.strip()
        if len(text) < 40:
            continue
        if len(text) <= limit:
            return text
        cut = text[:limit].rsplit(" ", 1)[0]
        return cut + "…"
    return ""
