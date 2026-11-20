"""Checks on posts and comments: revisions, auto-drafts, trash and spam."""

from __future__ import annotations

from ..models import STAGE_COMMENTS, STAGE_CONTENT, CheckResult, FixStatement, Status
from ..utils import format_bytes, format_int, plural, to_int, to_str
from .base import Check, Context, grade


def delete_posts_fixes(ctx: Context, check_id: str, where: str, what: str,
                       estimated: int, with_comments: bool = False) -> list[FixStatement]:
    """Statements that delete posts matching ``where`` (on alias ``p``) and the
    rows that point at them, children first so nothing is left orphaned."""
    posts = ctx.t("posts")
    fixes = []
    if ctx.has("postmeta"):
        fixes.append(FixStatement(
            check_id=check_id,
            description=f"Delete post meta of {what}",
            sql=(f"DELETE pm FROM {ctx.t('postmeta')} pm INNER JOIN {posts} p "
                 f"ON p.ID = pm.post_id WHERE {where}"),
            stage=STAGE_CONTENT,
        ))
    if ctx.has("term_relationships"):
        fixes.append(FixStatement(
            check_id=check_id,
            description=f"Delete term relationships of {what}",
            sql=(f"DELETE tr FROM {ctx.t('term_relationships')} tr INNER JOIN {posts} p "
                 f"ON p.ID = tr.object_id WHERE {where}"),
            stage=STAGE_CONTENT,
        ))
    if with_comments and ctx.has("comments"):
        if ctx.has("commentmeta"):
            fixes.append(FixStatement(
                check_id=check_id,
                description=f"Delete comment meta of comments on {what}",
                sql=(f"DELETE cm FROM {ctx.t('commentmeta')} cm "
                     f"INNER JOIN {ctx.t('comments')} c ON c.comment_ID = cm.comment_id "
                     f"INNER JOIN {posts} p ON p.ID = c.comment_post_ID WHERE {where}"),
                stage=STAGE_CONTENT,
            ))
        fixes.append(FixStatement(
            check_id=check_id,
            description=f"Delete comments on {what}",
            sql=(f"DELETE c FROM {ctx.t('comments')} c INNER JOIN {posts} p "
                 f"ON p.ID = c.comment_post_ID WHERE {where}"),
            stage=STAGE_CONTENT,
        ))
    fixes.append(FixStatement(
        check_id=check_id,
        description=f"Delete {what}",
        sql=f"DELETE p FROM {posts} p WHERE {where}",
        stage=STAGE_CONTENT,
        estimated_rows=estimated,
    ))
    return fixes


class RevisionsCheck(Check):
    id = "post_revisions"
    title = "Post revisions"
    category = "content"
    description = "Stored revisions, the posts with the most of them, and old ones to prune."
    requires = ("posts",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        posts = ctx.t("posts")
        age = int(th.revisions_min_age_days)
        old_cond = f"p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL {age} DAY"
        row = ctx.one(
            "/* wpdd:revisions */ SELECT COUNT(*) AS revisions, "
            "COALESCE(SUM(LENGTH(p.post_content)), 0) AS content_bytes, "
            "COUNT(DISTINCT p.post_parent) AS parents, "
            f"COALESCE(SUM({old_cond}), 0) AS old_revisions "
            f"FROM {posts} p WHERE p.post_type = 'revision'"
        )
        count = to_int(row.get("revisions"))
        old = to_int(row.get("old_revisions"))
        nbytes = to_int(row.get("content_bytes"))
        parents = to_int(row.get("parents"))
        rows = []
        if count:
            for r in ctx.query(
                "/* wpdd:revisions_top */ SELECT p.post_parent AS parent_id, "
                "COUNT(*) AS total, MAX(parent.post_title) AS title, "
                "MAX(parent.post_type) AS parent_type "
                f"FROM {posts} p LEFT JOIN {posts} parent ON parent.ID = p.post_parent "
                "WHERE p.post_type = 'revision' GROUP BY p.post_parent "
                f"ORDER BY total DESC LIMIT {int(th.revisions_top)}"
            ):
                title = to_str(r.get("title")) or "(parent missing)"
                rows.append([to_int(r.get("parent_id")), to_str(r.get("parent_type")),
                             title, format_int(to_int(r.get("total")))])
        status = grade(count, th.revisions_warning, th.revisions_critical)
        recommendations = []
        limit = ctx.constants.get("WP_POST_REVISIONS")
        if status in (Status.WARNING, Status.CRITICAL):
            if limit is None or limit is True or (isinstance(limit, int) and limit < 0):
                recommendations.append(
                    "Revisions are unlimited. Add define( 'WP_POST_REVISIONS', 5 ); to "
                    "wp-config.php to keep only the latest five per post.")
            else:
                recommendations.append(
                    f"WP_POST_REVISIONS is {limit!r}; revisions above that limit were "
                    "created before it was set, or by a plugin that bypasses it.")
        if old:
            recommendations.append(
                "WP-CLI alternative (runs WordPress hooks): "
                "`wp post delete $(wp post list --post_type=revision --format=ids) --force`.")
        fixes = []
        if old:
            fixes = delete_posts_fixes(
                ctx, self.id,
                where=f"p.post_type = 'revision' AND {old_cond}",
                what=f"{plural(old, 'revision')} older than {age} days",
                estimated=old,
            )
        summary = (f"{plural(count, 'revision')} for {plural(parents, 'post')} "
                   f"({format_bytes(nbytes)} of content); {format_int(old)} older than "
                   f"{age} days")
        return self.result(
            status, summary,
            metrics={"revisions": count, "old_revisions": old, "content_bytes": nbytes,
                     "parents": parents, "wp_post_revisions": limit},
            columns=["post id", "type", "title", "revisions"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


class AutoDraftsCheck(Check):
    id = "auto_drafts"
    title = "Auto-drafts"
    category = "content"
    description = ("Auto-drafts are created each time the editor opens. WordPress deletes "
                   "them after 7 days by cron; old ones mean cron is not running.")
    requires = ("posts",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        age = int(ctx.thresholds.auto_draft_age_days)
        old_cond = f"p.post_date < NOW() - INTERVAL {age} DAY"
        row = ctx.one(
            "/* wpdd:auto_drafts */ SELECT COUNT(*) AS total, "
            f"COALESCE(SUM({old_cond}), 0) AS old "
            f"FROM {ctx.t('posts')} p WHERE p.post_status = 'auto-draft'"
        )
        total = to_int(row.get("total"))
        old = to_int(row.get("old"))
        status = grade(old, ctx.thresholds.auto_drafts_warning)
        recommendations = []
        if old:
            recommendations.append(
                f"{plural(old, 'auto-draft')} are older than {age} days, so the daily "
                "wp_scheduled_auto_draft_delete event is not running. Check WP-Cron "
                "(`wp cron event list`, DISABLE_WP_CRON and the system crontab).")
        fixes = []
        if old:
            fixes = delete_posts_fixes(
                ctx, self.id,
                where=f"p.post_status = 'auto-draft' AND {old_cond}",
                what=f"{plural(old, 'auto-draft')} older than {age} days",
                estimated=old,
            )
        return self.result(
            status,
            f"{plural(total, 'auto-draft')}, {format_int(old)} older than {age} days",
            metrics={"auto_drafts": total, "old_auto_drafts": old},
            recommendations=recommendations,
            fixes=fixes,
        )


class TrashedPostsCheck(Check):
    id = "trashed_posts"
    title = "Trashed posts"
    category = "content"
    description = "Posts, pages and other post types sitting in the trash."
    requires = ("posts",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        empty_days = ctx.constants.get("EMPTY_TRASH_DAYS")
        age = int(th.trash_min_age_days)
        old_cond = f"p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL {age} DAY"
        by_type = ctx.query(
            "/* wpdd:trash */ SELECT p.post_type AS post_type, COUNT(*) AS total, "
            f"COALESCE(SUM({old_cond}), 0) AS old "
            f"FROM {ctx.t('posts')} p WHERE p.post_status = 'trash' "
            "GROUP BY p.post_type ORDER BY total DESC"
        )
        total = sum(to_int(r.get("total")) for r in by_type)
        old = sum(to_int(r.get("old")) for r in by_type)
        rows = [[to_str(r.get("post_type")), format_int(to_int(r.get("total"))),
                 format_int(to_int(r.get("old")))] for r in by_type]
        status = grade(total, th.trashed_warning)
        recommendations = []
        if old:
            recommendations.append(
                "Emptying the trash in wp-admin (or `wp post delete ... --force`) runs "
                "plugin hooks; prefer it for WooCommerce products and orders.")
            if empty_days == 0:
                recommendations.append(
                    "EMPTY_TRASH_DAYS is 0 (trash disabled), yet trashed posts exist; they "
                    "were trashed before the constant was set.")
            else:
                recommendations.append(
                    "WordPress empties the trash after EMPTY_TRASH_DAYS (30 by default) by "
                    "cron; old trashed posts mean that event is not running.")
        if any(to_str(r.get("post_type")) in ("shop_order", "shop_order_placehold")
               for r in by_type):
            recommendations.append(
                "Trashed WooCommerce orders found. With HPOS enabled, delete them in "
                "WooCommerce > Orders so the orders tables stay in sync.")
        fixes = []
        if old:
            fixes = delete_posts_fixes(
                ctx, self.id,
                where=f"p.post_status = 'trash' AND {old_cond}",
                what=f"{plural(old, 'trashed post')} older than {age} days",
                estimated=old,
                with_comments=True,
            )
        return self.result(
            status,
            f"{plural(total, 'trashed post')}, {format_int(old)} trashed more than "
            f"{age} days ago",
            metrics={"trashed": total, "old_trashed": old},
            columns=["post type", "trashed", f"older than {age} days"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


class SpamCommentsCheck(Check):
    id = "spam_comments"
    title = "Spam and trashed comments"
    category = "content"
    description = "Comments marked spam or trash, plus the pending moderation queue."
    requires = ("comments",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        th = ctx.thresholds
        comments = ctx.t("comments")
        counts = {to_str(r.get("comment_approved")): to_int(r.get("total")) for r in ctx.query(
            "/* wpdd:comments */ SELECT comment_approved, COUNT(*) AS total "
            f"FROM {comments} WHERE comment_approved IN ('spam', 'trash', 'post-trashed', '0') "
            "GROUP BY comment_approved"
        )}
        spam = counts.get("spam", 0)
        trash = counts.get("trash", 0)
        post_trashed = counts.get("post-trashed", 0)
        pending = counts.get("0", 0)
        removable = spam + trash
        status = grade(removable, th.comments_warning, th.comments_critical)
        rows = [["spam", format_int(spam)], ["trash", format_int(trash)],
                ["post-trashed", format_int(post_trashed)], ["pending", format_int(pending)]]
        fixes = []
        if removable:
            where = "c.comment_approved IN ('spam', 'trash')"
            if ctx.has("commentmeta"):
                fixes.append(FixStatement(
                    check_id=self.id,
                    description="Delete comment meta of spam and trashed comments",
                    sql=(f"DELETE cm FROM {ctx.t('commentmeta')} cm INNER JOIN {comments} c "
                         f"ON c.comment_ID = cm.comment_id WHERE {where}"),
                    stage=STAGE_COMMENTS,
                ))
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(removable, 'spam or trashed comment')}",
                sql=f"DELETE c FROM {comments} c WHERE {where}",
                stage=STAGE_COMMENTS,
                estimated_rows=removable,
            ))
        recommendations = []
        if removable:
            recommendations.append(
                "After deleting comments directly, refresh the cached counts with "
                "`wp comment recount`.")
        if spam >= th.comments_warning:
            recommendations.append(
                "Large spam volume: use an anti-spam plugin that discards obvious spam "
                "instead of storing it, or close comments on old posts.")
        if pending >= th.comments_warning:
            recommendations.append(
                f"{plural(pending, 'comment')} wait for moderation; review them before "
                "running any cleanup, they are not touched by the fix plan.")
        return self.result(
            status,
            f"{plural(spam, 'spam comment')}, {format_int(trash)} trashed, "
            f"{format_int(pending)} pending",
            metrics={"spam": spam, "trash": trash, "post_trashed": post_trashed,
                     "pending": pending},
            columns=["status", "comments"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


# WordPress stores this when a provider could not be reached or had no embed.
OEMBED_UNKNOWN = "'{{unknown}}'"
OEMBED_META = r"'\_oembed\_%'"
OEMBED_TIME_META = r"'\_oembed\_time\_%'"


class OembedCacheCheck(Check):
    id = "oembed_cache"
    title = "oEmbed cache"
    category = "content"
    description = ("Cached embed HTML for YouTube, X, Vimeo and other oEmbed providers: "
                   "oembed_cache posts (WordPress 4.9+) and legacy _oembed_* post meta, "
                   "including failed lookups stored as {{unknown}}.")
    requires = ("posts",)
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        posts = ctx.t("posts")
        row = ctx.one(
            "/* wpdd:oembed */ SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(p.post_content = {OEMBED_UNKNOWN}), 0) AS failed, "
            "COALESCE(SUM(LENGTH(p.post_content)), 0) AS bytes "
            f"FROM {posts} p WHERE p.post_type = 'oembed_cache'"
        )
        cache_posts, failed_posts = to_int(row.get("total")), to_int(row.get("failed"))
        size = to_int(row.get("bytes"))
        legacy = failed_legacy = 0
        value_cond = (f"v.meta_key LIKE {OEMBED_META} AND v.meta_key NOT LIKE {OEMBED_TIME_META}")
        if ctx.has("postmeta"):
            meta = ctx.one(
                "/* wpdd:oembed_meta */ SELECT COUNT(*) AS total, "
                f"COALESCE(SUM(v.meta_value = {OEMBED_UNKNOWN}), 0) AS failed, "
                "COALESCE(SUM(LENGTH(v.meta_value)), 0) AS bytes "
                f"FROM {ctx.t('postmeta')} v WHERE {value_cond}"
            )
            legacy, failed_legacy = to_int(meta.get("total")), to_int(meta.get("failed"))
            size += to_int(meta.get("bytes"))

        total = cache_posts + legacy
        failed = failed_posts + failed_legacy
        # Working caches are not a problem until there are many; failed lookups are junk.
        if total >= ctx.thresholds.oembed_cache_warning:
            status = Status.WARNING
        else:
            status = Status.INFO if failed else Status.OK
        recommendations: list[str] = []
        fixes: list[FixStatement] = []
        if failed_posts:
            where = f"p.post_type = 'oembed_cache' AND p.post_content = {OEMBED_UNKNOWN}"
            fixes += delete_posts_fixes(ctx, self.id, where=where,
                                        what=f"{plural(failed_posts, 'failed oEmbed lookup')}",
                                        estimated=failed_posts)
        if failed_legacy:
            pm = ctx.t("postmeta")
            failed_cond = f"{value_cond} AND v.meta_value = {OEMBED_UNKNOWN}"
            fixes.append(FixStatement(
                check_id=self.id,
                description="Delete the timestamps of failed legacy oEmbed lookups",
                sql=(f"DELETE t FROM {pm} t INNER JOIN {pm} v ON v.post_id = t.post_id "
                     "AND t.meta_key = CONCAT('_oembed_time_', SUBSTRING(v.meta_key, 9)) "
                     f"WHERE {failed_cond}"),
                stage=STAGE_CONTENT,
            ))
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(failed_legacy, 'failed legacy oEmbed lookup')}",
                sql=f"DELETE v FROM {pm} v WHERE {failed_cond}",
                stage=STAGE_CONTENT,
                estimated_rows=failed_legacy,
            ))
        if failed:
            recommendations.append(
                f"{plural(failed, 'cached oEmbed lookup')} failed ({{{{unknown}}}}). WordPress "
                "keeps these so it does not ask the provider on every view; they hold no embed "
                "and are safe to delete. The fix plan deletes only failed lookups.")
        if total - failed > 0 and status.rank >= Status.WARNING.rank:
            recommendations.append(
                "Working oEmbed caches are rebuilt on the next page view, but providers that now "
                "need an access token (Facebook, Instagram) cannot be fetched again; the plan "
                "keeps them. Clear one post with `wp embed cache clear <post-id>`.")
        return self.result(
            status,
            f"{plural(cache_posts, 'oembed_cache post')}, "
            f"{plural(legacy, 'legacy _oembed_ meta row')}, "
            f"{format_int(failed)} failed ({format_bytes(size)})",
            metrics={"oembed_cache_posts": cache_posts, "oembed_legacy_meta": legacy,
                     "oembed_failed": failed, "oembed_bytes": size},
            recommendations=recommendations,
            fixes=fixes,
        )
