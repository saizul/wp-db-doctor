"""Orphaned metadata and relationship rows whose parent row no longer exists."""

from __future__ import annotations

from ..models import STAGE_ORPHANS, CheckResult, FixStatement
from ..utils import format_bytes, format_int, plural, to_int, to_str
from .base import Check, Context, grade


class OrphanedMetaCheck(Check):
    """Rows in a ``*meta`` table pointing at a missing parent row."""

    meta_table = ""
    fk = ""
    parent_table = ""
    pk = ""
    noun = ""

    def run(self, ctx: Context) -> CheckResult:
        meta = ctx.t(self.meta_table)
        parent = ctx.t(self.parent_table)
        join = f"FROM {meta} m LEFT JOIN {parent} p ON p.{self.pk} = m.{self.fk} " \
               f"WHERE p.{self.pk} IS NULL"
        row = ctx.one(
            f"/* wpdd:orphans:{self.meta_table} */ SELECT COUNT(*) AS orphans, "
            f"COALESCE(SUM(LENGTH(m.meta_value)), 0) AS bytes {join}"
        )
        orphans = to_int(row.get("orphans"))
        nbytes = to_int(row.get("bytes"))
        rows = []
        if orphans:
            for r in ctx.query(
                f"/* wpdd:orphans_top:{self.meta_table} */ SELECT m.meta_key, "
                f"COUNT(*) AS total {join} GROUP BY m.meta_key ORDER BY total DESC "
                f"LIMIT {int(ctx.thresholds.orphans_top)}"
            ):
                rows.append([to_str(r.get("meta_key")), format_int(to_int(r.get("total")))])
        fixes = []
        if orphans:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(orphans, f'orphaned {self.meta_table} row')} "
                            f"whose {self.noun} no longer exists",
                sql=f"DELETE m {join}",
                stage=STAGE_ORPHANS,
                estimated_rows=orphans,
            ))
        recommendations = []
        if orphans:
            recommendations.append(
                f"Orphaned {self.meta_table} rows are left behind when {self.noun}s are "
                "deleted with direct SQL or by plugins that skip the WordPress API. They "
                "are never read, so deleting them is safe.")
        return self.result(
            grade(orphans, ctx.thresholds.orphans_warning),
            f"{plural(orphans, 'orphaned row')} ({format_bytes(nbytes)})",
            metrics={"orphans": orphans, "bytes": nbytes},
            columns=["meta key", "rows"],
            rows=rows,
            recommendations=recommendations,
            fixes=fixes,
        )


class OrphanedPostmetaCheck(OrphanedMetaCheck):
    id = "orphaned_postmeta"
    title = "Orphaned post meta"
    category = "orphans"
    description = "postmeta rows whose post_id does not exist in the posts table."
    requires = ("postmeta", "posts")
    fixable = True
    meta_table, fk, parent_table, pk, noun = "postmeta", "post_id", "posts", "ID", "post"


class OrphanedTermmetaCheck(OrphanedMetaCheck):
    id = "orphaned_termmeta"
    title = "Orphaned term meta"
    category = "orphans"
    description = "termmeta rows whose term_id does not exist in the terms table."
    requires = ("termmeta", "terms")
    fixable = True
    meta_table, fk, parent_table, pk, noun = "termmeta", "term_id", "terms", "term_id", "term"


class OrphanedCommentmetaCheck(OrphanedMetaCheck):
    id = "orphaned_commentmeta"
    title = "Orphaned comment meta"
    category = "orphans"
    description = "commentmeta rows whose comment_id does not exist in the comments table."
    requires = ("commentmeta", "comments")
    fixable = True
    meta_table, fk, parent_table, pk, noun = (
        "commentmeta", "comment_id", "comments", "comment_ID", "comment")


class OrphanedUsermetaCheck(OrphanedMetaCheck):
    id = "orphaned_usermeta"
    title = "Orphaned user meta"
    category = "orphans"
    description = "usermeta rows whose user_id does not exist in the users table."
    requires = ("usermeta", "users")
    fixable = True
    meta_table, fk, parent_table, pk, noun = "usermeta", "user_id", "users", "ID", "user"


class OrphanedTermRelationshipsCheck(Check):
    id = "orphaned_term_relationships"
    title = "Orphaned term relationships"
    category = "orphans"
    description = ("term_relationships rows that point at a missing post (link categories "
                   "are excluded) or a missing term_taxonomy row.")
    requires = ("term_relationships", "term_taxonomy", "posts")
    fixable = True

    def run(self, ctx: Context) -> CheckResult:
        tr = ctx.t("term_relationships")
        tt = ctx.t("term_taxonomy")
        posts = ctx.t("posts")
        missing_post = (f"FROM {tr} r INNER JOIN {tt} tt ON tt.term_taxonomy_id = "
                        f"r.term_taxonomy_id LEFT JOIN {posts} p ON p.ID = r.object_id "
                        "WHERE p.ID IS NULL AND tt.taxonomy <> 'link_category'")
        missing_tax = (f"FROM {tr} r LEFT JOIN {tt} tt ON tt.term_taxonomy_id = "
                       "r.term_taxonomy_id WHERE tt.term_taxonomy_id IS NULL")
        no_post = to_int(ctx.one(
            f"/* wpdd:orphans:term_relationships */ SELECT COUNT(*) AS orphans {missing_post}"
        ).get("orphans"))
        no_tax = to_int(ctx.one(
            f"/* wpdd:orphans:term_relationships_tax */ SELECT COUNT(*) AS orphans "
            f"{missing_tax}"
        ).get("orphans"))
        fixes = []
        if no_post:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(no_post, 'term relationship')} of deleted posts",
                sql=f"DELETE r {missing_post}",
                stage=STAGE_ORPHANS,
                estimated_rows=no_post,
            ))
        if no_tax:
            fixes.append(FixStatement(
                check_id=self.id,
                description=f"Delete {plural(no_tax, 'term relationship')} of deleted terms",
                sql=f"DELETE r {missing_tax}",
                stage=STAGE_ORPHANS,
                estimated_rows=no_tax,
            ))
        total = no_post + no_tax
        recommendations = []
        if total:
            recommendations.append(
                "Term counts may be off after cleanup; refresh them with "
                "`wp term recount category post_tag` (add product_cat, product_tag for "
                "WooCommerce).")
        return self.result(
            grade(total, ctx.thresholds.orphans_warning),
            f"{plural(no_post, 'relationship')} to missing posts, {format_int(no_tax)} to "
            "missing terms",
            metrics={"missing_post": no_post, "missing_term_taxonomy": no_tax},
            recommendations=recommendations,
            fixes=fixes,
        )
