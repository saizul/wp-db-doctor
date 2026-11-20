from __future__ import annotations

import pytest

from wp_db_doctor.checks import (
    ALL_CHECKS,
    CHECKS_BY_ID,
    UnknownCheckError,
    run_check,
    run_checks,
    select_checks,
)
from wp_db_doctor.checks.base import grade
from wp_db_doctor.checks.options import owner_hint
from wp_db_doctor.config import Thresholds
from wp_db_doctor.db import DatabaseError
from wp_db_doctor.models import Status
from wp_db_doctor.utils import InvalidPrefixError

from .conftest import FakeDatabase, bloated_responses, clean_responses


def run(db, make_ctx, check_id, **kwargs):
    ctx = make_ctx(db, **kwargs)
    return run_check(CHECKS_BY_ID[check_id](), ctx)


def test_check_ids_are_unique_and_documented():
    ids = [c.id for c in ALL_CHECKS]
    assert len(ids) == len(set(ids))
    for check in ALL_CHECKS:
        assert check.title and check.category and check.description


def test_clean_site_has_no_problems(clean_db, make_ctx):
    results = run_checks(select_checks(), make_ctx(clean_db))
    by_id = {r.check_id: r for r in results}
    assert by_id["action_scheduler"].status is Status.SKIPPED
    assert by_id["woocommerce_sessions"].status is Status.SKIPPED
    assert by_id["table_prefix"].status is Status.INFO  # default wp_ prefix note
    problems = [r for r in results if r.status.rank >= Status.WARNING.rank]
    assert problems == []
    assert all(not r.fixes for r in results)


def test_bloated_site_statuses(bloated_db, make_ctx):
    results = {r.check_id: r for r in run_checks(select_checks(), make_ctx(bloated_db))}
    expected = {
        "autoload": Status.CRITICAL,
        "expired_transients": Status.WARNING,
        "post_revisions": Status.CRITICAL,
        "auto_drafts": Status.WARNING,
        "trashed_posts": Status.WARNING,
        "spam_comments": Status.CRITICAL,
        "oembed_cache": Status.WARNING,
        "orphaned_postmeta": Status.WARNING,
        "orphaned_termmeta": Status.INFO,
        "orphaned_commentmeta": Status.OK,
        "orphaned_term_relationships": Status.WARNING,
        "action_scheduler": Status.CRITICAL,
        "woocommerce_sessions": Status.WARNING,
        "missing_indexes": Status.CRITICAL,
        "engine_collation": Status.WARNING,
        "table_sizes": Status.WARNING,
        "table_prefix": Status.WARNING,
        "server_version": Status.WARNING,
    }
    for cid, status in expected.items():
        assert results[cid].status is status, (cid, results[cid].summary)


def test_autoload_top_offenders_and_review_fixes(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "autoload")
    assert r.metrics["autoload_bytes"] == 4 * 1024 * 1024
    assert r.rows[0][0] == "wpseo_premium_redirects_export"
    assert r.rows[0][3] == "Yoast SEO"
    assert r.rows[0][1] == "1.6 MB"
    # cron is core, the transient is reported separately, small_one is below the limit.
    targets = [f.sql for f in r.fixes]
    assert len(targets) == 2
    assert all(f.review_only for f in r.fixes)
    assert "'wpseo_premium_redirects_export'" in targets[0]
    assert "SET autoload = 'off'" in targets[0]
    assert any("transients are autoloaded" in rec for rec in r.recommendations)
    assert any("cron option is very large" in rec for rec in r.recommendations)


def test_autoload_threshold_from_config(clean_db, make_ctx):
    th = Thresholds(autoload_warning_kb=100)
    r = run(clean_db, make_ctx, "autoload", thresholds=th)
    assert r.status is Status.WARNING


def test_autoload_query_covers_new_autoload_values(clean_db, make_ctx):
    run(clean_db, make_ctx, "autoload")
    assert "autoload_total" in clean_db.tags()


def test_expired_transients_fixes(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "expired_transients")
    assert r.metrics["expired"] == 1800
    assert len(r.fixes) == 2
    assert "SUBSTRING(a.option_name, 20)" in r.fixes[0].sql
    assert "SUBSTRING(a.option_name, 25)" in r.fixes[1].sql
    assert r.fixes[0].estimated_rows == 3600
    assert any("WP-Cron" in rec for rec in r.recommendations)


def test_revisions(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "post_revisions")
    assert r.metrics["old_revisions"] == 21000
    assert r.rows[1][2] == "(parent missing)"
    sqls = [f.sql for f in r.fixes]
    assert sqls[0].startswith("DELETE pm FROM `wp_postmeta` pm")
    assert sqls[1].startswith("DELETE tr FROM `wp_term_relationships` tr")
    assert sqls[-1].startswith("DELETE p FROM `wp_posts` p WHERE p.post_type = 'revision'")
    assert "INTERVAL 30 DAY" in sqls[-1]
    assert any("WP_POST_REVISIONS" in rec for rec in r.recommendations)


def test_revisions_mentions_existing_limit(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "post_revisions", constants={"WP_POST_REVISIONS": 3})
    assert any("WP_POST_REVISIONS is 3" in rec for rec in r.recommendations)


def test_revisions_none(clean_db, make_ctx):
    r = run(clean_db, make_ctx, "post_revisions")
    assert r.status is Status.OK
    assert "revisions_top" not in clean_db.tags()


def test_auto_drafts(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "auto_drafts")
    assert r.metrics == {"auto_drafts": 420, "old_auto_drafts": 400}
    assert "post_status = 'auto-draft'" in r.fixes[-1].sql
    assert any("cron" in rec.lower() for rec in r.recommendations)


def test_trashed_posts_include_comments(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "trashed_posts")
    assert r.metrics["trashed"] == 240
    descriptions = " ".join(f.description for f in r.fixes)
    assert "comment meta" in descriptions and "Delete comments on" in descriptions
    assert any("WooCommerce orders" in rec for rec in r.recommendations)


def test_trashed_posts_without_comment_tables(make_ctx):
    resp = clean_responses()
    resp["tables"] = [t for t in resp["tables"]
                      if t["TABLE_NAME"] not in ("wp_comments", "wp_commentmeta")]
    resp["trash"] = [{"post_type": "page", "total": 5, "old": 5}]
    r = run(FakeDatabase(resp), make_ctx, "trashed_posts")
    assert r.status is Status.INFO
    assert not any("comment" in f.description for f in r.fixes)


def test_spam_comments(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "spam_comments")
    assert r.metrics["spam"] == 60000
    assert r.fixes[-1].estimated_rows == 60040
    assert "comment_approved IN ('spam', 'trash')" in r.fixes[-1].sql
    assert any("wp comment recount" in rec for rec in r.recommendations)


def test_orphaned_postmeta(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "orphaned_postmeta")
    assert r.rows[0] == ["_edit_lock", "30,000"]
    assert r.fixes[0].sql == ("DELETE m FROM `wp_postmeta` m LEFT JOIN `wp_posts` p "
                              "ON p.ID = m.post_id WHERE p.ID IS NULL")


def test_orphaned_commentmeta_join_columns(bloated_db, make_ctx):
    run(bloated_db, make_ctx, "orphaned_commentmeta")
    assert bloated_db.tags()[-1] == "orphans:commentmeta"


def test_orphaned_term_relationships_excludes_links(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "orphaned_term_relationships")
    assert "link_category" in r.fixes[0].sql
    assert r.metrics == {"missing_post": 1200, "missing_term_taxonomy": 4}


def test_action_scheduler(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "action_scheduler")
    assert r.metrics["prunable"] == 582500
    assert r.metrics["overdue_pending"] == 450
    assert r.metrics["log_rows_estimate"] == 1_800_000
    sqls = [f.sql for f in r.fixes]
    assert sqls[0].startswith("DELETE l FROM `wp_actionscheduler_logs` l INNER JOIN")
    assert "status IN ('complete', 'failed', 'canceled')" in sqls[1]
    assert "a.action_id IS NULL" in sqls[2]
    assert any("woocommerce_run_product_attribute_lookup_update" in rec
               for rec in r.recommendations)


def test_action_scheduler_overdue_raises_status(make_ctx):
    resp = clean_responses(extra=("actionscheduler_actions",))
    resp["as_status"] = [{"status": "pending", "total": 300, "old": 0, "overdue": 300}]
    r = run(FakeDatabase(resp), make_ctx, "action_scheduler")
    assert r.status is Status.WARNING
    assert r.fixes == []


def test_woocommerce_sessions(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "woocommerce_sessions")
    assert r.fixes[0].sql == ("DELETE FROM `wp_woocommerce_sessions` "
                              "WHERE session_expiry < UNIX_TIMESTAMP()")


def test_missing_indexes(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "missing_indexes")
    found = {(row[0], row[1]) for row in r.rows}
    assert ("wp_postmeta", "meta_key") in found
    assert ("wp_options", "PRIMARY KEY") in found
    assert ("wp_posts", "AUTO_INCREMENT") in found
    add_index = next(f for f in r.fixes if "meta_key" in f.sql)
    assert add_index.sql == "ALTER TABLE `wp_postmeta` ADD INDEX `meta_key` (`meta_key`(191))"
    assert not add_index.review_only
    pk = next(f for f in r.fixes if "PRIMARY KEY" in f.sql)
    assert pk.review_only
    assert all(f.is_schema_change for f in r.fixes)


def test_missing_indexes_params(bloated_db, make_ctx):
    run(bloated_db, make_ctx, "missing_indexes")
    params = dict(bloated_db.queries)["autoinc"]
    assert params[0] == "wordpress"
    assert params[-1] == "%auto_increment%"


def test_engine_collation(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "engine_collation")
    problems = {row[0]: row[3] for row in r.rows}
    assert problems["wp_yoast_indexable"] in ("not InnoDB", "not utf8mb4")
    assert any("differs from utf8mb4_unicode_520_ci" in v for v in problems.values())
    engine_fix = next(f for f in r.fixes if "ENGINE=InnoDB" in f.sql)
    assert not engine_fix.review_only
    assert all(f.review_only for f in r.fixes if "CONVERT TO" in f.sql)


def test_engine_collation_respects_db_collate(clean_db, make_ctx):
    r = run(clean_db, make_ctx, "engine_collation",
            constants={"DB_COLLATE": "utf8mb4_0900_ai_ci"})
    assert r.status is Status.INFO
    assert r.metrics["target_collation"] == "utf8mb4_0900_ai_ci"


def test_table_sizes(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "table_sizes")
    assert r.rows[0][0] == "wp_postmeta"
    assert "large" in r.rows[0][5]
    assert r.fixes[0].sql == "OPTIMIZE TABLE `wp_postmeta`"
    assert any("do not use the wp_ prefix" in rec for rec in r.recommendations)


def test_table_prefix_other_installs(bloated_db, make_ctx):
    r = run(bloated_db, make_ctx, "table_prefix")
    assert r.metrics["other_prefixes"] == ["old_"]


def test_table_prefix_wrong(clean_db, make_ctx):
    r = run(clean_db, make_ctx, "table_prefix", prefix="shop_")
    assert r.status is Status.CRITICAL
    assert "found: wp_" in r.summary


def test_table_prefix_multisite(make_ctx):
    resp = clean_responses(extra=("2_options", "2_posts", "10_options", "10_posts"))
    r = run(FakeDatabase(resp), make_ctx, "table_prefix")
    assert r.metrics["subsites"] == 2
    assert "multisite" in r.summary
    assert any("--prefix wp_2_" in rec for rec in r.recommendations)


def test_table_prefix_missing_core_tables(make_ctx):
    resp = clean_responses(prefix="site_")
    resp["tables"] = [t for t in resp["tables"] if t["TABLE_NAME"] != "site_termmeta"]
    r = run(FakeDatabase(resp), make_ctx, "table_prefix", prefix="site_")
    assert r.status is Status.WARNING
    assert "site_termmeta" in r.recommendations[0]


@pytest.mark.parametrize(
    ("version", "status"),
    [
        ("10.11.8-MariaDB", Status.OK),
        ("10.6.18-MariaDB-0ubuntu0.22.04.1", Status.WARNING),
        ("5.5.5-10.4.32-MariaDB", Status.WARNING),
        ("8.0.39", Status.WARNING),
        ("8.4.2", Status.OK),
        ("5.5.62", Status.WARNING),
        ("9.1.0", Status.OK),
    ],
)
def test_server_version(make_ctx, version, status):
    db = FakeDatabase(clean_responses(version=version))
    r = run(db, make_ctx, "server_version")
    assert r.status is status, r.summary


def test_buffer_pool_too_small(make_ctx):
    resp = bloated_responses()
    resp["server"][0]["version"] = "8.4.2"
    resp["server"][0]["buffer_pool"] = 128 * 1024 * 1024
    r = run(FakeDatabase(resp), make_ctx, "server_version")
    assert r.status is Status.INFO
    assert "innodb_buffer_pool_size" in r.recommendations[0]


def test_skipped_when_table_missing(clean_db, make_ctx):
    r = run(clean_db, make_ctx, "woocommerce_sessions")
    assert r.status is Status.SKIPPED
    assert "wp_woocommerce_sessions" in r.summary


def test_database_error_becomes_error_status(make_ctx):
    resp = clean_responses()
    resp["autoload_total"] = DatabaseError("MySQL error 1142: SELECT command denied")
    r = run(FakeDatabase(resp), make_ctx, "autoload")
    assert r.status is Status.ERROR
    assert "1142" in r.summary


def test_custom_prefix_is_used_everywhere(make_ctx):
    db = FakeDatabase(bloated_responses(prefix="shop_"))
    results = run_checks(select_checks(), make_ctx(db, prefix="shop_"))
    for r in results:
        for f in r.fixes:
            assert "`wp_" not in f.sql


def test_invalid_prefix_rejected(clean_db, make_ctx):
    with pytest.raises(InvalidPrefixError):
        make_ctx(clean_db, prefix="wp_; DROP TABLE x")


def test_select_checks():
    assert [c.id for c in select_checks(only=["autoload", "table_sizes"])] == [
        "autoload", "table_sizes"]
    assert "autoload" not in [c.id for c in select_checks(skip=["autoload"])]
    with pytest.raises(UnknownCheckError):
        select_checks(only=["nope"])


def test_grade():
    assert grade(0, 10) is Status.OK
    assert grade(5, 10) is Status.INFO
    assert grade(10, 10) is Status.WARNING
    assert grade(100, 10, 50) is Status.CRITICAL


def test_owner_hint():
    assert owner_hint("wpseo_titles") == "Yoast SEO"
    assert owner_hint("_transient_x").startswith("transient")
    assert owner_hint("something_unknown") == ""


def test_every_query_is_tagged_and_safe(bloated_db, make_ctx):
    # FakeDatabase asserts tags and %-safety on each query; run everything.
    run_checks(select_checks(), make_ctx(bloated_db))
    assert len(bloated_db.queries) > 20


def test_oembed_cache_clean_and_bloated(clean_db, bloated_db, make_ctx):
    clean = run(clean_db, make_ctx, "oembed_cache")
    assert clean.status is Status.OK and clean.fixes == []
    assert clean.metrics == {"oembed_cache_posts": 12, "oembed_legacy_meta": 0, "oembed_failed": 0,
                             "oembed_bytes": 9000}

    r = run(bloated_db, make_ctx, "oembed_cache")
    assert r.status is Status.WARNING
    assert r.metrics["oembed_failed"] == 1200
    assert r.summary == ("4,200 oembed_cache posts, 1,500 legacy _oembed_ meta rows, "
                         "1,200 failed (8.0 MB)")
    assert "1,200 cached oEmbed lookups failed ({{unknown}})" in r.recommendations[0]
    assert "Instagram" in r.recommendations[1]
    sql = [f.sql for f in r.fixes]
    # Only failed lookups are deleted: their post meta first, the posts, then legacy meta.
    assert all("{{unknown}}" in s for s in sql)
    assert any(s.startswith("DELETE pm FROM `wp_postmeta` pm") for s in sql)
    assert any(s.startswith("DELETE p FROM `wp_posts` p WHERE p.post_type = 'oembed_cache'")
               for s in sql)
    assert "CONCAT('_oembed_time_', SUBSTRING(v.meta_key, 9))" in sql[-2]
    assert sql[-1].startswith("DELETE v FROM `wp_postmeta` v WHERE v.meta_key LIKE")
    assert r.fixes[-1].estimated_rows == 300


def test_oembed_cache_failed_only_and_without_postmeta(make_ctx):
    responses = clean_responses()
    responses["oembed"] = [{"total": 5, "failed": 2, "bytes": 100}]
    r = run(FakeDatabase(responses), make_ctx, "oembed_cache")
    assert r.status is Status.INFO  # below the warning threshold, but failed lookups to clean
    assert len(r.recommendations) == 1 and len(r.fixes) == 3

    responses = clean_responses()
    responses["tables"] = [t for t in responses["tables"] if t["TABLE_NAME"] != "wp_postmeta"]
    db = FakeDatabase(responses)
    r = run(db, make_ctx, "oembed_cache")
    assert "oembed_meta" not in db.tags()
    assert r.metrics["oembed_legacy_meta"] == 0


def test_oembed_cache_threshold(bloated_db, make_ctx):
    th = Thresholds(oembed_cache_warning=10_000)
    assert run(bloated_db, make_ctx, "oembed_cache", thresholds=th).status is Status.INFO
