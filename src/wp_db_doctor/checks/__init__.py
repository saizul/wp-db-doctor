"""Check registry and runner."""

from __future__ import annotations

from collections.abc import Iterable

from ..db import DatabaseError
from ..models import CheckResult, Status
from .base import Check, Context, load_context
from .content import (
    AutoDraftsCheck,
    OembedCacheCheck,
    RevisionsCheck,
    SpamCommentsCheck,
    TrashedPostsCheck,
)
from .options import AutoloadCheck, ExpiredTransientsCheck
from .orphans import (
    OrphanedCommentmetaCheck,
    OrphanedPostmetaCheck,
    OrphanedTermmetaCheck,
    OrphanedTermRelationshipsCheck,
    OrphanedUsermetaCheck,
)
from .queues import ActionSchedulerCheck, WooCommerceSessionsCheck
from .schema import (
    EngineCollationCheck,
    MissingIndexesCheck,
    ServerVersionCheck,
    TablePrefixCheck,
    TableSizesCheck,
)

# Report order: identity first, then bloat, then schema.
ALL_CHECKS: tuple[type[Check], ...] = (
    TablePrefixCheck,
    ServerVersionCheck,
    AutoloadCheck,
    ExpiredTransientsCheck,
    RevisionsCheck,
    AutoDraftsCheck,
    TrashedPostsCheck,
    SpamCommentsCheck,
    OembedCacheCheck,
    OrphanedPostmetaCheck,
    OrphanedTermmetaCheck,
    OrphanedCommentmetaCheck,
    OrphanedUsermetaCheck,
    OrphanedTermRelationshipsCheck,
    ActionSchedulerCheck,
    WooCommerceSessionsCheck,
    MissingIndexesCheck,
    EngineCollationCheck,
    TableSizesCheck,
)

CHECKS_BY_ID: dict[str, type[Check]] = {c.id: c for c in ALL_CHECKS}


class UnknownCheckError(ValueError):
    pass


def select_checks(only: Iterable[str] = (), skip: Iterable[str] = ()) -> list[Check]:
    only = list(only)
    skip = list(skip)
    for cid in [*only, *skip]:
        if cid not in CHECKS_BY_ID:
            raise UnknownCheckError(
                f"unknown check {cid!r}; run `wp-db-doctor checks` for the list")
    chosen = [c for c in ALL_CHECKS if (not only or c.id in only) and c.id not in skip]
    return [c() for c in chosen]


def run_check(check: Check, ctx: Context) -> CheckResult:
    missing = check.missing_tables(ctx)
    if missing:
        return check.result(Status.SKIPPED, f"table not found: {', '.join(missing)}")
    try:
        return check.run(ctx)
    except DatabaseError as exc:
        return check.result(Status.ERROR, f"query failed: {exc}")


def run_checks(checks: Iterable[Check], ctx: Context) -> list[CheckResult]:
    return [run_check(check, ctx) for check in checks]


__all__ = [
    "ALL_CHECKS",
    "CHECKS_BY_ID",
    "Check",
    "Context",
    "UnknownCheckError",
    "load_context",
    "run_check",
    "run_checks",
    "select_checks",
]
