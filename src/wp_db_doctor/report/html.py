"""Self-contained HTML report: inline CSS, no JavaScript, no external requests."""

from __future__ import annotations

from html import escape

from .._version import __version__
from ..models import Report, Status
from .common import STATUS_LABEL, fixable_count, header_facts

CSS = """
:root{--bg:#f7f7f8;--fg:#1d1f23;--muted:#5c6370;--card:#fff;--line:#e3e5e8;
--ok:#1f8a4c;--info:#2463b5;--warning:#b06a00;--critical:#c0262d;--skipped:#7a7f87}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e7e9;--muted:#9aa0a8;
--card:#1e2126;--line:#30343b;--ok:#47c27a;--info:#6ea4ef;--warning:#e7a33e;
--critical:#f0656b;--skipped:#8d939b}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--fg)}
main{max-width:1100px;margin:0 auto;padding:32px 20px 60px}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:0}
.muted{color:var(--muted)}
.facts{display:flex;flex-wrap:wrap;gap:8px 24px;margin:16px 0 24px;padding:0;list-style:none}
.facts b{font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;
margin-bottom:28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .n{font-size:28px;font-weight:700}
.check{background:var(--card);border:1px solid var(--line);border-left-width:5px;
border-radius:10px;padding:16px 18px;margin:0 0 14px}
.check header{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.badge{font-size:12px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:99px;
color:#fff}
.s-ok{border-left-color:var(--ok)} .b-ok{background:var(--ok)}
.s-info{border-left-color:var(--info)} .b-info{background:var(--info)}
.s-warning,.s-error{border-left-color:var(--warning)} .b-warning,.b-error{background:var(--warning)}
.s-critical{border-left-color:var(--critical)} .b-critical{background:var(--critical)}
.s-skipped{border-left-color:var(--skipped)} .b-skipped{background:var(--skipped)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
td{word-break:break-word}
ul{margin:10px 0 0;padding-left:20px}
details{margin-top:10px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;
overflow:auto;font-size:12.5px}
footer{margin-top:32px;font-size:13px}
"""


def render_html(report: Report) -> str:
    db = report.database
    counts = report.counts()
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>wp-db-doctor: {escape(db.name)}</title>",
        f"<style>{CSS}</style></head><body><main>",
        f"<h1>WordPress database report: {escape(db.name)}</h1>",
        '<ul class="facts">',
    ]
    for key, value in header_facts(report):
        parts.append(f"<li><b>{escape(key)}</b> {escape(value)}</li>")
    parts.append("</ul>")
    parts.append('<section class="cards">')
    for status in (Status.CRITICAL, Status.WARNING, Status.INFO, Status.OK, Status.SKIPPED):
        n = counts[status.value] + (counts["error"] if status is Status.WARNING else 0)
        parts.append(f'<div class="card s-{status.value}" style="border-left:5px solid '
                     f'var(--{status.value})"><div class="n">{n}</div>'
                     f'<div class="muted">{escape(status.value)}</div></div>')
    parts.append("</section>")
    for r in report.results:
        cls = r.status.value
        parts.append(f'<section class="check s-{cls}" id="{escape(r.check_id)}">')
        parts.append(f'<header><span class="badge b-{cls}">{STATUS_LABEL[r.status]}</span>'
                     f"<h2>{escape(r.title)}</h2>"
                     f'<code class="muted">{escape(r.check_id)}</code></header>')
        parts.append(f"<p>{escape(r.summary)}</p>")
        if r.rows and r.columns:
            parts.append("<table><thead><tr>")
            parts.extend(f"<th>{escape(str(c))}</th>" for c in r.columns)
            parts.append("</tr></thead><tbody>")
            for row in r.rows:
                parts.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row)
                             + "</tr>")
            parts.append("</tbody></table>")
        if r.recommendations:
            parts.append("<ul>")
            parts.extend(f"<li>{escape(rec)}</li>" for rec in r.recommendations)
            parts.append("</ul>")
        if r.fixes:
            parts.append(f"<details><summary>Proposed SQL ({len(r.fixes)})</summary><pre>")
            for fix in r.fixes:
                prefix = "-- REVIEW: " if fix.review_only else "-- "
                body = f"-- {fix.sql};" if fix.review_only else f"{fix.sql};"
                parts.append(escape(f"{prefix}{fix.description}\n{body}\n\n"))
            parts.append("</pre></details>")
        parts.append("</section>")
    fixable = fixable_count(report)
    parts.append('<footer class="muted">')
    if fixable:
        parts.append(f"<p>{fixable} cleanup statement(s) available. Write them to a file "
                     "for review with <code>wp-db-doctor fix -o fix.sql</code>.</p>")
    parts.append(f"<p>wp-db-doctor {escape(__version__)}</p></footer>")
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
