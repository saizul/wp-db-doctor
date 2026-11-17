"""Compare two JSON audit reports to track a database over time.

The JSON report (``audit -o report.json``) is the input format, so a baseline
can be kept from a previous run, an earlier CI job or another environment. The
comparison is pure data work: no database connection is needed.

A *regression* is a check whose status got more severe (by rank, so a change
between ``warning`` and ``error`` is reported but not counted), or a check that
appears in the current report with a finding that the baseline did not have.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Status
from .utils import format_bytes, format_int

SUPPORTED_SCHEMA_VERSIONS = (1,)

#: Comparison formats understood by :func:`render_comparison`.
COMPARE_FORMATS = ("text", "json", "markdown")

CHANGE_WORSE = "worse"
CHANGE_BETTER = "better"
CHANGE_CHANGED = "changed"
CHANGE_SAME = "same"
CHANGE_NEW = "new"
CHANGE_REMOVED = "removed"
ALL_CHANGES = (CHANGE_WORSE, CHANGE_BETTER, CHANGE_CHANGED, CHANGE_SAME, CHANGE_NEW,
               CHANGE_REMOVED)


class ReportLoadError(ValueError):
    """A file could not be read as a wp-db-doctor JSON report."""


@dataclass
class MetricDelta:
    name: str
    old: float
    new: float

    @property
    def delta(self) -> float:
        return self.new - self.old

    @property
    def percent(self) -> float | None:
        if self.old == 0:
            return None
        return self.delta / abs(self.old) * 100.0

    @property
    def is_bytes(self) -> bool:
        return self.name.endswith("bytes") or self.name == "bytes"

    def to_dict(self) -> dict[str, Any]:
        pct = self.percent
        return {
            "metric": self.name,
            "old": _plain(self.old),
            "new": _plain(self.new),
            "delta": _plain(self.delta),
            "percent": None if pct is None else round(pct, 1),
        }


@dataclass
class CheckChange:
    check_id: str
    title: str
    old_status: Status | None
    new_status: Status | None
    change: str
    summary: str = ""
    metrics: list[MetricDelta] = field(default_factory=list)

    @property
    def is_regression(self) -> bool:
        return self.change == CHANGE_WORSE or (
            self.change == CHANGE_NEW
            and self.new_status is not None
            and self.new_status.rank > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "title": self.title,
            "old_status": self.old_status.value if self.old_status else None,
            "new_status": self.new_status.value if self.new_status else None,
            "change": self.change,
            "regression": self.is_regression,
            "summary": self.summary,
            "metrics": [m.to_dict() for m in self.metrics],
        }


@dataclass
class Comparison:
    baseline_label: str
    current_label: str
    baseline_generated_at: str
    current_generated_at: str
    baseline_database: str
    current_database: str
    old_size: int
    new_size: int
    checks: list[CheckChange]
    warnings: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[CheckChange]:
        return [c for c in self.checks if c.is_regression]

    @property
    def improvements(self) -> list[CheckChange]:
        return [c for c in self.checks if c.change == CHANGE_BETTER]

    @property
    def size_delta(self) -> int:
        return self.new_size - self.old_size

    @property
    def size_growth_percent(self) -> float | None:
        if self.old_size <= 0:
            return None
        return self.size_delta / self.old_size * 100.0

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(ALL_CHANGES, 0)
        for check in self.checks:
            counts[check.change] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        growth = self.size_growth_percent
        return {
            "schema_version": 1,
            "baseline": {
                "file": self.baseline_label,
                "generated_at": self.baseline_generated_at,
                "database": self.baseline_database,
                "total_size_bytes": self.old_size,
            },
            "current": {
                "file": self.current_label,
                "generated_at": self.current_generated_at,
                "database": self.current_database,
                "total_size_bytes": self.new_size,
            },
            "size_delta_bytes": self.size_delta,
            "size_growth_percent": None if growth is None else round(growth, 1),
            "summary": self.counts(),
            "regressions": [c.check_id for c in self.regressions],
            "warnings": self.warnings,
            "checks": [c.to_dict() for c in self.checks],
        }


def _plain(value: float) -> int | float:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def load_report(path: str | Path) -> dict[str, Any]:
    """Read and sanity-check a JSON report written by ``audit``."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportLoadError(f"cannot read {p}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportLoadError(f"{p} is not valid JSON: {exc.msg} (line {exc.lineno})") from exc
    return validate_report(data, label=str(p))


def validate_report(data: Any, label: str = "report") -> dict[str, Any]:
    """Check that ``data`` looks like a wp-db-doctor JSON report and return it."""
    if not isinstance(data, dict):
        raise ReportLoadError(f"{label} is not a wp-db-doctor JSON report (expected an object)")
    tool = data.get("tool")
    if not isinstance(tool, dict) or tool.get("name") != "wp-db-doctor":
        raise ReportLoadError(f"{label} is not a wp-db-doctor JSON report (no tool name)")
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ReportLoadError(
            f"{label} has report schema version {version!r}; this version of wp-db-doctor "
            f"reads {', '.join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)}"
        )
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise ReportLoadError(f"{label} has no list of checks")
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            raise ReportLoadError(f"{label}: check #{index + 1} has no id")
        try:
            Status.parse(str(check.get("status", "")))
        except ValueError as exc:
            raise ReportLoadError(f"{label}: check {check['id']!r}: {exc}") from None
    return data


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return float(value)


def metric_deltas(old: dict[str, Any], new: dict[str, Any]) -> list[MetricDelta]:
    """Numeric top-level metrics present in both dicts whose value changed."""
    deltas = []
    for name, new_value in new.items():
        if name not in old:
            continue
        a, b = _number(old[name]), _number(new_value)
        if a is None or b is None or a == b:
            continue
        deltas.append(MetricDelta(name=name, old=a, new=b))
    return deltas


def _classify(old: Status | None, new: Status | None) -> str:
    if old is None:
        return CHANGE_NEW
    if new is None:
        return CHANGE_REMOVED
    if new.rank > old.rank:
        return CHANGE_WORSE
    if new.rank < old.rank:
        return CHANGE_BETTER
    if new != old:
        return CHANGE_CHANGED
    return CHANGE_SAME


def _db(data: dict[str, Any]) -> dict[str, Any]:
    db = data.get("database")
    return db if isinstance(db, dict) else {}


def compare_reports(baseline: dict[str, Any], current: dict[str, Any], *,
                    baseline_label: str = "baseline",
                    current_label: str = "current") -> Comparison:
    """Compare two validated report dicts (see :func:`load_report`)."""
    old_db, new_db = _db(baseline), _db(current)
    warnings = []
    for key, what in (("name", "database"), ("prefix", "table prefix")):
        a, b = old_db.get(key), new_db.get(key)
        if a and b and a != b:
            warnings.append(f"the reports are for different {what}s ({a} and {b})")
    if str(baseline.get("generated_at", "")) > str(current.get("generated_at", "")):
        warnings.append("the baseline report is newer than the current one; "
                        "are the arguments in the right order?")

    old_checks = {c["id"]: c for c in baseline["checks"]}
    new_checks = {c["id"]: c for c in current["checks"]}
    order = list(new_checks) + [cid for cid in old_checks if cid not in new_checks]
    changes = []
    for cid in order:
        old, new = old_checks.get(cid), new_checks.get(cid)
        old_status = Status.parse(old["status"]) if old else None
        new_status = Status.parse(new["status"]) if new else None
        source = new or old or {}
        old_metrics = old.get("metrics") if old else None
        new_metrics = new.get("metrics") if new else None
        deltas = []
        if isinstance(old_metrics, dict) and isinstance(new_metrics, dict):
            deltas = metric_deltas(old_metrics, new_metrics)
        changes.append(CheckChange(
            check_id=cid,
            title=str(source.get("title") or cid),
            old_status=old_status,
            new_status=new_status,
            change=_classify(old_status, new_status),
            summary=str(source.get("summary") or ""),
            metrics=deltas,
        ))
    return Comparison(
        baseline_label=baseline_label,
        current_label=current_label,
        baseline_generated_at=str(baseline.get("generated_at", "")),
        current_generated_at=str(current.get("generated_at", "")),
        baseline_database=str(old_db.get("name", "")),
        current_database=str(new_db.get("name", "")),
        old_size=int(_number(old_db.get("total_size_bytes")) or 0),
        new_size=int(_number(new_db.get("total_size_bytes")) or 0),
        checks=changes,
        warnings=warnings,
    )


# --- rendering -------------------------------------------------------------

_MARK = {
    CHANGE_WORSE: "worse",
    CHANGE_BETTER: "better",
    CHANGE_CHANGED: "changed",
    CHANGE_SAME: "same",
    CHANGE_NEW: "new",
    CHANGE_REMOVED: "removed",
}


def _status(value: Status | None) -> str:
    return value.value if value else "-"


def _fmt_value(metric: MetricDelta, value: float) -> str:
    if metric.is_bytes:
        return format_bytes(value)
    if float(value).is_integer():
        return format_int(int(value))
    return f"{value:,.2f}"


def _fmt_delta(metric: MetricDelta) -> str:
    sign = "+" if metric.delta > 0 else "-"
    amount = _fmt_value(metric, abs(metric.delta))
    pct = metric.percent
    return f"{sign}{amount}" + ("" if pct is None else f", {pct:+.1f}%")


def _size_line(comp: Comparison) -> str:
    sign = "+" if comp.size_delta >= 0 else "-"
    line = (f"{format_bytes(comp.old_size)} -> {format_bytes(comp.new_size)} "
            f"({sign}{format_bytes(abs(comp.size_delta))}")
    growth = comp.size_growth_percent
    if growth is not None:
        line += f", {growth:+.1f}%"
    return line + ")"


def _summary_line(comp: Comparison) -> str:
    counts = comp.counts()
    parts = [f"{counts[k]} {k}" for k in ALL_CHANGES if counts[k]]
    return ", ".join(parts) or "no checks"


def _interesting(comp: Comparison, show_unchanged: bool) -> list[CheckChange]:
    if show_unchanged:
        return comp.checks
    return [c for c in comp.checks if c.change != CHANGE_SAME or c.metrics]


def render_comparison_text(comp: Comparison, show_unchanged: bool = False) -> str:
    out = [
        f"Comparing {comp.baseline_label} ({comp.baseline_generated_at or 'unknown date'})",
        f"     with {comp.current_label} ({comp.current_generated_at or 'unknown date'})",
        f"Database size: {_size_line(comp)}",
        f"Checks: {_summary_line(comp)}",
    ]
    out.extend(f"warning: {w}" for w in comp.warnings)
    rows = _interesting(comp, show_unchanged)
    if rows:
        out.append("")
        width = max(len(c.check_id) for c in rows)
        for c in rows:
            flag = "!" if c.is_regression else " "
            out.append(f"{flag} {_MARK[c.change].ljust(7)}  {c.check_id.ljust(width)}  "
                       f"{_status(c.old_status)} -> {_status(c.new_status)}")
            for m in c.metrics:
                out.append(f"{' ' * 11}{m.name}: {_fmt_value(m, m.old)} -> "
                           f"{_fmt_value(m, m.new)} ({_fmt_delta(m)})")
    out.append("")
    if comp.regressions:
        out.append(f"{len(comp.regressions)} regression(s): "
                   + ", ".join(c.check_id for c in comp.regressions))
    else:
        out.append("No regressions.")
    return "\n".join(out) + "\n"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_comparison_markdown(comp: Comparison, show_unchanged: bool = False) -> str:
    out = ["# WordPress database comparison", ""]
    out.append("| | Baseline | Current |")
    out.append("|---|---|---|")
    out.append(f"| File | {_cell(comp.baseline_label)} | {_cell(comp.current_label)} |")
    out.append(f"| Generated | {_cell(comp.baseline_generated_at)} | "
               f"{_cell(comp.current_generated_at)} |")
    out.append(f"| Database | {_cell(comp.baseline_database)} | "
               f"{_cell(comp.current_database)} |")
    out.append(f"| Size | {format_bytes(comp.old_size)} | {format_bytes(comp.new_size)} |")
    out.append("")
    out.append(f"**Size change:** {_size_line(comp)}  ")
    out.append(f"**Checks:** {_summary_line(comp)}")
    out.extend(f"\n> Warning: {_cell(w)}" for w in comp.warnings)
    rows = _interesting(comp, show_unchanged)
    if rows:
        out.append("")
        out.append("| Change | Check | Status | Metric changes |")
        out.append("|---|---|---|---|")
        for c in rows:
            label = f"**{_MARK[c.change]}**" if c.is_regression else _MARK[c.change]
            metrics = "<br>".join(
                f"{_cell(m.name)}: {_fmt_value(m, m.old)} &rarr; {_fmt_value(m, m.new)} "
                f"({_fmt_delta(m)})" for m in c.metrics) or ""
            out.append(f"| {label} | {_cell(c.title)} (`{c.check_id}`) | "
                       f"{_status(c.old_status)} &rarr; {_status(c.new_status)} | {metrics} |")
    out.append("")
    if comp.regressions:
        out.append(f"**{len(comp.regressions)} regression(s):** "
                   + ", ".join(f"`{c.check_id}`" for c in comp.regressions))
    else:
        out.append("No regressions.")
    return "\n".join(out) + "\n"


def render_comparison_json(comp: Comparison, show_unchanged: bool = True) -> str:
    return json.dumps(comp.to_dict(), indent=2) + "\n"


def render_comparison(comp: Comparison, fmt: str, show_unchanged: bool = False) -> str:
    if fmt == "text":
        return render_comparison_text(comp, show_unchanged)
    if fmt == "markdown":
        return render_comparison_markdown(comp, show_unchanged)
    if fmt == "json":
        return render_comparison_json(comp)
    raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(COMPARE_FORMATS)}")


def compare_format_for_path(path: str) -> str | None:
    low = path.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".markdown")):
        return "markdown"
    if low.endswith(".txt"):
        return "text"
    return None


__all__ = [
    "COMPARE_FORMATS",
    "CheckChange",
    "Comparison",
    "MetricDelta",
    "ReportLoadError",
    "compare_format_for_path",
    "compare_reports",
    "load_report",
    "metric_deltas",
    "render_comparison",
    "validate_report",
]
