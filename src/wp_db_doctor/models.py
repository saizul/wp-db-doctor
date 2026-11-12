"""Data structures shared by checks, fix planning and report renderers."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Status(str, enum.Enum):
    """Outcome of a single check, ordered by how much attention it needs."""

    OK = "ok"
    SKIPPED = "skipped"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @classmethod
    def parse(cls, value: str) -> Status:
        try:
            return cls(value.lower())
        except ValueError:
            names = ", ".join(s.value for s in cls)
            raise ValueError(f"unknown status {value!r} (expected one of: {names})") from None


_RANK = {
    Status.OK: 0,
    Status.SKIPPED: 0,
    Status.INFO: 1,
    Status.WARNING: 2,
    Status.ERROR: 2,
    Status.CRITICAL: 3,
}


class FixKind(str, enum.Enum):
    DELETE = "delete"
    UPDATE = "update"
    DDL = "ddl"
    OPTIMIZE = "optimize"


# Fix statements run in stage order: content first, then the metadata that the
# content deletes leave behind, then schema work that rewrites whole tables.
STAGE_CONTENT = 10
STAGE_COMMENTS = 20
STAGE_OPTIONS = 30
STAGE_QUEUES = 40
STAGE_ORPHANS = 50
STAGE_SCHEMA = 60
STAGE_OPTIMIZE = 70


@dataclass
class FixStatement:
    """One SQL statement proposed by a check.

    ``review_only`` statements are written to the fix plan commented out: they
    are suggestions that need a human decision (for example turning off
    autoload for a plugin option) and are never executed by ``--apply``.
    """

    check_id: str
    description: str
    sql: str
    kind: FixKind = FixKind.DELETE
    stage: int = STAGE_CONTENT
    estimated_rows: int | None = None
    review_only: bool = False

    @property
    def is_schema_change(self) -> bool:
        return self.kind in (FixKind.DDL, FixKind.OPTIMIZE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "description": self.description,
            "sql": self.sql,
            "kind": self.kind.value,
            "stage": self.stage,
            "estimated_rows": self.estimated_rows,
            "review_only": self.review_only,
        }


@dataclass
class CheckResult:
    check_id: str
    title: str
    category: str
    status: Status
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    fixes: list[FixStatement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "title": self.title,
            "category": self.category,
            "status": self.status.value,
            "summary": self.summary,
            "metrics": self.metrics,
            "details": {"columns": self.columns, "rows": self.rows},
            "recommendations": self.recommendations,
            "fixes": [f.to_dict() for f in self.fixes],
        }


@dataclass
class TableInfo:
    name: str
    engine: str | None
    collation: str | None
    rows: int
    data_length: int
    index_length: int
    data_free: int

    @property
    def size(self) -> int:
        return self.data_length + self.index_length

    @property
    def charset(self) -> str | None:
        if not self.collation:
            return None
        return self.collation.split("_", 1)[0]


@dataclass
class DatabaseInfo:
    name: str
    host: str
    prefix: str
    server_version: str = ""
    charset: str | None = None
    collation: str | None = None
    table_count: int = 0
    total_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "prefix": self.prefix,
            "server_version": self.server_version,
            "charset": self.charset,
            "collation": self.collation,
            "table_count": self.table_count,
            "total_size_bytes": self.total_size,
        }


@dataclass
class Report:
    tool_version: str
    generated_at: str
    database: DatabaseInfo
    results: list[CheckResult]

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for result in self.results:
            counts[result.status.value] += 1
        return counts

    @property
    def worst(self) -> Status:
        worst = Status.OK
        for result in self.results:
            if result.status.rank > worst.rank:
                worst = result.status
        return worst

    def fixes(self) -> list[FixStatement]:
        return [fix for result in self.results for fix in result.fixes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "tool": {"name": "wp-db-doctor", "version": self.tool_version},
            "generated_at": self.generated_at,
            "database": self.database.to_dict(),
            "summary": self.counts(),
            "checks": [r.to_dict() for r in self.results],
        }
