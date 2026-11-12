-- wp-db-doctor 0.1.0 fix plan
-- Database: shop_wp (prefix shop_) on shop_user@localhost:3307
-- Created: 2026-11-12T08:00:00Z
--
-- READ EVERY STATEMENT BEFORE RUNNING IT. Direct SQL skips WordPress and plugin
-- hooks. Take a backup first, for example:
--   wp db export before-wp-db-doctor.sql
--   mysqldump --single-transaction --quick --routines --triggers -u shop_user -p shop_wp > before-wp-db-doctor.sql
--
-- Statements marked REVIEW are suggestions and are commented out on purpose.
-- 7 schema change(s) left out; add --include-schema to see ALTER/OPTIMIZE TABLE statements.

-- ===== post_revisions =====
-- Delete post meta of 21,000 revisions older than 30 days
DELETE pm FROM `shop_postmeta` pm INNER JOIN `shop_posts` p ON p.ID = pm.post_id WHERE p.post_type = 'revision' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete term relationships of 21,000 revisions older than 30 days
DELETE tr FROM `shop_term_relationships` tr INNER JOIN `shop_posts` p ON p.ID = tr.object_id WHERE p.post_type = 'revision' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete 21,000 revisions older than 30 days (about 21,000 rows)
DELETE p FROM `shop_posts` p WHERE p.post_type = 'revision' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- ===== auto_drafts =====
-- Delete post meta of 400 auto-drafts older than 7 days
DELETE pm FROM `shop_postmeta` pm INNER JOIN `shop_posts` p ON p.ID = pm.post_id WHERE p.post_status = 'auto-draft' AND p.post_date < NOW() - INTERVAL 7 DAY;

-- Delete term relationships of 400 auto-drafts older than 7 days
DELETE tr FROM `shop_term_relationships` tr INNER JOIN `shop_posts` p ON p.ID = tr.object_id WHERE p.post_status = 'auto-draft' AND p.post_date < NOW() - INTERVAL 7 DAY;

-- Delete 400 auto-drafts older than 7 days (about 400 rows)
DELETE p FROM `shop_posts` p WHERE p.post_status = 'auto-draft' AND p.post_date < NOW() - INTERVAL 7 DAY;

-- ===== trashed_posts =====
-- Delete post meta of 200 trashed posts older than 30 days
DELETE pm FROM `shop_postmeta` pm INNER JOIN `shop_posts` p ON p.ID = pm.post_id WHERE p.post_status = 'trash' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete term relationships of 200 trashed posts older than 30 days
DELETE tr FROM `shop_term_relationships` tr INNER JOIN `shop_posts` p ON p.ID = tr.object_id WHERE p.post_status = 'trash' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete comment meta of comments on 200 trashed posts older than 30 days
DELETE cm FROM `shop_commentmeta` cm INNER JOIN `shop_comments` c ON c.comment_ID = cm.comment_id INNER JOIN `shop_posts` p ON p.ID = c.comment_post_ID WHERE p.post_status = 'trash' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete comments on 200 trashed posts older than 30 days
DELETE c FROM `shop_comments` c INNER JOIN `shop_posts` p ON p.ID = c.comment_post_ID WHERE p.post_status = 'trash' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete 200 trashed posts older than 30 days (about 200 rows)
DELETE p FROM `shop_posts` p WHERE p.post_status = 'trash' AND p.post_modified_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- ===== spam_comments =====
-- Delete comment meta of spam and trashed comments
DELETE cm FROM `shop_commentmeta` cm INNER JOIN `shop_comments` c ON c.comment_ID = cm.comment_id WHERE c.comment_approved IN ('spam', 'trash');

-- Delete 60,040 spam or trashed comments (about 60,040 rows)
DELETE c FROM `shop_comments` c WHERE c.comment_approved IN ('spam', 'trash');

-- ===== autoload =====
-- REVIEW: Stop autoloading wpseo_premium_redirects_export (1.6 MB); confirm the owning plugin reads it with get_option() only where needed (about 1 row)
-- UPDATE `shop_options` SET autoload = 'off' WHERE option_name = 'wpseo_premium_redirects_export';

-- REVIEW: Stop autoloading old_plugin_cache (200.0 KB); confirm the owning plugin reads it with get_option() only where needed (about 1 row)
-- UPDATE `shop_options` SET autoload = 'off' WHERE option_name = 'old_plugin_cache';

-- ===== expired_transients =====
-- Delete 1,800 expired transients (value and timeout rows), as delete_expired_transients() does (about 3,600 rows)
DELETE a, b FROM `shop_options` a INNER JOIN `shop_options` b ON b.option_name = CONCAT('_transient_', SUBSTRING(a.option_name, 20)) WHERE a.option_name LIKE '\_transient\_timeout\_%' AND CAST(a.option_value AS UNSIGNED) < UNIX_TIMESTAMP();

-- Delete 25 expired site transients (about 50 rows)
DELETE a, b FROM `shop_options` a INNER JOIN `shop_options` b ON b.option_name = CONCAT('_site_transient_', SUBSTRING(a.option_name, 25)) WHERE a.option_name LIKE '\_site\_transient\_timeout\_%' AND CAST(a.option_value AS UNSIGNED) < UNIX_TIMESTAMP();

-- ===== action_scheduler =====
-- Delete log entries of finished actions older than 30 days
DELETE l FROM `shop_actionscheduler_logs` l INNER JOIN `shop_actionscheduler_actions` a ON a.action_id = l.action_id WHERE a.status IN ('complete', 'failed', 'canceled') AND a.scheduled_date_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- Delete 582,500 finished actions older than 30 days (complete, failed, canceled) (about 582,500 rows)
DELETE FROM `shop_actionscheduler_actions` WHERE status IN ('complete', 'failed', 'canceled') AND scheduled_date_gmt < UTC_TIMESTAMP() - INTERVAL 30 DAY;

-- ===== woocommerce_sessions =====
-- Delete 7,000 expired WooCommerce sessions (about 7,000 rows)
DELETE FROM `shop_woocommerce_sessions` WHERE session_expiry < UNIX_TIMESTAMP();

-- ===== action_scheduler =====
-- Delete log entries whose action no longer exists
DELETE l FROM `shop_actionscheduler_logs` l LEFT JOIN `shop_actionscheduler_actions` a ON a.action_id = l.action_id WHERE a.action_id IS NULL;

-- ===== orphaned_postmeta =====
-- Delete 54,000 orphaned postmeta rows whose post no longer exists (about 54,000 rows)
DELETE m FROM `shop_postmeta` m LEFT JOIN `shop_posts` p ON p.ID = m.post_id WHERE p.ID IS NULL;

-- ===== orphaned_termmeta =====
-- Delete 3 orphaned termmeta rows whose term no longer exists (about 3 rows)
DELETE m FROM `shop_termmeta` m LEFT JOIN `shop_terms` p ON p.term_id = m.term_id WHERE p.term_id IS NULL;

-- ===== orphaned_term_relationships =====
-- Delete 1,200 term relationships of deleted posts (about 1,200 rows)
DELETE r FROM `shop_term_relationships` r INNER JOIN `shop_term_taxonomy` tt ON tt.term_taxonomy_id = r.term_taxonomy_id LEFT JOIN `shop_posts` p ON p.ID = r.object_id WHERE p.ID IS NULL AND tt.taxonomy <> 'link_category';

-- Delete 4 term relationships of deleted terms (about 4 rows)
DELETE r FROM `shop_term_relationships` r LEFT JOIN `shop_term_taxonomy` tt ON tt.term_taxonomy_id = r.term_taxonomy_id WHERE tt.term_taxonomy_id IS NULL;
