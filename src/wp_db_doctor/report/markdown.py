from __future__ import annotations

from .._version import __version__
from ..models import Report
from .common import STATUS_LABEL, fixable_count, header_facts, summary_line


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Report) -> str:
    out = [f"# WordPress database report: {_cell(report.database.name)}", ""]
    out.append("| | |")
    out.append("|---|---|")
    out.extend(f"| {k} | {_cell(v)} |" for k, v in header_facts(report))
    out.append("")
    out.append(f"**Summary:** {summary_line(report)}")
    out.append("")
    out.append("| Status | Check | Result |")
    out.append("|---|---|---|")
    for r in report.results:
        out.append(f"| {STATUS_LABEL[r.status]} | [{_cell(r.title)}](#{r.check_id}) | "
                   f"{_cell(r.summary)} |")
    out.append("")
    for r in report.results:
        out.append(f'<a id="{r.check_id}"></a>')
        out.append(f"## {STATUS_LABEL[r.status]}: {_cell(r.title)}")
        out.append("")
        out.append(f"`{r.check_id}` - {_cell(r.summary)}")
        out.append("")
        if r.rows and r.columns:
            out.append("| " + " | ".join(_cell(c) for c in r.columns) + " |")
            out.append("|" + "---|" * len(r.columns))
            for row in r.rows:
                out.append("| " + " | ".join(_cell(v) for v in row) + " |")
            out.append("")
        for rec in r.recommendations:
            out.append(f"- {_cell(rec)}")
        if r.recommendations:
            out.append("")
        fixes = [f for f in r.fixes if not f.review_only]
        if fixes:
            out.append(f"Fix plan: {len(fixes)} statement(s), see `wp-db-doctor fix --only "
                       f"{r.check_id}`.")
            out.append("")
    if fixable_count(report):
        out.append("---")
        out.append("")
        out.append("Generate the reviewed cleanup SQL with `wp-db-doctor fix -o fix.sql`.")
        out.append("")
    out.append(f"_wp-db-doctor {__version__}_")
    return "\n".join(out) + "\n"
