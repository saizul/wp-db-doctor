"""Report renderers: text, JSON, Markdown and HTML."""

from __future__ import annotations

from ..models import Report
from .html import render_html
from .json_out import render_json
from .markdown import render_markdown
from .text import render_text

FORMATS = ("text", "json", "markdown", "html")

_RENDERERS = {
    "text": render_text,
    "json": render_json,
    "markdown": render_markdown,
    "html": render_html,
}


def render(report: Report, fmt: str) -> str:
    try:
        renderer = _RENDERERS[fmt]
    except KeyError:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}") from None
    return renderer(report)


def format_for_path(path: str) -> str | None:
    """Guess the format from an output file extension."""
    low = path.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".markdown")):
        return "markdown"
    if low.endswith((".html", ".htm")):
        return "html"
    if low.endswith(".txt"):
        return "text"
    return None


__all__ = ["FORMATS", "format_for_path", "render"]
