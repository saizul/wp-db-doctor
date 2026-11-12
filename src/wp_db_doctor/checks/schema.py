"""Schema checks: indexes, primary keys, engines, collations, sizes, prefix
and server version."""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter
from dataclasses import dataclass

from ..models import STAGE_OPTIMIZE, STAGE_SCHEMA, CheckResult, FixKind, FixStatement, Status
from ..utils import format_bytes, format_int, plural, quote_ident, to_int, to_str
from .base import Check, Context


@dataclass(frozen=True)
class ExpectedIndex:
    table: str  # without prefix
    name: str
    columns: tuple[tuple[str, int | None], ...]  # (column, prefix length)
    severity: Status = Status.WARNING
    unique: bool = False

    def column_names(self) -> tuple[str, ...]:
        return tuple(c for c, _ in self.columns)

    def ddl(self, ctx: Context) -> str:
        cols = ", ".join(
            quote_ident(c) + (f"({n})" if n else "") for c, n in self.columns)
        kind = "UNIQUE INDEX" if self.unique else "INDEX"
        return f"ALTER TABLE {ctx.t(self.table)} ADD {kind} {quote_ident(self.name)} ({cols})"


# Secondary indexes from wp-admin/includes/schema.php (and plugin tables that
# commonly lose them in migrations). 191 is the utf8mb4 prefix length WordPress uses.
EXPECTED_INDEXES: tuple[ExpectedIndex, ...] = (
    ExpectedIndex("postmeta", "post_id", (("post_id", None),)),
    ExpectedIndex("postmeta", "meta_key", (("meta_key", 191),)),
    ExpectedIndex("usermeta", "user_id", (("user_id", None),)),
    ExpectedIndex("usermeta", "meta_key", (("meta_key", 191),)),
    ExpectedIndex("termmeta", "term_id", (("term_id", None),)),
    ExpectedIndex("termmeta", "meta_key", (("meta_key", 191),)),
    ExpectedIndex("commentmeta", "comment_id", (("comment_id", None),)),
    ExpectedIndex("commentmeta", "meta_key", (("meta_key", 191),)),
    ExpectedIndex("options", "option_name", (("option_name", None),), Status.CRITICAL, True),
    ExpectedIndex("options", "autoload", (("autoload", None),), Status.INFO),
    ExpectedIndex("posts", "type_status_date",
                  (("post_type", None), ("post_status", None), ("post_date", None),
                   ("ID", None))),
    ExpectedIndex("posts", "post_name", (("post_name", 191),)),
    ExpectedIndex("posts", "post_parent", (("post_parent", None),)),
    ExpectedIndex("posts", "post_author", (("post_author", None),), Status.INFO),
    ExpectedIndex("comments", "comment_post_ID", (("comment_post_ID", None),)),
    ExpectedIndex("comments", "comment_approved_date_gmt",
                  (("comment_approved", None), ("comment_date_gmt", None)), Status.INFO),
    ExpectedIndex("comments", "comment_parent", (("comment_parent", None),), Status.INFO),
    ExpectedIndex("term_relationships", "term_taxonomy_id", (("term_taxonomy_id", None),)),
    ExpectedIndex("term_taxonomy", "taxonomy", (("taxonomy", None),), Status.INFO),
    ExpectedIndex("users", "user_login_key", (("user_login", None),)),
    ExpectedIndex("users", "user_email", (("user_email", None),), Status.INFO),
    ExpectedIndex("actionscheduler_actions", "hook", (("hook", 163),), Status.INFO),
    ExpectedIndex("actionscheduler_actions", "status", (("status", None),), Status.INFO),
    ExpectedIndex("actionscheduler_actions", "scheduled_date_gmt",
                  (("scheduled_date_gmt", None),), Status.INFO),
    ExpectedIndex("woocommerce_sessions", "session_key", (("session_key", None),),
                  Status.WARNING, True),
)

# Primary key column per core table; it must also be AUTO_INCREMENT where noted.
PRIMARY_KEYS: dict[str, tuple[tuple[str, ...], bool]] = {
    "posts": (("ID",), True),
    "postmeta": (("meta_id",), True),
    "options": (("option_id",), True),
    "users": (("ID",), True),
    "usermeta": (("umeta_id",), True),
    "terms": (("term_id",), True),
    "termmeta": (("meta_id",), True),
    "term_taxonomy": (("term_taxonomy_id",), True),
    "term_relationships": (("object_id", "term_taxonomy_id"), False),
    "comments": (("comment_ID",), True),
    "commentmeta": (("meta_id",), True),
    "links": (("link_id",), True),
}


class MissingIndexesCheck(Check):
    id = "missing_indexes"
    title = "Missing indexes and primary keys"
    category = "schema"
    description = ("Compares indexes on core and common plugin tables with the WordPress "
                   "schema. Missing keys are a classic result of broken migrations.")
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        bases = sorted({e.table for e in EXPECTED_INDEXES} | set(PRIMARY_KEYS))
        present = [ctx.name(b) for b in bases if ctx.has(b)]
        if not present:
            return self.result(Status.SKIPPED, "no WordPress tables found for this prefix")
        placeholders = ", ".join(["%s"] * len(present))
        stats = ctx.query(
            "/* wpdd:indexes */ SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, "
            "SEQ_IN_INDEX AS seq, COLUMN_NAME AS column_name, NON_UNIQUE AS non_unique "
            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN "
            f"({placeholders}) ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
            (ctx.schema, *present),
        )
        indexes: dict[str, dict[str, list[str]]] = {}
        for r in stats:
            table = to_str(r.get("table_name"))
            index = to_str(r.get("index_name"))
            indexes.setdefault(table, {}).setdefault(index, []).append(
                to_str(r.get("column_name")).lower())
        columns = ctx.query(
            "/* wpdd:autoinc */ SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
            "EXTRA AS extra FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
            f"AND TABLE_NAME IN ({placeholders}) AND EXTRA LIKE %s",
            (ctx.schema, *present, "%auto_increment%"),
        )
        autoinc = {(to_str(r.get("table_name")), to_str(r.get("column_name")).lower())
                   for r in columns}

        rows: list[list[str]] = []
        fixes: list[FixStatement] = []
        worst = Status.OK
        for base, (pk_cols, needs_ai) in PRIMARY_KEYS.items():
            table = ctx.name(base)
            if table not in present:
                continue
            primary = indexes.get(table, {}).get("PRIMARY")
            want = [c.lower() for c in pk_cols]
            if primary != want:
                rows.append([table, "PRIMARY KEY", "(" + ", ".join(pk_cols) + ")", "critical"])
                worst = Status.CRITICAL
                cols = ", ".join(quote_ident(c) for c in pk_cols)
                fixes.append(FixStatement(
                    check_id=self.id,
                    description=f"{table} has no primary key on ({', '.join(pk_cols)}). "
                                "Remove duplicate and zero IDs first, then add it",
                    sql=f"ALTER TABLE {ctx.t(base)} ADD PRIMARY KEY ({cols})",
                    kind=FixKind.DDL,
                    stage=STAGE_SCHEMA,
                    review_only=True,
                ))
            elif needs_ai and (table, want[0]) not in autoinc:
                rows.append([table, "AUTO_INCREMENT", pk_cols[0], "critical"])
                worst = Status.CRITICAL
                fixes.append(FixStatement(
                    check_id=self.id,
                    description=f"{table}.{pk_cols[0]} is not AUTO_INCREMENT; new rows get "
                                "ID 0. Check the column type before running",
                    sql=(f"ALTER TABLE {ctx.t(base)} MODIFY {quote_ident(pk_cols[0])} "
                         "BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT"),
                    kind=FixKind.DDL,
                    stage=STAGE_SCHEMA,
                    review_only=True,
                ))
        for expected in EXPECTED_INDEXES:
            table = ctx.name(expected.table)
            if table not in present:
                continue
            want = [c.lower() for c in expected.column_names()]
            have = indexes.get(table, {})
            if any(cols[: len(want)] == want for cols in have.values()):
                continue
            rows.append([table, expected.name, "(" + ", ".join(expected.column_names()) + ")",
                         expected.severity.value])
            if expected.severity.rank > worst.rank:
                worst = expected.severity
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Add index {expected.name} on {table}",
                sql=expected.ddl(ctx),
                kind=FixKind.DDL,
                stage=STAGE_SCHEMA,
                review_only=expected.unique,
            ))
        recommendations = []
        if worst is Status.CRITICAL:
            recommendations.append(
                "Missing primary keys or AUTO_INCREMENT usually come from an export/import "
                "that dropped table definitions. New posts and options get ID 0 and "
                "overwrite each other; fix this before anything else.")
        if rows:
            recommendations.append(
                "Adding an index rewrites the table. On large tables run it in a quiet "
                "period, or use an online schema change tool.")
        if any(r[1] == "option_name" for r in rows):
            recommendations.append(
                "Without a unique option_name, duplicate options may exist. Check with "
                "SELECT option_name, COUNT(*) FROM wp_options GROUP BY option_name "
                "HAVING COUNT(*) > 1 before adding the index.")
        summary = ("all expected keys and indexes are present" if not rows
                   else f"{plural(len(rows), 'missing key')} on "
                        f"{plural(len({r[0] for r in rows}), 'table')}")
        return self.result(
            worst, summary,
            metrics={"missing": len(rows)},
            columns=["table", "index", "columns", "severity"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


DEFAULT_COLLATION = "utf8mb4_unicode_520_ci"


class EngineCollationCheck(Check):
    id = "engine_collation"
    title = "Table engines and collations"
    category = "schema"
    description = ("Non-InnoDB tables, tables not using utf8mb4, and collations that differ "
                   "between tables (which breaks joins and comparisons).")
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        tables = [t for name, t in sorted(ctx.tables.items()) if name.startswith(ctx.prefix)]
        if not tables:
            return self.result(Status.SKIPPED, "no tables with this prefix")
        engines = Counter((t.engine or "unknown") for t in tables)
        collations = Counter(t.collation for t in tables if t.collation)
        configured = ctx.constants.get("DB_COLLATE") or None
        utf8mb4 = [c for c, _ in collations.most_common() if c.startswith("utf8mb4")]
        target = configured or (utf8mb4[0] if utf8mb4 else DEFAULT_COLLATION)
        rows: list[list[str]] = []
        fixes: list[FixStatement] = []
        status = Status.OK
        non_innodb = [t for t in tables if (t.engine or "").lower() != "innodb"
                      and t.engine is not None]
        for t in non_innodb:
            rows.append([t.name, t.engine or "", t.collation or "", "not InnoDB"])
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Convert {t.name} from {t.engine} to InnoDB "
                            f"({format_bytes(t.size)}, locks the table while it runs)",
                sql=f"ALTER TABLE {quote_ident(t.name)} ENGINE=InnoDB",
                kind=FixKind.DDL,
                stage=STAGE_SCHEMA,
            ))
        if non_innodb:
            status = Status.WARNING
        not_mb4 = [t for t in tables if t.charset and t.charset != "utf8mb4"]
        mismatched = [t for t in tables if t.collation and t.collation != target
                      and t not in not_mb4]
        for t in not_mb4:
            rows.append([t.name, t.engine or "", t.collation or "", "not utf8mb4"])
        for t in mismatched:
            rows.append([t.name, t.engine or "", t.collation or "", f"differs from {target}"])
        for t in not_mb4 + mismatched:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Convert {t.name} to utf8mb4 / {target}. Converting rewrites "
                            "every text column; test on a copy first",
                sql=(f"ALTER TABLE {quote_ident(t.name)} CONVERT TO CHARACTER SET utf8mb4 "
                     f"COLLATE {target}"),
                kind=FixKind.DDL,
                stage=STAGE_SCHEMA,
                review_only=True,
            ))
        if not_mb4:
            status = Status.WARNING
        elif mismatched and status is Status.OK:
            status = Status.INFO
        recommendations = []
        if non_innodb:
            recommendations.append(
                "MyISAM/Aria tables lock the whole table on every write and are not crash "
                "safe. InnoDB is the default since MySQL 5.5 and is what WordPress expects.")
        if not_mb4:
            recommendations.append(
                "Tables in utf8 (utf8mb3) or latin1 cannot store emoji and many scripts. "
                "WordPress converts core tables to utf8mb4 on upgrade only when the server "
                "supports it; plugin tables often stay behind.")
        if mismatched:
            recommendations.append(
                "Mixed collations cause 'Illegal mix of collations' errors in joins "
                "between plugin and core tables.")
        summary_bits = []
        summary_bits.append(", ".join(f"{n} {e}" for e, n in engines.most_common()))
        summary_bits.append(plural(len(collations), "collation"))
        return self.result(
            status,
            "; ".join(summary_bits) + (f" (target {target})" if rows else ""),
            metrics={"engines": dict(engines), "collations": dict(collations),
                     "target_collation": target},
            columns=["table", "engine", "collation", "problem"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


class TableSizesCheck(Check):
    id = "table_sizes"
    title = "Largest tables"
    category = "schema"
    description = ("The largest tables in the schema, tables above the size threshold and "
                   "tables with reclaimable free space.")
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        tables = sorted(ctx.tables.values(), key=lambda t: t.size, reverse=True)
        if not tables:
            return self.result(Status.SKIPPED, "the schema has no tables")
        large = th.large_table_mb * 1024 * 1024
        overhead = th.table_overhead_mb * 1024 * 1024
        total = sum(t.size for t in tables)
        rows = []
        for t in tables[: th.tables_top]:
            note = []
            if t.size >= large:
                note.append("large")
            if t.data_free >= overhead:
                note.append(f"{format_bytes(t.data_free)} free")
            if not t.name.startswith(ctx.prefix):
                note.append("other prefix")
            rows.append([t.name, format_bytes(t.size), format_bytes(t.data_length),
                         format_bytes(t.index_length), f"~{format_int(t.rows)}",
                         ", ".join(note)])
        oversized = [t for t in tables if t.size >= large]
        fragmented = [t for t in tables if t.data_free >= overhead]
        fixes = [FixStatement(
            check_id=self.id,
            description=f"Rebuild {t.name} to reclaim about {format_bytes(t.data_free)}",
            sql=f"OPTIMIZE TABLE {quote_ident(t.name)}",
            kind=FixKind.OPTIMIZE,
            stage=STAGE_OPTIMIZE,
        ) for t in fragmented]
        status = Status.OK
        if fragmented:
            status = Status.INFO
        if oversized:
            status = Status.WARNING
        recommendations = []
        if oversized:
            names = ", ".join(t.name for t in oversized[:5])
            recommendations.append(
                f"Large tables: {names}. Find out what fills them (logs, sessions, "
                "analytics and security plugins are common) before deleting anything.")
        if fragmented:
            recommendations.append(
                "OPTIMIZE TABLE rebuilds the table to release free space. Run it after the "
                "cleanup statements, when traffic is low; it copies the table.")
        others = [t for t in tables if not t.name.startswith(ctx.prefix)]
        if others:
            recommendations.append(
                f"{plural(len(others), 'table')} in this schema do not use the {ctx.prefix} "
                "prefix (another site, an old install or a non-WordPress app).")
        return self.result(
            status,
            f"{plural(len(tables), 'table')}, {format_bytes(total)} total; "
            f"{format_int(len(oversized))} over {th.large_table_mb} MB",
            metrics={"tables": len(tables), "total_bytes": total,
                     "oversized": [t.name for t in oversized],
                     "fragmented": [t.name for t in fragmented]},
            columns=["table", "total", "data", "index", "rows", "note"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


CORE_TABLES = ("options", "posts", "postmeta", "users", "usermeta", "terms", "termmeta",
               "term_taxonomy", "term_relationships", "comments", "commentmeta", "links")


def detect_prefixes(table_names: list[str]) -> list[str]:
    """Prefixes that have both an ``options`` and a ``posts`` table."""
    names = set(table_names)
    found = []
    for name in names:
        if name.endswith("options"):
            prefix = name[: -len("options")]
            if prefix + "posts" in names:
                found.append(prefix)
    return sorted(found)


class TablePrefixCheck(Check):
    id = "table_prefix"
    title = "Table prefix"
    category = "schema"
    description = ("Confirms the prefix matches the tables, finds other WordPress installs "
                   "sharing the schema, multisite sub-sites and missing core tables.")

    def run(self, ctx: Context) -> CheckResult:
        prefixes = detect_prefixes(list(ctx.tables))
        subsite_re = re.compile(re.escape(ctx.prefix) + r"(\d+)_$")
        subsites = sorted((p for p in prefixes if subsite_re.match(p)),
                          key=lambda p: int(subsite_re.match(p).group(1)))  # type: ignore
        others = [p for p in prefixes if p != ctx.prefix and p not in subsites]
        metrics = {"prefix": ctx.prefix, "subsites": len(subsites), "other_prefixes": others}
        rows = [[p, "this site" if p == ctx.prefix else
                 ("multisite sub-site" if p in subsites else "other install")]
                for p in prefixes]
        if ctx.prefix not in prefixes:
            hint = f"; found: {', '.join(prefixes)}" if prefixes else ""
            return self.result(
                Status.CRITICAL,
                f"no WordPress tables use the prefix {ctx.prefix!r}{hint}",
                metrics=metrics, columns=["prefix", "role"], rows=rows,
                recommendations=["Pass the right prefix with --prefix, or point --wp-config "
                                 "at the site's wp-config.php."])
        missing = [ctx.name(b) for b in CORE_TABLES if not ctx.has(b)]
        recommendations = []
        status = Status.OK
        if missing:
            status = Status.WARNING
            recommendations.append(
                f"Core tables are missing: {', '.join(missing)}. Visit wp-admin/upgrade.php "
                "or run `wp core update-db` to recreate them.")
        if others:
            status = Status.WARNING if status is Status.OK else status
            recommendations.append(
                f"Other WordPress installs share this database ({', '.join(others)}). If they "
                "are leftovers, export them and drop their tables; shared schemas make "
                "backups and restores riskier.")
        if ctx.prefix == "wp_" and status is Status.OK:
            status = Status.INFO
            recommendations.append(
                "The default wp_ prefix is fine. A custom prefix only slows down automated "
                "SQL injection payloads; renaming it on a live site is risky and not a fix "
                "for an injectable plugin.")
        if subsites:
            recommendations.append(
                f"This is a multisite network with {plural(len(subsites), 'sub-site')}. "
                f"Checks cover {ctx.prefix}* main-site tables; audit a sub-site with "
                f"--prefix {subsites[0]}.")
        summary = f"prefix {ctx.prefix}"
        if subsites:
            summary += f", multisite with {plural(len(subsites), 'sub-site')}"
        if others:
            summary += f", {plural(len(others), 'other install')} in the same schema"
        if missing:
            summary += f", {plural(len(missing), 'core table')} missing"
        return self.result(status, summary, metrics=metrics, columns=["prefix", "role"],
                           rows=rows, recommendations=recommendations)


# (product, major.minor) -> end of life. Sources: MySQL and MariaDB release policies.
EOL: dict[tuple[str, tuple[int, int]], _dt.date] = {
    ("mysql", (5, 6)): _dt.date(2021, 2, 1),
    ("mysql", (5, 7)): _dt.date(2023, 10, 31),
    ("mysql", (8, 0)): _dt.date(2026, 4, 30),
    ("mysql", (8, 4)): _dt.date(2032, 4, 30),
    ("mariadb", (10, 3)): _dt.date(2023, 5, 25),
    ("mariadb", (10, 4)): _dt.date(2024, 6, 18),
    ("mariadb", (10, 5)): _dt.date(2025, 6, 24),
    ("mariadb", (10, 6)): _dt.date(2026, 7, 6),
    ("mariadb", (10, 11)): _dt.date(2028, 2, 16),
    ("mariadb", (11, 4)): _dt.date(2029, 5, 29),
    ("mariadb", (11, 8)): _dt.date(2028, 6, 4),
}


def parse_server_version(version: str) -> tuple[str, tuple[int, int, int]]:
    product = "mariadb" if "mariadb" in version.lower() else "mysql"
    text = version
    # MariaDB 10.x used to report "5.5.5-10.x.y-MariaDB" through some clients.
    if text.startswith("5.5.5-"):
        text = text[6:]
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return product, (0, 0, 0)
    return product, (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


class ServerVersionCheck(Check):
    id = "server_version"
    title = "Database server"
    category = "server"
    description = ("Server product and version against its end-of-life date, and the InnoDB "
                   "buffer pool against the data size.")

    def run(self, ctx: Context) -> CheckResult:
        version = to_str(ctx.server.get("version"))
        if not version:
            return self.result(Status.SKIPPED, "server version not available")
        product, ver = parse_server_version(version)
        label = "MariaDB" if product == "mariadb" else "MySQL"
        status = Status.OK
        recommendations = []
        eol = EOL.get((product, ver[:2]))
        known = sorted(mm for (p, mm) in EOL if p == product)
        ancient = eol is None and bool(known) and ver[:2] < known[0]
        if ancient:
            status = Status.WARNING
            recommendations.append(
                f"{label} {ver[0]}.{ver[1]} is long past end of life and no longer gets "
                "security fixes. Plan an upgrade to a supported LTS release.")
        elif eol is not None and eol <= ctx.today:
            status = Status.WARNING
            recommendations.append(
                f"{label} {ver[0]}.{ver[1]} reached end of life on {eol.isoformat()} and no "
                "longer gets security fixes. Plan an upgrade to a supported LTS release.")
        buffer_pool = to_int(ctx.server.get("buffer_pool"))
        innodb = sum(t.size for t in ctx.tables.values() if (t.engine or "").lower() == "innodb")
        if buffer_pool and innodb > buffer_pool:
            if status is Status.OK:
                status = Status.INFO
            recommendations.append(
                f"innodb_buffer_pool_size is {format_bytes(buffer_pool)} but InnoDB data and "
                f"indexes take {format_bytes(innodb)}. On a dedicated database server give "
                "the buffer pool 50-70% of RAM.")
        summary = f"{label} {ver[0]}.{ver[1]}.{ver[2]}"
        if eol is not None:
            summary += f" (end of life {eol.isoformat()})"
        elif ancient:
            summary += " (end of life)"
        return self.result(
            status, summary,
            metrics={"product": product, "version": version,
                     "end_of_life": eol.isoformat() if eol else None,
                     "buffer_pool_bytes": buffer_pool, "innodb_bytes": innodb},
            recommendations=recommendations,
        )
