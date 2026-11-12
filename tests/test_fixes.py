from __future__ import annotations

import pytest

from wp_db_doctor.checks import run_checks, select_checks
from wp_db_doctor.fixes import (
    BackupNotConfirmed,
    FixPlan,
    apply_plan,
    backup_commands,
    build_plan,
    confirm_backup,
    render_sql,
)
from wp_db_doctor.models import (
    STAGE_CONTENT,
    STAGE_ORPHANS,
    FixKind,
    FixStatement,
)


@pytest.fixture
def results(bloated_db, make_ctx):
    return run_checks(select_checks(), make_ctx(bloated_db))


def plan_for(results, **kwargs):
    return build_plan(results, database="wordpress", prefix="wp_", host="u@localhost",
                      created_at="2026-11-12T00:00:00Z", **kwargs)


def test_plan_excludes_schema_by_default(results):
    plan = plan_for(results)
    assert plan.excluded_schema > 0
    assert not any(s.is_schema_change for s in plan.statements)


def test_plan_include_schema(results):
    plan = plan_for(results, include_schema=True)
    assert plan.excluded_schema == 0
    kinds = {s.kind for s in plan.statements}
    assert FixKind.DDL in kinds and FixKind.OPTIMIZE in kinds
    # OPTIMIZE runs last, after every DELETE.
    assert plan.statements[-1].kind is FixKind.OPTIMIZE


def test_plan_order_content_before_orphans(results):
    plan = plan_for(results)
    stages = [s.stage for s in plan.statements]
    assert stages == sorted(stages)
    first_orphan = next(i for i, s in enumerate(plan.statements) if s.stage == STAGE_ORPHANS)
    last_content = max(i for i, s in enumerate(plan.statements) if s.stage == STAGE_CONTENT)
    assert last_content < first_orphan


def test_plan_without_review(results):
    plan = plan_for(results, include_review=False)
    assert plan.review_only == []


def test_render_sql(results):
    sql = render_sql(plan_for(results), user="shop_user")
    assert sql.startswith("-- wp-db-doctor ")
    assert "READ EVERY STATEMENT" in sql
    assert "mysqldump --single-transaction" in sql and "-u shop_user" in sql
    assert "-- REVIEW: Stop autoloading wpseo_premium_redirects_export" in sql
    assert "-- UPDATE `wp_options` SET autoload = 'off'" in sql
    assert "\nDELETE p FROM `wp_posts` p WHERE p.post_type = 'revision'" in sql
    assert "schema change(s) left out" in sql
    # Every executable statement ends with a semicolon on its own line.
    for line in sql.splitlines():
        if line and not line.startswith("--"):
            assert line.endswith(";"), line


def test_render_empty_plan():
    plan = FixPlan(database="wp", prefix="wp_", host="h", created_at="now")
    assert "Nothing to fix." in render_sql(plan)


def test_backup_commands_quote_names():
    cmds = backup_commands("my db", user="o'neil")
    assert "'my db'" in cmds[1]
    assert "'o'\"'\"'neil'" in cmds[1]


def test_confirm_backup_paths():
    confirm_backup("wp", assume_yes=True, interactive=False)
    with pytest.raises(BackupNotConfirmed, match="--i-have-a-backup"):
        confirm_backup("wp", assume_yes=False, interactive=False)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return "wp\n"

    confirm_backup("wp", assume_yes=False, interactive=True, ask=ask)
    assert "Type the database name" in prompts[0]
    with pytest.raises(BackupNotConfirmed):
        confirm_backup("wp", assume_yes=False, interactive=True, ask=lambda _: "yes")


def test_apply_skips_review_and_reports(bloated_db, results):
    plan = plan_for(results, include_schema=True)
    seen = []
    done = apply_plan(bloated_db, plan, progress=seen.append)
    assert len(done) == len(plan.executable) == len(seen)
    assert all(not s.startswith("UPDATE") for s in bloated_db.executed)
    assert not any("ADD PRIMARY KEY" in s for s in bloated_db.executed)
    assert all(d.affected == 7 for d in done)


def test_apply_stops_on_error(bloated_db, results):
    plan = plan_for(results)
    bloated_db.fail_execute_on = "wp_comments"
    done = apply_plan(bloated_db, plan)
    assert done[-1].error and "Lock wait" in done[-1].error
    assert len(done) < len(plan.executable)


def test_fix_statement_dict():
    f = FixStatement("x", "d", "DELETE FROM t", kind=FixKind.DELETE, estimated_rows=2)
    assert f.to_dict()["kind"] == "delete"
    assert not f.is_schema_change
