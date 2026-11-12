"""Checks on the options table: autoload size and expired transients."""

from __future__ import annotations

from ..models import STAGE_OPTIONS, CheckResult, FixKind, FixStatement, Status
from ..utils import format_bytes, format_int, plural, sql_literal, to_int, to_str
from .base import Check, Context, grade

# Values WordPress loads on every request. 6.6 added on/off/auto/auto-on/auto-off.
AUTOLOAD_VALUES = ("yes", "on", "auto", "auto-on")
AUTOLOAD_IN = "(" + ", ".join(f"'{v}'" for v in AUTOLOAD_VALUES) + ")"

# Options that WordPress needs on every request; never suggest turning them off.
CORE_AUTOLOAD_KEEP = frozenset({
    "siteurl", "home", "blogname", "blogdescription", "active_plugins", "template",
    "stylesheet", "cron", "rewrite_rules", "permalink_structure", "sidebars_widgets",
    "widget_block", "user_roles", "db_version", "WPLANG", "timezone_string",
    "gmt_offset", "date_format", "time_format", "start_of_week", "uninstall_plugins",
    "recently_activated", "can_compress_scripts", "page_on_front", "show_on_front",
    "page_for_posts", "default_role", "users_can_register", "upload_path",
    "upload_url_path", "category_base", "tag_base",
})

# Prefix -> who usually owns an option. Only a hint for the report.
OWNER_HINTS: tuple[tuple[str, str], ...] = (
    ("_transient_", "transient (should not be autoloaded)"),
    ("_site_transient_", "site transient (should not be autoloaded)"),
    ("rewrite_rules", "WordPress core (regenerate: Settings > Permalinks > Save)"),
    ("cron", "WordPress core cron events (large means stuck or duplicate events)"),
    ("wp_user_roles", "WordPress core roles"),
    ("user_roles", "WordPress core roles"),
    ("widget_", "widgets"),
    ("theme_mods_", "theme customizer settings"),
    ("wpseo", "Yoast SEO"),
    ("rank_math", "Rank Math"),
    ("rank-math", "Rank Math"),
    ("elementor", "Elementor"),
    ("_elementor", "Elementor"),
    ("woocommerce", "WooCommerce"),
    ("wc_", "WooCommerce"),
    ("jetpack", "Jetpack"),
    ("wordfence", "Wordfence"),
    ("wf", "Wordfence"),
    ("itsec", "Solid Security"),
    ("wp_rocket", "WP Rocket"),
    ("rocket_", "WP Rocket"),
    ("litespeed", "LiteSpeed Cache"),
    ("w3tc", "W3 Total Cache"),
    ("autoptimize", "Autoptimize"),
    ("aioseo", "All in One SEO"),
    ("wpforms", "WPForms"),
    ("gf_", "Gravity Forms"),
    ("rg_", "Gravity Forms"),
    ("updraft", "UpdraftPlus"),
    ("redux", "Redux framework (theme options)"),
    ("_wp_session_", "old session plugin data"),
    ("fs_", "Freemius SDK"),
    ("action_scheduler", "Action Scheduler"),
)


def owner_hint(option_name: str) -> str:
    low = option_name.lower()
    for prefix, owner in OWNER_HINTS:
        if low.startswith(prefix):
            return owner
    return ""


class AutoloadCheck(Check):
    id = "autoload"
    title = "Autoloaded options"
    category = "options"
    description = ("Total size of options loaded on every request, with the largest "
                   "offenders. WordPress Site Health warns above 800 KB.")
    requires = ("options",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        options = ctx.t("options")
        total = ctx.one(
            f"/* wpdd:autoload_total */ SELECT COUNT(*) AS option_count, "
            f"COALESCE(SUM(LENGTH(option_value)), 0) AS total_bytes "
            f"FROM {options} WHERE autoload IN {AUTOLOAD_IN}"
        )
        count = to_int(total.get("option_count"))
        size = to_int(total.get("total_bytes"))
        top = ctx.query(
            f"/* wpdd:autoload_top */ SELECT option_name, LENGTH(option_value) AS bytes "
            f"FROM {options} WHERE autoload IN {AUTOLOAD_IN} "
            f"ORDER BY bytes DESC LIMIT {int(th.autoload_top)}"
        )
        rows = []
        fixes = []
        large = th.autoload_large_option_kb * 1024
        for row in top:
            name = to_str(row.get("option_name"))
            nbytes = to_int(row.get("bytes"))
            share = f"{(nbytes / size * 100):.1f}%" if size else "-"
            rows.append([name, format_bytes(nbytes), share, owner_hint(name)])
            if nbytes >= large and name not in CORE_AUTOLOAD_KEEP and not name.startswith(
                    ("_transient_", "_site_transient_")):
                fixes.append(FixStatement(
                    check_id=self.id,
                    description=f"Stop autoloading {name} ({format_bytes(nbytes)}); "
                                "confirm the owning plugin reads it with get_option() only "
                                "where needed",
                    sql=f"UPDATE {options} SET autoload = 'off' "
                        f"WHERE option_name = {sql_literal(name)}",
                    kind=FixKind.UPDATE,
                    stage=STAGE_OPTIONS,
                    estimated_rows=1,
                    review_only=True,
                ))
        transient_autoload = [r for r in top if to_str(r.get("option_name")).startswith(
            ("_transient_", "_site_transient_"))]

        kb = size / 1024
        if kb >= th.autoload_critical_kb:
            status = Status.CRITICAL
        elif kb >= th.autoload_warning_kb:
            status = Status.WARNING
        else:
            status = Status.OK
        summary = (f"{format_bytes(size)} autoloaded across "
                   f"{plural(count, 'option')}")
        recommendations = []
        if status is not Status.OK:
            recommendations.append(
                "Every page load reads all autoloaded options; with a persistent object "
                "cache the whole set is one cache item, so keep it well under 1 MB.")
            recommendations.append(
                "Look at the largest options first. Leftovers from removed plugins can be "
                "deleted; settings of active plugins can often be switched to autoload "
                "'off' (review the commented UPDATE statements in the fix plan).")
        if transient_autoload:
            recommendations.append(
                "Some transients are autoloaded. That happens when a plugin calls "
                "set_transient() without an expiry; report it to the plugin author.")
        if any(to_str(r.get("option_name")) == "cron" and to_int(r.get("bytes")) > 512 * 1024
               for r in top):
            recommendations.append(
                "The cron option is very large: list events with `wp cron event list` and "
                "remove duplicates left by deactivated plugins.")
        return self.result(
            status, summary,
            metrics={"autoload_bytes": size, "autoload_options": count},
            columns=["option", "size", "share", "likely owner"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


TIMEOUT = r"'\_transient\_timeout\_%'"
SITE_TIMEOUT = r"'\_site\_transient\_timeout\_%'"
TRANSIENT = r"'\_transient\_%'"
SITE_TRANSIENT = r"'\_site\_transient\_%'"


class ExpiredTransientsCheck(Check):
    id = "expired_transients"
    title = "Expired transients"
    category = "options"
    description = ("Transients whose timeout has passed but that are still stored in the "
                   "options table (WordPress only removes them lazily or by daily cron).")
    requires = ("options",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        options = ctx.t("options")
        row = ctx.one(
            "/* wpdd:transients */ SELECT "
            f"COALESCE(SUM(option_name LIKE {TIMEOUT} AND "
            "CAST(option_value AS UNSIGNED) < UNIX_TIMESTAMP()), 0) AS expired, "
            f"COALESCE(SUM(option_name LIKE {SITE_TIMEOUT} AND "
            "CAST(option_value AS UNSIGNED) < UNIX_TIMESTAMP()), 0) AS expired_site, "
            f"COALESCE(SUM(option_name LIKE {TRANSIENT} AND "
            f"option_name NOT LIKE {TIMEOUT}), 0) AS transients, "
            f"COALESCE(SUM(option_name LIKE {SITE_TRANSIENT} AND "
            f"option_name NOT LIKE {SITE_TIMEOUT}), 0) AS site_transients, "
            "COALESCE(SUM(LENGTH(option_value)), 0) AS transient_bytes "
            f"FROM {options} WHERE option_name LIKE {TRANSIENT} "
            f"OR option_name LIKE {SITE_TRANSIENT}"
        )
        expired = to_int(row.get("expired"))
        expired_site = to_int(row.get("expired_site"))
        transients = to_int(row.get("transients"))
        site_transients = to_int(row.get("site_transients"))
        nbytes = to_int(row.get("transient_bytes"))
        total_expired = expired + expired_site
        status = grade(total_expired, ctx.thresholds.expired_transients_warning)
        fixes = []
        if expired:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(expired, 'expired transient')} "
                            "(value and timeout rows), as delete_expired_transients() does",
                sql=(f"DELETE a, b FROM {options} a INNER JOIN {options} b "
                     "ON b.option_name = CONCAT('_transient_', SUBSTRING(a.option_name, 20)) "
                     f"WHERE a.option_name LIKE {TIMEOUT} "
                     "AND CAST(a.option_value AS UNSIGNED) < UNIX_TIMESTAMP()"),
                stage=STAGE_OPTIONS,
                estimated_rows=expired * 2,
            ))
        if expired_site:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(expired_site, 'expired site transient')}",
                sql=(f"DELETE a, b FROM {options} a INNER JOIN {options} b "
                     "ON b.option_name = CONCAT('_site_transient_', "
                     "SUBSTRING(a.option_name, 25)) "
                     f"WHERE a.option_name LIKE {SITE_TIMEOUT} "
                     "AND CAST(a.option_value AS UNSIGNED) < UNIX_TIMESTAMP()"),
                stage=STAGE_OPTIONS,
                estimated_rows=expired_site * 2,
            ))
        recommendations = []
        if total_expired:
            recommendations.append(
                "WP-CLI alternative that runs WordPress hooks: `wp transient delete --expired`.")
        if total_expired >= ctx.thresholds.expired_transients_warning:
            recommendations.append(
                "Many expired transients usually mean WP-Cron is not running. If "
                "DISABLE_WP_CRON is set, make sure a system cron calls wp-cron.php.")
        if transients + site_transients > 5000:
            recommendations.append(
                "A persistent object cache (Redis or Memcached) keeps transients out of the "
                "database entirely.")
        summary = (f"{plural(total_expired, 'expired transient')} out of "
                   f"{format_int(transients + site_transients)} stored "
                   f"({format_bytes(nbytes)} including timeouts)")
        return self.result(
            status, summary,
            metrics={
                "expired": expired,
                "expired_site": expired_site,
                "transients": transients,
                "site_transients": site_transients,
                "transient_bytes": nbytes,
            },
            recommendations=recommendations,
            fixes=fixes,
        )

