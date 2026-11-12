"""Turn check results into a reviewed SQL fix plan, and apply it on request.

Nothing here runs SQL unless ``apply_plan`` is called, and the CLI only calls
it with ``--apply`` after the user confirms a backup exists.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ._version import __version__
from .db import Database, DatabaseError
from .models import CheckResult, FixStatement
from .utils import plural


@dataclass
class FixPlan:
    database: str
    prefix: str
    host: str
    created_at: str
    statements: list[FixStatement] = field(default_factory=list)
    excluded_schema: int = 0

    @property
    def executable(self) -> list[FixStatement]:
        return [s for s in self.statements if not s.review_only]

    @property
    def review_only(self) -> list[FixStatement]:
        return [s for s in self.statements if s.review_only]


def build_plan(results: Iterable[CheckResult], *, database: str, prefix: str, host: str,
               created_at: str, include_schema: bool = False,
               include_review: bool = True) -> FixPlan:
    """Collect fix statements from results in a safe execution order.

    Schema changes (``ALTER TABLE``/``OPTIMIZE TABLE``) are left out unless
    ``include_schema`` is set, because they rewrite whole tables.
    """
    collected: list[tuple[int, int, FixStatement]] = []
    excluded = 0
    seq = 0
    for result in results:
        for fix in result.fixes:
            if fix.is_schema_change and not include_schema:
                excluded += 1
                continue
            if fix.review_only and not include_review:
                continue
            collected.append((fix.stage, seq, fix))
            seq += 1
    collected.sort(key=lambda item: (item[0], item[1]))
    return FixPlan(database=database, prefix=prefix, host=host, created_at=created_at,
                   statements=[fix for _, _, fix in collected], excluded_schema=excluded)


def backup_commands(database: str, user: str | None = None) -> list[str]:
    db = shlex.quote(database)
    user_part = f"-u {shlex.quote(user)} " if user else ""
    return [
        "wp db export before-wp-db-doctor.sql",
        f"mysqldump --single-transaction --quick --routines --triggers {user_part}-p {db} "
        "> before-wp-db-doctor.sql",
    ]


def render_sql(plan: FixPlan, user: str | None = None) -> str:
    lines = [
        f"-- wp-db-doctor {__version__} fix plan",
        f"-- Database: {plan.database} (prefix {plan.prefix}) on {plan.host}",
        f"-- Created: {plan.created_at}",
        "--",
        "-- READ EVERY STATEMENT BEFORE RUNNING IT. Direct SQL skips WordPress and plugin",
        "-- hooks. Take a backup first, for example:",
    ]
    lines.extend(f"--   {cmd}" for cmd in backup_commands(plan.database, user))
    lines.append("--")
    lines.append("-- Statements marked REVIEW are suggestions and are commented out on purpose.")
    if plan.excluded_schema:
        lines.append(f"-- {plan.excluded_schema} schema change(s) left out; add --include-schema "
                     "to see ALTER/OPTIMIZE TABLE statements.")
    if not plan.statements:
        lines.append("")
        lines.append("-- Nothing to fix.")
        return "\n".join(lines) + "\n"
    lines.append("")
    current = None
    for fix in plan.statements:
        if fix.check_id != current:
            lines.append(f"-- ===== {fix.check_id} =====")
            current = fix.check_id
        rows = (f" (about {plural(fix.estimated_rows, 'row')})"
                if fix.estimated_rows is not None else "")
        if fix.review_only:
            lines.append(f"-- REVIEW: {fix.description}{rows}")
            lines.append(f"-- {fix.sql};")
        else:
            lines.append(f"-- {fix.description}{rows}")
            lines.append(f"{fix.sql};")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


class BackupNotConfirmed(Exception):
    pass


def confirm_backup(database: str, *, assume_yes: bool, interactive: bool,
                   ask: Callable[[str], str] = input) -> None:
    """Make the user state that a backup exists before anything is applied.

    ``assume_yes`` comes from ``--i-have-a-backup``. Otherwise the user has to
    type the database name at an interactive prompt.
    """
    if assume_yes:
        return
    if not interactive:
        raise BackupNotConfirmed(
            "refusing to apply without confirmation: run interactively or pass "
            "--i-have-a-backup")
    answer = ask(
        f"Applying changes to '{database}'. Take a backup first "
        f"(e.g. `{backup_commands(database)[0]}`).\n"
        f"Type the database name to confirm a current backup exists: ")
    if answer.strip() != database:
        raise BackupNotConfirmed("confirmation did not match the database name; nothing applied")


@dataclass
class AppliedStatement:
    fix: FixStatement
    affected: int | None
    error: str | None = None


def apply_plan(db: Database, plan: FixPlan,
               progress: Callable[[AppliedStatement], None] | None = None,
               ) -> list[AppliedStatement]:
    """Execute non-review statements in order, stopping at the first error."""
    done: list[AppliedStatement] = []
    for fix in plan.executable:
        try:
            affected = db.execute(fix.sql)
        except DatabaseError as exc:
            item = AppliedStatement(fix, None, str(exc))
            done.append(item)
            if progress:
                progress(item)
            break
        item = AppliedStatement(fix, affected)
        done.append(item)
        if progress:
            progress(item)
    return done
