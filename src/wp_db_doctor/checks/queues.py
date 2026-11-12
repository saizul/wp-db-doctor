"""Action Scheduler queue and WooCommerce session checks."""

from __future__ import annotations

from ..models import STAGE_QUEUES, CheckResult, FixStatement, Status
from ..utils import format_bytes, format_int, plural, to_int, to_str
from .base import Check, Context, grade

PRUNABLE = ("complete", "failed", "canceled")


class ActionSchedulerCheck(Check):
    id = "action_scheduler"
    title = "Action Scheduler"
    category = "queues"
    description = ("Completed, failed and canceled actions kept by Action Scheduler "
                   "(WooCommerce and many plugins), plus overdue pending actions.")
    requires = ("actionscheduler_actions",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        actions = ctx.t("actionscheduler_actions")
        days = int(th.action_scheduler_retention_days)
        old_cond = f"scheduled_date_gmt < UTC_TIMESTAMP() - INTERVAL {days} DAY"
        by_status = {}
        for r in ctx.query(
            "/* wpdd:as_status */ SELECT status, COUNT(*) AS total, "
            f"COALESCE(SUM({old_cond}), 0) AS old, "
            "COALESCE(SUM(scheduled_date_gmt < UTC_TIMESTAMP() - INTERVAL 1 DAY), 0) AS overdue "
            f"FROM {actions} GROUP BY status"
        ):
            by_status[to_str(r.get("status"))] = (
                to_int(r.get("total")), to_int(r.get("old")), to_int(r.get("overdue")))
        total = sum(v[0] for v in by_status.values())
        prunable = sum(by_status.get(s, (0, 0, 0))[1] for s in PRUNABLE)
        failed = by_status.get("failed", (0, 0, 0))[0]
        overdue = by_status.get("pending", (0, 0, 0))[2]
        rows = [[status, format_int(v[0]), format_int(v[1])]
                for status, v in sorted(by_status.items(), key=lambda kv: -kv[1][0])]

        logs_rows = 0
        if ctx.has("actionscheduler_logs"):
            logs_rows = ctx.tables[ctx.name("actionscheduler_logs")].rows

        failed_hooks = []
        if failed:
            failed_hooks = [
                (to_str(r.get("hook")), to_int(r.get("total"))) for r in ctx.query(
                    "/* wpdd:as_failed_hooks */ SELECT hook, COUNT(*) AS total "
                    f"FROM {actions} WHERE status = 'failed' GROUP BY hook "
                    "ORDER BY total DESC LIMIT 5")
            ]

        status = grade(prunable, th.action_scheduler_warning, th.action_scheduler_critical)
        if overdue >= th.action_scheduler_overdue_warning and status.rank < Status.WARNING.rank:
            status = Status.WARNING

        statuses_sql = "('complete', 'failed', 'canceled')"
        fixes = []
        if prunable:
            if ctx.has("actionscheduler_logs"):
                fixes.append(FixStatement(
                    check_id=self.id,
                    description=f"Delete log entries of finished actions older than {days} days",
                    sql=(f"DELETE l FROM {ctx.t('actionscheduler_logs')} l INNER JOIN "
                         f"{actions} a ON a.action_id = l.action_id WHERE a.status IN "
                         f"{statuses_sql} AND a.{old_cond}"),
                    stage=STAGE_QUEUES,
                ))
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(prunable, 'finished action')} older than "
                            f"{days} days (complete, failed, canceled)",
                sql=f"DELETE FROM {actions} WHERE status IN {statuses_sql} AND {old_cond}",
                stage=STAGE_QUEUES,
                estimated_rows=prunable,
            ))
        if ctx.has("actionscheduler_logs") and total:
            fixes.append(FixStatement(
                check_id=self.id,
                description="Delete log entries whose action no longer exists",
                sql=(f"DELETE l FROM {ctx.t('actionscheduler_logs')} l LEFT JOIN {actions} a "
                     "ON a.action_id = l.action_id WHERE a.action_id IS NULL"),
                stage=STAGE_QUEUES + 1,
            ))

        recommendations = []
        if prunable >= th.action_scheduler_warning:
            recommendations.append(
                "Action Scheduler keeps finished actions for 30 days by default and cleans "
                "them in batches of 20 per run, which cannot keep up with busy stores. "
                "Lower the period with the action_scheduler_retention_period filter, or "
                "raise the batch size with action_scheduler_cleanup_batch_size.")
        if overdue:
            recommendations.append(
                f"{plural(overdue, 'pending action')} are more than a day overdue: the queue "
                "runner is not keeping up. Check WP-Cron, or run the queue from system cron "
                "with `wp action-scheduler run`.")
        if failed_hooks:
            names = ", ".join(f"{hook} ({format_int(n)})" for hook, n in failed_hooks)
            recommendations.append(f"Most failed hooks: {names}. Their logs say why.")
        return self.result(
            status,
            f"{plural(total, 'action')}, {format_int(prunable)} finished more than {days} "
            f"days ago, {format_int(overdue)} overdue",
            metrics={"actions": total, "prunable": prunable, "failed": failed,
                     "overdue_pending": overdue, "log_rows_estimate": logs_rows,
                     "by_status": {k: v[0] for k, v in by_status.items()}},
            columns=["status", "actions", f"older than {days} days"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


class WooCommerceSessionsCheck(Check):
    id = "woocommerce_sessions"
    title = "WooCommerce sessions"
    category = "queues"
    description = "Customer sessions in woocommerce_sessions, including expired ones."
    requires = ("woocommerce_sessions",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        sessions = ctx.t("woocommerce_sessions")
        row = ctx.one(
            "/* wpdd:wc_sessions */ SELECT COUNT(*) AS sessions, "
            "COALESCE(SUM(session_expiry < UNIX_TIMESTAMP()), 0) AS expired, "
            "COALESCE(SUM(LENGTH(session_value)), 0) AS bytes "
            f"FROM {sessions}"
        )
        total = to_int(row.get("sessions"))
        expired = to_int(row.get("expired"))
        nbytes = to_int(row.get("bytes"))
        fixes = []
        recommendations = []
        if expired:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(expired, 'expired WooCommerce session')}",
                sql=f"DELETE FROM {sessions} WHERE session_expiry < UNIX_TIMESTAMP()",
                stage=STAGE_QUEUES,
                estimated_rows=expired,
            ))
            recommendations.append(
                "WooCommerce removes expired sessions with the twice-daily "
                "woocommerce_cleanup_sessions event; a backlog means WP-Cron is not running. "
                "WooCommerce > Status > Tools > Clear customer sessions also works, but "
                "logs out every customer.")
        return self.result(
            grade(expired, ctx.thresholds.wc_sessions_expired_warning),
            f"{plural(total, 'session')} ({format_bytes(nbytes)}), {format_int(expired)} "
            "expired",
            metrics={"sessions": total, "expired": expired, "bytes": nbytes},
            recommendations=recommendations,
            fixes=fixes,
        )
