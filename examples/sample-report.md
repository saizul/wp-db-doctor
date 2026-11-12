# WordPress database report: shop_wp

| | |
|---|---|
| Database | shop_wp |
| Host | shop_user@localhost:3307 |
| Prefix | shop_ |
| Server | 5.7.44-log |
| Tables | 19 |
| Size | 1.6 GB |
| Generated | 2026-11-12T08:00:00Z |

**Summary:** 5 critical, 10 warning, 1 info, 2 ok

| Status | Check | Result |
|---|---|---|
| WARN | [Table prefix](#table_prefix) | prefix shop_, 1 other install in the same schema |
| WARN | [Database server](#server_version) | MySQL 5.7.44 (end of life 2023-10-31) |
| CRIT | [Autoloaded options](#autoload) | 4.0 MB autoloaded across 1,450 options |
| WARN | [Expired transients](#expired_transients) | 1,825 expired transients out of 9,120 stored (30.0 MB including timeouts) |
| CRIT | [Post revisions](#post_revisions) | 25,000 revisions for 800 posts (300.0 MB of content); 21,000 older than 30 days |
| WARN | [Auto-drafts](#auto_drafts) | 420 auto-drafts, 400 older than 7 days |
| WARN | [Trashed posts](#trashed_posts) | 240 trashed posts, 200 trashed more than 30 days ago |
| CRIT | [Spam and trashed comments](#spam_comments) | 60,000 spam comments, 40 trashed, 3 pending |
| WARN | [Orphaned post meta](#orphaned_postmeta) | 54,000 orphaned rows (12.0 MB) |
| INFO | [Orphaned term meta](#orphaned_termmeta) | 3 orphaned rows (90 B) |
| OK | [Orphaned comment meta](#orphaned_commentmeta) | 0 orphaned rows (0 B) |
| OK | [Orphaned user meta](#orphaned_usermeta) | 0 orphaned rows (0 B) |
| WARN | [Orphaned term relationships](#orphaned_term_relationships) | 1,200 relationships to missing posts, 4 to missing terms |
| CRIT | [Action Scheduler](#action_scheduler) | 603,500 actions, 582,500 finished more than 30 days ago, 450 overdue |
| WARN | [WooCommerce sessions](#woocommerce_sessions) | 9,000 sessions (40.0 MB), 7,000 expired |
| CRIT | [Missing indexes and primary keys](#missing_indexes) | 3 missing keys on 3 tables |
| WARN | [Table engines and collations](#engine_collation) | 16 InnoDB, 1 MyISAM; 3 collations (target utf8mb4_unicode_520_ci) |
| WARN | [Largest tables](#table_sizes) | 19 tables, 1.6 GB total; 2 over 256 MB |

<a id="table_prefix"></a>
## WARN: Table prefix

`table_prefix` - prefix shop_, 1 other install in the same schema

| prefix | role |
|---|---|
| old_ | other install |
| shop_ | this site |

- Other WordPress installs share this database (old_). If they are leftovers, export them and drop their tables; shared schemas make backups and restores riskier.

<a id="server_version"></a>
## WARN: Database server

`server_version` - MySQL 5.7.44 (end of life 2023-10-31)

- MySQL 5.7 reached end of life on 2023-10-31 and no longer gets security fixes. Plan an upgrade to a supported LTS release.
- innodb_buffer_pool_size is 1.0 GB but InnoDB data and indexes take 1.6 GB. On a dedicated database server give the buffer pool 50-70% of RAM.

<a id="autoload"></a>
## CRIT: Autoloaded options

`autoload` - 4.0 MB autoloaded across 1,450 options

| option | size | share | likely owner |
|---|---|---|---|
| wpseo_premium_redirects_export | 1.6 MB | 39.1% | Yoast SEO |
| cron | 900.0 KB | 22.0% | WordPress core cron events (large means stuck or duplicate events) |
| _transient_feed_abc | 300.0 KB | 7.3% | transient (should not be autoloaded) |
| old_plugin_cache | 200.0 KB | 4.9% |  |
| small_one | 1.0 KB | 0.0% |  |

- Every page load reads all autoloaded options; with a persistent object cache the whole set is one cache item, so keep it well under 1 MB.
- Look at the largest options first. Leftovers from removed plugins can be deleted; settings of active plugins can often be switched to autoload 'off' (review the commented UPDATE statements in the fix plan).
- Some transients are autoloaded. That happens when a plugin calls set_transient() without an expiry; report it to the plugin author.
- The cron option is very large: list events with `wp cron event list` and remove duplicates left by deactivated plugins.

<a id="expired_transients"></a>
## WARN: Expired transients

`expired_transients` - 1,825 expired transients out of 9,120 stored (30.0 MB including timeouts)

- WP-CLI alternative that runs WordPress hooks: `wp transient delete --expired`.
- Many expired transients usually mean WP-Cron is not running. If DISABLE_WP_CRON is set, make sure a system cron calls wp-cron.php.
- A persistent object cache (Redis or Memcached) keeps transients out of the database entirely.

Fix plan: 2 statement(s), see `wp-db-doctor fix --only expired_transients`.

<a id="post_revisions"></a>
## CRIT: Post revisions

`post_revisions` - 25,000 revisions for 800 posts (300.0 MB of content); 21,000 older than 30 days

| post id | type | title | revisions |
|---|---|---|---|
| 12 | page | Home | 640 |
| 99 |  | (parent missing) | 300 |

- Revisions are unlimited. Add define( 'WP_POST_REVISIONS', 5 ); to wp-config.php to keep only the latest five per post.
- WP-CLI alternative (runs WordPress hooks): `wp post delete $(wp post list --post_type=revision --format=ids) --force`.

Fix plan: 3 statement(s), see `wp-db-doctor fix --only post_revisions`.

<a id="auto_drafts"></a>
## WARN: Auto-drafts

`auto_drafts` - 420 auto-drafts, 400 older than 7 days

- 400 auto-drafts are older than 7 days, so the daily wp_scheduled_auto_draft_delete event is not running. Check WP-Cron (`wp cron event list`, DISABLE_WP_CRON and the system crontab).

Fix plan: 3 statement(s), see `wp-db-doctor fix --only auto_drafts`.

<a id="trashed_posts"></a>
## WARN: Trashed posts

`trashed_posts` - 240 trashed posts, 200 trashed more than 30 days ago

| post type | trashed | older than 30 days |
|---|---|---|
| post | 150 | 120 |
| shop_order | 90 | 80 |

- Emptying the trash in wp-admin (or `wp post delete ... --force`) runs plugin hooks; prefer it for WooCommerce products and orders.
- WordPress empties the trash after EMPTY_TRASH_DAYS (30 by default) by cron; old trashed posts mean that event is not running.
- Trashed WooCommerce orders found. With HPOS enabled, delete them in WooCommerce > Orders so the orders tables stay in sync.

Fix plan: 5 statement(s), see `wp-db-doctor fix --only trashed_posts`.

<a id="spam_comments"></a>
## CRIT: Spam and trashed comments

`spam_comments` - 60,000 spam comments, 40 trashed, 3 pending

| status | comments |
|---|---|
| spam | 60,000 |
| trash | 40 |
| post-trashed | 0 |
| pending | 3 |

- After deleting comments directly, refresh the cached counts with `wp comment recount`.
- Large spam volume: use an anti-spam plugin that discards obvious spam instead of storing it, or close comments on old posts.

Fix plan: 2 statement(s), see `wp-db-doctor fix --only spam_comments`.

<a id="orphaned_postmeta"></a>
## WARN: Orphaned post meta

`orphaned_postmeta` - 54,000 orphaned rows (12.0 MB)

| meta key | rows |
|---|---|
| _edit_lock | 30,000 |
| _elementor_data | 24,000 |

- Orphaned postmeta rows are left behind when posts are deleted with direct SQL or by plugins that skip the WordPress API. They are never read, so deleting them is safe.

Fix plan: 1 statement(s), see `wp-db-doctor fix --only orphaned_postmeta`.

<a id="orphaned_termmeta"></a>
## INFO: Orphaned term meta

`orphaned_termmeta` - 3 orphaned rows (90 B)

| meta key | rows |
|---|---|
| order | 3 |

- Orphaned termmeta rows are left behind when terms are deleted with direct SQL or by plugins that skip the WordPress API. They are never read, so deleting them is safe.

Fix plan: 1 statement(s), see `wp-db-doctor fix --only orphaned_termmeta`.

<a id="orphaned_commentmeta"></a>
## OK: Orphaned comment meta

`orphaned_commentmeta` - 0 orphaned rows (0 B)

<a id="orphaned_usermeta"></a>
## OK: Orphaned user meta

`orphaned_usermeta` - 0 orphaned rows (0 B)

<a id="orphaned_term_relationships"></a>
## WARN: Orphaned term relationships

`orphaned_term_relationships` - 1,200 relationships to missing posts, 4 to missing terms

- Term counts may be off after cleanup; refresh them with `wp term recount category post_tag` (add product_cat, product_tag for WooCommerce).

Fix plan: 2 statement(s), see `wp-db-doctor fix --only orphaned_term_relationships`.

<a id="action_scheduler"></a>
## CRIT: Action Scheduler

`action_scheduler` - 603,500 actions, 582,500 finished more than 30 days ago, 450 overdue

| status | actions | older than 30 days |
|---|---|---|
| complete | 600,000 | 580,000 |
| failed | 3,000 | 2,500 |
| pending | 500 | 0 |

- Action Scheduler keeps finished actions for 30 days by default and cleans them in batches of 20 per run, which cannot keep up with busy stores. Lower the period with the action_scheduler_retention_period filter, or raise the batch size with action_scheduler_cleanup_batch_size.
- 450 pending actions are more than a day overdue: the queue runner is not keeping up. Check WP-Cron, or run the queue from system cron with `wp action-scheduler run`.
- Most failed hooks: woocommerce_run_product_attribute_lookup_update (2,900). Their logs say why.

Fix plan: 3 statement(s), see `wp-db-doctor fix --only action_scheduler`.

<a id="woocommerce_sessions"></a>
## WARN: WooCommerce sessions

`woocommerce_sessions` - 9,000 sessions (40.0 MB), 7,000 expired

- WooCommerce removes expired sessions with the twice-daily woocommerce_cleanup_sessions event; a backlog means WP-Cron is not running. WooCommerce > Status > Tools > Clear customer sessions also works, but logs out every customer.

Fix plan: 1 statement(s), see `wp-db-doctor fix --only woocommerce_sessions`.

<a id="missing_indexes"></a>
## CRIT: Missing indexes and primary keys

`missing_indexes` - 3 missing keys on 3 tables

| table | index | columns | severity |
|---|---|---|---|
| shop_posts | AUTO_INCREMENT | ID | critical |
| shop_options | PRIMARY KEY | (option_id) | critical |
| shop_postmeta | meta_key | (meta_key) | warning |

- Missing primary keys or AUTO_INCREMENT usually come from an export/import that dropped table definitions. New posts and options get ID 0 and overwrite each other; fix this before anything else.
- Adding an index rewrites the table. On large tables run it in a quiet period, or use an online schema change tool.

Fix plan: 1 statement(s), see `wp-db-doctor fix --only missing_indexes`.

<a id="engine_collation"></a>
## WARN: Table engines and collations

`engine_collation` - 16 InnoDB, 1 MyISAM; 3 collations (target utf8mb4_unicode_520_ci)

| table | engine | collation | problem |
|---|---|---|---|
| shop_yoast_indexable | MyISAM | utf8_general_ci | not InnoDB |
| shop_yoast_indexable | MyISAM | utf8_general_ci | not utf8mb4 |
| shop_links | InnoDB | utf8mb4_general_ci | differs from utf8mb4_unicode_520_ci |

- MyISAM/Aria tables lock the whole table on every write and are not crash safe. InnoDB is the default since MySQL 5.5 and is what WordPress expects.
- Tables in utf8 (utf8mb3) or latin1 cannot store emoji and many scripts. WordPress converts core tables to utf8mb4 on upgrade only when the server supports it; plugin tables often stay behind.
- Mixed collations cause 'Illegal mix of collations' errors in joins between plugin and core tables.

Fix plan: 1 statement(s), see `wp-db-doctor fix --only engine_collation`.

<a id="table_sizes"></a>
## WARN: Largest tables

`table_sizes` - 19 tables, 1.6 GB total; 2 over 256 MB

| table | total | data | index | rows | note |
|---|---|---|---|---|---|
| shop_postmeta | 1.2 GB | 900.0 MB | 300.0 MB | ~2,400,000 | large, 120.0 MB free |
| shop_actionscheduler_logs | 480.0 MB | 400.0 MB | 80.0 MB | ~1,800,000 | large |
| shop_options | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_posts | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_users | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_usermeta | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_terms | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_termmeta | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_term_taxonomy | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |
| shop_term_relationships | 32.0 KB | 16.0 KB | 16.0 KB | ~100 |  |

- Large tables: shop_postmeta, shop_actionscheduler_logs. Find out what fills them (logs, sessions, analytics and security plugins are common) before deleting anything.
- OPTIMIZE TABLE rebuilds the table to release free space. Run it after the cleanup statements, when traffic is low; it copies the table.
- 2 tables in this schema do not use the shop_ prefix (another site, an old install or a non-WordPress app).

Fix plan: 1 statement(s), see `wp-db-doctor fix --only table_sizes`.

---

Generate the reviewed cleanup SQL with `wp-db-doctor fix -o fix.sql`.

_wp-db-doctor 0.1.0_
