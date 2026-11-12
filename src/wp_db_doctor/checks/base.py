"""Check base class, the audit context and helpers shared by checks."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..config import Thresholds
from ..db import Database
from ..models import CheckResult, Status, TableInfo
from ..utils import quote_ident, to_int, to_str, validate_prefix

TABLES_SQL = (
    "/* wpdd:tables */ SELECT TABLE_NAME AS table_name, ENGINE AS engine, "
    "TABLE_COLLATION AS table_collation, TABLE_ROWS AS table_rows, "
    "DATA_LENGTH AS data_length, INDEX_LENGTH AS index_length, DATA_FREE AS data_free "
    "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
    "ORDER BY TABLE_NAME"
)

SERVER_SQL = (
    "/* wpdd:server */ SELECT VERSION() AS version, DATABASE() AS db_name, "
    "@@character_set_database AS charset, @@collation_database AS collation, "
    "@@innodb_buffer_pool_size AS buffer_pool"
)


@dataclass
class Context:
    """Everything a check needs: the connection, prefix, thresholds and the
    table list (read once from ``information_schema``)."""

    db: Database
    prefix: str
    schema: str
    thresholds: Thresholds = field(default_factory=Thresholds)
    tables: dict[str, TableInfo] = field(default_factory=dict)
    server: dict[str, Any] = field(default_factory=dict)
    constants: dict[str, Any] = field(default_factory=dict)
    today: _dt.date = field(default_factory=_dt.date.today)

    def __post_init__(self) -> None:
        validate_prefix(self.prefix)

    def name(self, base: str) -> str:
        return self.prefix + base

    def t(self, base: str) -> str:
        """Quoted name of a WordPress table, e.g. ``t('options')`` -> `wp_options`."""
        return quote_ident(self.prefix + base)

    def has(self, base: str) -> bool:
        return (self.prefix + base) in self.tables

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return self.db.query(sql, params)

    def one(self, sql: str, params: Any = None) -> dict[str, Any]:
        rows = self.db.query(sql, params)
        return rows[0] if rows else {}


def load_context(db: Database, prefix: str, schema: str | None = None,
                 thresholds: Thresholds | None = None,
                 constants: dict[str, Any] | None = None,
                 today: _dt.date | None = None) -> Context:
    server = db.query(SERVER_SQL)
    server_row = server[0] if server else {}
    schema_name = schema or to_str(server_row.get("db_name"))
    if not schema_name:
        raise ValueError("no database selected: pass --database or set DB_NAME")
    tables: dict[str, TableInfo] = {}
    for row in db.query(TABLES_SQL, (schema_name,)):
        name = to_str(row.get("table_name"))
        tables[name] = TableInfo(
            name=name,
            engine=to_str(row.get("engine")) or None,
            collation=to_str(row.get("table_collation")) or None,
            rows=to_int(row.get("table_rows")),
            data_length=to_int(row.get("data_length")),
            index_length=to_int(row.get("index_length")),
            data_free=to_int(row.get("data_free")),
        )
    return Context(
        db=db,
        prefix=prefix,
        schema=schema_name,
        thresholds=thresholds or Thresholds(),
        tables=tables,
        server=server_row,
        constants=dict(constants or {}),
        today=today or _dt.date.today(),
    )


class Check:
    """Base class. Subclasses set the class attributes and implement ``run``."""

    id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    category: ClassVar[str] = ""
    description: ClassVar[str] = ""
    requires: ClassVar[tuple[str, ...]] = ()
    fixable: ClassVar[bool] = False

    def run(self, ctx: Context) -> CheckResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def result(self, status: Status, summary: str, **kwargs: Any) -> CheckResult:
        return CheckResult(
            check_id=self.id,
            title=self.title,
            category=self.category,
            status=status,
            summary=summary,
            **kwargs,
        )

    def missing_tables(self, ctx: Context) -> list[str]:
        return [ctx.name(base) for base in self.requires if not ctx.has(base)]


def grade(count: int, warning: int, critical: int | None = None) -> Status:
    """Map a count to a status: 0 is OK, below ``warning`` is INFO."""
    if count <= 0:
        return Status.OK
    if critical is not None and count >= critical:
        return Status.CRITICAL
    if count >= warning:
        return Status.WARNING
    return Status.INFO
