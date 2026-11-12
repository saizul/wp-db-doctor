"""Helpers shared by the renderers."""

from __future__ import annotations

from ..models import Report, Status
from ..utils import format_bytes

STATUS_LABEL = {
    Status.OK: "OK",
    Status.SKIPPED: "SKIP",
    Status.INFO: "INFO",
    Status.WARNING: "WARN",
    Status.ERROR: "ERROR",
    Status.CRITICAL: "CRIT",
}


def header_facts(report: Report) -> list[tuple[str, str]]:
    db = report.database
    facts = [
        ("Database", db.name),
        ("Host", db.host),
        ("Prefix", db.prefix),
    ]
    if db.server_version:
        facts.append(("Server", db.server_version))
    facts.append(("Tables", str(db.table_count)))
    facts.append(("Size", format_bytes(db.total_size)))
    facts.append(("Generated", report.generated_at))
    return facts


def summary_line(report: Report) -> str:
    counts = report.counts()
    order = ("critical", "error", "warning", "info", "ok", "skipped")
    return ", ".join(f"{counts[k]} {k}" for k in order if counts.get(k))


def fixable_count(report: Report) -> int:
    return sum(1 for f in report.fixes() if not f.review_only)
