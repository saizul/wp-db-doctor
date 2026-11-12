from __future__ import annotations

import json

import pytest

from wp_db_doctor.audit import run_audit
from wp_db_doctor.models import Status
from wp_db_doctor.report import FORMATS, format_for_path, render

from .conftest import TODAY, FakeDatabase, bloated_responses


@pytest.fixture
def report(bloated_db):
    return run_audit(bloated_db, prefix="wp_", schema="wordpress", host_label="u@localhost",
                     generated_at="2026-11-12T08:00:00Z", today=TODAY)


def test_report_basics(report):
    assert report.database.name == "wordpress"
    assert report.database.server_version == "5.7.44-log"
    assert report.database.table_count == 19
    assert report.worst is Status.CRITICAL
    counts = report.counts()
    assert counts["critical"] >= 4
    assert sum(counts.values()) == len(report.results)


def test_json(report):
    data = json.loads(render(report, "json"))
    assert data["schema_version"] == 1
    assert data["tool"]["name"] == "wp-db-doctor"
    assert data["database"]["prefix"] == "wp_"
    ids = [c["id"] for c in data["checks"]]
    assert ids[0] == "table_prefix"
    autoload = next(c for c in data["checks"] if c["id"] == "autoload")
    assert autoload["status"] == "critical"
    assert autoload["details"]["columns"][0] == "option"
    assert autoload["fixes"][0]["review_only"] is True


def test_text(report):
    text = render(report, "text")
    assert "[CRIT]   Autoloaded options (autoload)" in text
    assert "wpseo_premium_redirects_export" in text
    assert "Summary:" in text
    assert "wp-db-doctor fix" in text
    assert max(len(line) for line in text.splitlines()) <= 120


def test_markdown(report):
    md = render(report, "markdown")
    assert md.startswith("# WordPress database report: wordpress")
    assert "| CRIT | [Autoloaded options](#autoload) |" in md
    assert '<a id="autoload"></a>' in md
    assert "wp-db-doctor fix --only post_revisions" in md


def test_html_is_self_contained_and_escaped(bloated_db):
    resp = bloated_responses()
    resp["autoload_top"] = [{"option_name": "<script>alert(1)</script>", "bytes": 4000000}]
    r = run_audit(FakeDatabase(resp), prefix="wp_", schema="wordpress",
                  generated_at="x", today=TODAY)
    html = render(r, "html")
    assert html.startswith("<!DOCTYPE html>")
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "http://" not in html and "https://" not in html
    assert "prefers-color-scheme:dark" in html
    assert "Proposed SQL" in html


def test_markdown_escapes_pipes(bloated_db):
    resp = bloated_responses()
    resp["autoload_top"] = [{"option_name": "a|b", "bytes": 4000000}]
    r = run_audit(FakeDatabase(resp), prefix="wp_", schema="wordpress",
                  generated_at="x", today=TODAY)
    assert "a\\|b" in render(r, "markdown")


def test_all_formats_render(report):
    for fmt in FORMATS:
        assert render(report, fmt)
    with pytest.raises(ValueError):
        render(report, "xml")


@pytest.mark.parametrize(
    ("path", "fmt"),
    [("r.json", "json"), ("r.md", "markdown"), ("R.HTML", "html"), ("r.txt", "text"),
     ("r.pdf", None)],
)
def test_format_for_path(path, fmt):
    assert format_for_path(path) == fmt
