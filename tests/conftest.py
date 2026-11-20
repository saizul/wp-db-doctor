"""Fake database used by the tests.

Every query the checks send starts with a ``/* wpdd:<tag> */`` comment. The
fake answers by tag, records everything it saw, and fails loudly on a query it
has no answer for, so a new query cannot slip in untested.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

import pytest

from wp_db_doctor.checks.schema import EXPECTED_INDEXES, PRIMARY_KEYS
from wp_db_doctor.db import DatabaseError

TAG_RE = re.compile(r"^/\* wpdd:([\w:]+) \*/ ")

TODAY = dt.date(2026, 11, 12)

Response = Any  # list of rows, or callable(sql, params) -> list of rows


class FakeDatabase:
    def __init__(self, responses: dict[str, Response]) -> None:
        self.responses = dict(responses)
        self.queries: list[tuple[str, Any]] = []
        self.executed: list[str] = []
        self.rowcount = 7
        self.fail_execute_on: str | None = None
        self.closed = False

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        m = TAG_RE.match(sql)
        assert m, f"query without a wpdd tag: {sql}"
        if params is not None:
            # With parameters the driver %-formats the text: a stray % breaks it.
            assert "%" not in sql.replace("%s", ""), sql
        tag = m.group(1)
        self.queries.append((tag, params))
        if tag not in self.responses:
            raise AssertionError(f"no fake response for query tag {tag!r}")
        response = self.responses[tag]
        if isinstance(response, Exception):
            raise response
        rows = response(sql, params) if callable(response) else response
        # The Database contract (see DBAPIDatabase) returns lowercase column names.
        return [{str(k).lower(): v for k, v in r.items()} for r in rows]

    def execute(self, sql: str, params: Any = None) -> int:
        if self.fail_execute_on and self.fail_execute_on in sql:
            raise DatabaseError("MySQL error 1205: Lock wait timeout exceeded")
        self.executed.append(sql)
        return self.rowcount

    def close(self) -> None:
        self.closed = True

    def tags(self) -> list[str]:
        return [t for t, _ in self.queries]


CORE = ("options", "posts", "postmeta", "users", "usermeta", "terms", "termmeta",
        "term_taxonomy", "term_relationships", "comments", "commentmeta", "links")

MB = 1024 * 1024


def table_row(name: str, engine: str = "InnoDB", collation: str = "utf8mb4_unicode_520_ci",
              rows: int = 100, data: int = 16384, index: int = 16384,
              free: int = 0) -> dict[str, Any]:
    # Uppercase keys as MySQL 8 returns them from information_schema.
    return {"TABLE_NAME": name, "ENGINE": engine, "TABLE_COLLATION": collation,
            "TABLE_ROWS": rows, "DATA_LENGTH": data, "INDEX_LENGTH": index, "DATA_FREE": free}


def index_rows(prefix: str, tables: list[str], drop: tuple[tuple[str, str], ...] = ()):
    rows = []
    for base, (cols, _) in PRIMARY_KEYS.items():
        if prefix + base in tables and (base, "PRIMARY") not in drop:
            for seq, col in enumerate(cols, 1):
                rows.append({"table_name": prefix + base, "index_name": "PRIMARY",
                             "seq": seq, "column_name": col, "non_unique": 0})
    for e in EXPECTED_INDEXES:
        if prefix + e.table in tables and (e.table, e.name) not in drop:
            for seq, col in enumerate(e.column_names(), 1):
                rows.append({"table_name": prefix + e.table, "index_name": e.name,
                             "seq": seq, "column_name": col, "non_unique": int(not e.unique)})
    return rows


def autoinc_rows(prefix: str, tables: list[str], drop: tuple[str, ...] = ()):
    return [{"table_name": prefix + base, "column_name": cols[0], "extra": "auto_increment"}
            for base, (cols, ai) in PRIMARY_KEYS.items()
            if ai and prefix + base in tables and base not in drop]


def clean_responses(prefix: str = "wp_", extra: tuple[str, ...] = (),
                    version: str = "10.11.8-MariaDB") -> dict[str, Response]:
    names = [prefix + t for t in CORE + extra]
    return {
        "server": [{"version": version, "db_name": "wordpress", "charset": "utf8mb4",
                    "collation": "utf8mb4_unicode_520_ci", "buffer_pool": 1024 * MB}],
        "tables": [table_row(n) for n in names],
        "autoload_total": [{"option_count": 310, "total_bytes": 220 * 1024}],
        "autoload_top": [{"option_name": "rewrite_rules", "bytes": 40 * 1024},
                         {"option_name": "wp_user_roles", "bytes": 12 * 1024}],
        "transients": [{"expired": 0, "expired_site": 0, "transients": 12,
                        "site_transients": 4, "transient_bytes": 9000}],
        "revisions": [{"revisions": 0, "content_bytes": 0, "parents": 0, "old_revisions": 0}],
        "revisions_top": [],
        "auto_drafts": [{"total": 1, "old": 0}],
        "trash": [],
        "comments": [],
        "oembed": [{"total": 12, "failed": 0, "bytes": 9000}],
        "oembed_meta": [{"total": 0, "failed": 0, "bytes": 0}],
        "orphans:postmeta": [{"orphans": 0, "bytes": 0}],
        "orphans:termmeta": [{"orphans": 0, "bytes": 0}],
        "orphans:commentmeta": [{"orphans": 0, "bytes": 0}],
        "orphans:usermeta": [{"orphans": 0, "bytes": 0}],
        "orphans:term_relationships": [{"orphans": 0}],
        "orphans:term_relationships_tax": [{"orphans": 0}],
        "indexes": index_rows(prefix, names),
        "autoinc": autoinc_rows(prefix, names),
    }


def bloated_responses(prefix: str = "wp_") -> dict[str, Response]:
    extra = ("actionscheduler_actions", "actionscheduler_logs", "actionscheduler_claims",
             "woocommerce_sessions", "yoast_indexable")
    r = clean_responses(prefix, extra, version="5.7.44-log")
    names = [prefix + t for t in CORE + extra]
    tables = []
    for n in names:
        if n == prefix + "postmeta":
            tables.append(table_row(n, rows=2_400_000, data=900 * MB, index=300 * MB,
                                    free=120 * MB))
        elif n == prefix + "actionscheduler_logs":
            tables.append(table_row(n, rows=1_800_000, data=400 * MB, index=80 * MB))
        elif n == prefix + "yoast_indexable":
            tables.append(table_row(n, engine="MyISAM", collation="utf8_general_ci"))
        elif n == prefix + "links":
            tables.append(table_row(n, collation="utf8mb4_general_ci"))
        else:
            tables.append(table_row(n))
    tables.append(table_row("old_options"))
    tables.append(table_row("old_posts"))
    r["tables"] = tables
    r["autoload_total"] = [{"option_count": 1450, "total_bytes": 4 * MB}]
    r["autoload_top"] = [
        {"option_name": "wpseo_premium_redirects_export", "bytes": 1600 * 1024},
        {"option_name": "cron", "bytes": 900 * 1024},
        {"option_name": "_transient_feed_abc", "bytes": 300 * 1024},
        {"option_name": "old_plugin_cache", "bytes": 200 * 1024},
        {"option_name": "small_one", "bytes": 1024},
    ]
    r["transients"] = [{"expired": 1800, "expired_site": 25, "transients": 9000,
                        "site_transients": 120, "transient_bytes": 30 * MB}]
    r["revisions"] = [{"revisions": 25000, "content_bytes": 300 * MB, "parents": 800,
                       "old_revisions": 21000}]
    r["revisions_top"] = [
        {"parent_id": 12, "total": 640, "title": "Home", "parent_type": "page"},
        {"parent_id": 99, "total": 300, "title": None, "parent_type": None},
    ]
    r["auto_drafts"] = [{"total": 420, "old": 400}]
    r["trash"] = [{"post_type": "post", "total": 150, "old": 120},
                  {"post_type": "shop_order", "total": 90, "old": 80}]
    r["comments"] = [{"comment_approved": "spam", "total": 60000},
                     {"comment_approved": "trash", "total": 40},
                     {"comment_approved": "0", "total": 3}]
    r["oembed"] = [{"total": 4200, "failed": 900, "bytes": 6 * MB}]
    r["oembed_meta"] = [{"total": 1500, "failed": 300, "bytes": 2 * MB}]
    r["orphans:postmeta"] = [{"orphans": 54000, "bytes": 12 * MB}]
    r["orphans_top:postmeta"] = [{"meta_key": "_edit_lock", "total": 30000},
                                 {"meta_key": "_elementor_data", "total": 24000}]
    r["orphans:termmeta"] = [{"orphans": 3, "bytes": 90}]
    r["orphans_top:termmeta"] = [{"meta_key": "order", "total": 3}]
    r["orphans:term_relationships"] = [{"orphans": 1200}]
    r["orphans:term_relationships_tax"] = [{"orphans": 4}]
    r["as_status"] = [
        {"status": "complete", "total": 600000, "old": 580000, "overdue": 599000},
        {"status": "failed", "total": 3000, "old": 2500, "overdue": 3000},
        {"status": "pending", "total": 500, "old": 0, "overdue": 450},
    ]
    r["as_failed_hooks"] = [{"hook": "woocommerce_run_product_attribute_lookup_update",
                             "total": 2900}]
    r["wc_sessions"] = [{"sessions": 9000, "expired": 7000, "bytes": 40 * MB}]
    r["indexes"] = index_rows(prefix, names, drop=(("postmeta", "meta_key"),
                                                    ("options", "PRIMARY")))
    r["autoinc"] = autoinc_rows(prefix, names, drop=("posts",))
    return r


@pytest.fixture
def clean_db() -> FakeDatabase:
    return FakeDatabase(clean_responses())


@pytest.fixture
def bloated_db() -> FakeDatabase:
    return FakeDatabase(bloated_responses())


@pytest.fixture
def make_ctx() -> Callable[..., Any]:
    from wp_db_doctor.checks import load_context

    def factory(db: FakeDatabase, prefix: str = "wp_", **kwargs: Any):
        kwargs.setdefault("today", TODAY)
        return load_context(db, prefix, schema="wordpress", **kwargs)

    return factory
