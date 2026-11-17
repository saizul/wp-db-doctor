from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest

from wp_db_doctor.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from wp_db_doctor.compare import (
    ReportLoadError,
    compare_reports,
    load_report,
    metric_deltas,
    render_comparison,
    validate_report,
)
from wp_db_doctor.models import Status

from .conftest import FakeDatabase, bloated_responses, clean_responses

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-report.json"


def _check(cid, status, metrics=None, title=None):
    return {
        "id": cid,
        "title": title or cid.replace("_", " ").title(),
        "category": "content",
        "status": status,
        "summary": f"{cid} is {status}",
        "metrics": metrics or {},
        "details": {"columns": [], "rows": []},
        "recommendations": [],
        "fixes": [],
    }


def _report(checks, size=1000, generated="2026-11-01T00:00:00Z", name="example_wp"):
    return {
        "schema_version": 1,
        "tool": {"name": "wp-db-doctor", "version": "0.1.0"},
        "generated_at": generated,
        "database": {"name": name, "host": "h", "prefix": "wp_", "total_size_bytes": size},
        "summary": {},
        "checks": checks,
    }


@pytest.fixture
def pair():
    old = _report([
        _check("autoload", "warning", {"autoload_bytes": 1048576, "autoload_options": 900}),
        _check("post_revisions", "critical", {"revisions": 25000}),
        _check("spam_comments", "ok", {"spam": 0}),
        _check("woocommerce_sessions", "ok", {"sessions": 10}),
        _check("table_sizes", "warning", {"total_bytes": 1000}),
    ], size=1000)
    new = _report([
        _check("autoload", "critical", {"autoload_bytes": 4194304, "autoload_options": 900}),
        _check("post_revisions", "ok", {"revisions": 12}),
        _check("spam_comments", "ok", {"spam": 0}),
        _check("table_sizes", "error", {"total_bytes": 1500}),
        _check("missing_indexes", "critical", {"missing": 2}),
    ], size=1500, generated="2026-11-08T00:00:00Z")
    return old, new


def test_classification(pair):
    comp = compare_reports(*pair)
    by_id = {c.check_id: c for c in comp.checks}
    assert by_id["autoload"].change == "worse"
    assert by_id["post_revisions"].change == "better"
    assert by_id["spam_comments"].change == "same"
    # warning and error share a rank: reported, but not a regression
    assert by_id["table_sizes"].change == "changed"
    assert not by_id["table_sizes"].is_regression
    assert by_id["missing_indexes"].change == "new"
    assert by_id["woocommerce_sessions"].change == "removed"
    assert by_id["woocommerce_sessions"].new_status is None
    assert [c.check_id for c in comp.regressions] == ["autoload", "missing_indexes"]
    assert [c.check_id for c in comp.improvements] == ["post_revisions"]
    # current report order first, removed checks last
    assert comp.checks[-1].check_id == "woocommerce_sessions"


def test_new_ok_check_is_not_a_regression():
    comp = compare_reports(_report([]), _report([_check("autoload", "ok")]))
    assert comp.checks[0].change == "new"
    assert comp.regressions == []


def test_metric_deltas_skip_non_numeric_and_unchanged():
    deltas = metric_deltas(
        {"a": 1, "b": 2, "flag": True, "name": "x", "nested": {"k": 1}, "gone": 5},
        {"a": 3, "b": 2, "flag": False, "name": "y", "nested": {"k": 2}, "added": 1},
    )
    assert [(d.name, d.old, d.new, d.delta) for d in deltas] == [("a", 1, 3, 2)]
    assert deltas[0].percent == pytest.approx(200.0)
    assert metric_deltas({"z": 0}, {"z": 4})[0].percent is None


def test_size_growth(pair):
    comp = compare_reports(*pair)
    assert comp.size_delta == 500
    assert comp.size_growth_percent == pytest.approx(50.0)
    assert compare_reports(_report([], size=0), _report([], size=10)).size_growth_percent is None


def test_warnings_for_mismatched_reports():
    old = _report([], generated="2026-11-09T00:00:00Z", name="shop")
    new = _report([], generated="2026-11-01T00:00:00Z", name="blog")
    comp = compare_reports(old, new)
    assert any("different databases" in w for w in comp.warnings)
    assert any("newer than the current" in w for w in comp.warnings)


def test_text_rendering(pair):
    text = render_comparison(compare_reports(*pair), "text")
    assert "Database size: 1000 B -> 1.5 KB (+500 B, +50.0%)" in text
    assert "! worse    autoload" in text
    assert "autoload_bytes: 1.0 MB -> 4.0 MB (+3.0 MB, +300.0%)" in text
    assert "revisions: 25,000 -> 12 (-24,988, -100.0%)" in text
    assert "spam_comments" not in text
    assert "2 regression(s): autoload, missing_indexes" in text
    assert "spam_comments" in render_comparison(compare_reports(*pair), "text", True)


def test_text_no_regressions():
    old = _report([_check("autoload", "critical")])
    new = _report([_check("autoload", "ok")])
    assert render_comparison(compare_reports(old, new), "text").endswith("No regressions.\n")


def test_markdown_rendering(pair):
    md = render_comparison(compare_reports(*pair), "markdown")
    assert md.startswith("# WordPress database comparison")
    assert "| **worse** | Autoload (`autoload`) | warning &rarr; critical |" in md
    assert "**2 regression(s):** `autoload`, `missing_indexes`" in md


def test_json_rendering(pair):
    data = json.loads(render_comparison(compare_reports(*pair), "json"))
    assert data["regressions"] == ["autoload", "missing_indexes"]
    assert data["size_growth_percent"] == 50.0
    assert data["summary"]["worse"] == 1
    autoload = next(c for c in data["checks"] if c["id"] == "autoload")
    assert autoload["metrics"] == [{"metric": "autoload_bytes", "old": 1048576,
                                    "new": 4194304, "delta": 3145728, "percent": 300.0}]
    # JSON always lists every check
    assert len(data["checks"]) == 6


def test_unknown_format(pair):
    with pytest.raises(ValueError):
        render_comparison(compare_reports(*pair), "html")


def test_sample_report_loads_and_compares_with_itself():
    data = load_report(SAMPLE)
    comp = compare_reports(data, copy.deepcopy(data))
    assert comp.regressions == []
    assert {c.change for c in comp.checks} == {"same"}
    assert comp.size_delta == 0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "expected an object"),
        ({"tool": {"name": "other"}}, "no tool name"),
        ({"tool": {"name": "wp-db-doctor"}, "schema_version": 99, "checks": []},
         "schema version 99"),
        ({"tool": {"name": "wp-db-doctor"}, "schema_version": 1}, "no list of checks"),
        ({"tool": {"name": "wp-db-doctor"}, "schema_version": 1, "checks": [{}]}, "has no id"),
        ({"tool": {"name": "wp-db-doctor"}, "schema_version": 1,
          "checks": [{"id": "x", "status": "bad"}]}, "unknown status"),
    ],
)
def test_validate_report_rejects(data, message):
    with pytest.raises(ReportLoadError, match=message):
        validate_report(data)


def test_load_report_errors(tmp_path):
    with pytest.raises(ReportLoadError, match="cannot read"):
        load_report(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReportLoadError, match="not valid JSON"):
        load_report(bad)


# --- CLI ---------------------------------------------------------------------


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), env={}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_compare_default(tmp_path, pair):
    old = _write(tmp_path / "old.json", pair[0])
    new = _write(tmp_path / "new.json", pair[1])
    code, out, _ = _run("compare", old, new)
    assert code == EXIT_OK
    assert "2 regression(s)" in out


def test_cli_compare_fail_on_regression(tmp_path, pair):
    old = _write(tmp_path / "old.json", pair[0])
    new = _write(tmp_path / "new.json", pair[1])
    assert _run("compare", old, new, "--fail-on-regression")[0] == EXIT_FINDINGS
    # reversed: revisions are back, and the re-added sessions check is ok (not a regression)
    code, out, _ = _run("compare", new, old, "--fail-on-regression")
    assert code == EXIT_FINDINGS
    assert "1 regression(s): post_revisions" in out
    same = _write(tmp_path / "same.json", pair[1])
    assert _run("compare", new, same, "--fail-on-regression")[0] == EXIT_OK


def test_cli_compare_max_growth(tmp_path, pair):
    old = _write(tmp_path / "old.json", pair[0])
    new = _write(tmp_path / "new.json", pair[1])
    code, _, err = _run("compare", old, new, "--max-growth", "25")
    assert code == EXIT_FINDINGS
    assert "grew by 50.0%" in err
    assert _run("compare", old, new, "--max-growth", "60")[0] == EXIT_OK
    assert _run("compare", old, new, "--max-growth", "-1")[0] == EXIT_USAGE


def test_cli_compare_outputs(tmp_path, pair):
    old = _write(tmp_path / "old.json", pair[0])
    new = _write(tmp_path / "new.json", pair[1])
    md, js = tmp_path / "diff.md", tmp_path / "diff.json"
    code, out, err = _run("compare", old, new, "-o", str(md), "-o", str(js), "-q")
    assert code == EXIT_OK
    assert out == ""
    assert f"wrote {md}" in err
    assert md.read_text(encoding="utf-8").startswith("# WordPress database comparison")
    assert json.loads(js.read_text(encoding="utf-8"))["regressions"]
    code, _, err = _run("compare", old, new, "-o", str(tmp_path / "diff.html"))
    assert code == EXIT_USAGE
    assert "cannot tell the format" in err


def test_cli_compare_json_stdout(tmp_path, pair):
    old = _write(tmp_path / "old.json", pair[0])
    new = _write(tmp_path / "new.json", pair[1])
    code, out, _ = _run("compare", old, new, "-f", "json")
    assert code == EXIT_OK
    assert json.loads(out)["baseline"]["file"] == old


def test_cli_compare_bad_input(tmp_path):
    bad = _write(tmp_path / "bad.json", {"hello": "world"})
    code, _, err = _run("compare", bad, bad)
    assert code == EXIT_USAGE
    assert "not a wp-db-doctor JSON report" in err


def test_cli_audit_then_compare_round_trip(tmp_path):
    """Reports written by `audit` are accepted by `compare` unchanged."""
    files = {}
    for label, responses in (("before", bloated_responses()), ("after", clean_responses())):
        db = FakeDatabase(responses)
        path = tmp_path / f"{label}.json"
        code = main(["audit", "-u", "wpuser", "-d", "wordpress", "-o", str(path), "-q"],
                    connector=lambda _s, _r, db=db: db, env={}, stdout=io.StringIO(),
                    stderr=io.StringIO())
        assert code == EXIT_OK
        files[label] = str(path)
    # cleaning up is an improvement, never a regression
    code, out, _ = _run("compare", files["before"], files["after"], "--fail-on-regression")
    assert code == EXIT_OK
    assert "better" in out
    # the other way round, the bloat shows up as regressions
    code, out, _ = _run("compare", files["after"], files["before"], "--fail-on-regression")
    assert code == EXIT_FINDINGS
    comp = compare_reports(load_report(files["after"]), load_report(files["before"]))
    assert all(c.new_status is not None and c.new_status.rank >= Status.INFO.rank
               for c in comp.regressions)
