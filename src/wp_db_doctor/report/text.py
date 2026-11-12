from __future__ import annotations

import textwrap

from .._version import __version__
from ..models import Report
from .common import STATUS_LABEL, fixable_count, header_facts, summary_line

WIDTH = 100


def _table(columns: list[str], rows: list[list[object]], indent: str) -> list[str]:
    cells = [[str(c) for c in columns]] + [[str(v) for v in row] for row in rows]
    widths = [0] * len(columns)
    for row in cells:
        for i, value in enumerate(row[: len(columns)]):
            widths[i] = min(max(widths[i], len(value)), 48)

    def fmt(row: list[str]) -> str:
        parts = []
        for i, value in enumerate(row[: len(columns)]):
            if len(value) > widths[i]:
                value = value[: widths[i] - 1] + "~"
            parts.append(value.ljust(widths[i]))
        return (indent + "  ".join(parts)).rstrip()

    out = [fmt(cells[0]), indent + "  ".join("-" * w for w in widths)]
    out.extend(fmt(row) for row in cells[1:])
    return out


def render_text(report: Report) -> str:
    lines = [f"wp-db-doctor {__version__}"]
    facts = header_facts(report)
    label_width = max(len(k) for k, _ in facts)
    lines.extend(f"  {k.ljust(label_width)}  {v}" for k, v in facts)
    lines.append("")
    indent = " " * 9
    for result in report.results:
        tag = f"[{STATUS_LABEL[result.status]}]".ljust(8)
        lines.append(f"{tag} {result.title} ({result.check_id})")
        for chunk in textwrap.wrap(result.summary, WIDTH - len(indent)) or [""]:
            lines.append(indent + chunk)
        if result.rows and result.columns:
            lines.append("")
            lines.extend(_table(result.columns, result.rows, indent))
        for rec in result.recommendations:
            wrapped = textwrap.wrap(rec, WIDTH - len(indent) - 2)
            if wrapped:
                lines.append(indent + "> " + wrapped[0])
                lines.extend(indent + "  " + w for w in wrapped[1:])
        lines.append("")
    lines.append(f"Summary: {summary_line(report)}")
    fixable = fixable_count(report)
    if fixable:
        lines.append(f"{fixable} cleanup statement(s) available: run `wp-db-doctor fix` "
                     "to write the SQL plan for review.")
    return "\n".join(lines) + "\n"
