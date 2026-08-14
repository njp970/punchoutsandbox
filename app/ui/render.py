"""Template rendering.

Autoescaping is ON for every template, without exception. The storefront
renders supplier-controlled catalogue text and, on the inspector screens,
whole documents that arrived from an untrusted buyer — so an unescaped
template here is a stored XSS in a tool whose users are, by definition,
sending it hostile-shaped input on purpose.

Where raw markup genuinely has to reach the page (syntax-highlighted XML),
the highlighter escapes first and wraps second, and the result is marked safe
at that single point rather than by disabling autoescape for a template.
"""
from __future__ import annotations

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(template: str, **context) -> str:
    return env.get_template(template).render(**context)
