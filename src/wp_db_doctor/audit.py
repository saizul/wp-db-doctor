"""Run an audit against a database connection and build the report."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from typing import Any

from ._version import __version__
from .checks import load_context, run_checks, select_checks
from .config import Thresholds
from .db import Database
from .models import DatabaseInfo, Report
from .utils import to_str


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def run_audit(db: Database, *, prefix: str, schema: str | None = None,
              host_label: str = "", thresholds: Thresholds | None = None,
              only: Iterable[str] = (), skip: Iterable[str] = (),
              constants: dict[str, Any] | None = None,
              generated_at: str | None = None,
              today: _dt.date | None = None) -> Report:
    checks = select_checks(only, skip)
    ctx = load_context(db, prefix, schema=schema, thresholds=thresholds,
                       constants=constants, today=today)
    results = run_checks(checks, ctx)
    info = DatabaseInfo(
        name=ctx.schema,
        host=host_label,
        prefix=prefix,
        server_version=to_str(ctx.server.get("version")),
        charset=to_str(ctx.server.get("charset")) or None,
        collation=to_str(ctx.server.get("collation")) or None,
        table_count=len(ctx.tables),
        total_size=sum(t.size for t in ctx.tables.values()),
    )
    return Report(tool_version=__version__, generated_at=generated_at or utc_now(),
                  database=info, results=results)
