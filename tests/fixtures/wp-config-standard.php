<?php
/**
 * The base configuration for WordPress (sample values only).
 *
 * define( 'DB_NAME', 'commented_out_in_docblock' );
 */

// ** Database settings ** //
/** The name of the database for WordPress */
define( 'DB_NAME', 'shop_wp' );

/** Database username */
define( 'DB_USER', 'shop_user' );

/** Database password */
define( 'DB_PASSWORD', 'p@ss\'word;with"stuff' );

/** Database hostname */
define( 'DB_HOST', 'localhost:3307' );

/** Database charset to use in creating database tables. */
define( 'DB_CHARSET', 'utf8mb4' );

/** The database collate type. Don't change this if in doubt. */
define( 'DB_COLLATE', '' );

# define( 'DB_USER', 'hash_comment_user' );
// define( 'DB_HOST', 'slash-comment-host' );

define( 'AUTH_KEY',         'put your unique phrase here' );
define( 'SECURE_AUTH_KEY',  'put your unique phrase here' );

$table_prefix = 'shop_';

define( 'WP_DEBUG', false );
define( 'WP_POST_REVISIONS', 5 );
define( 'EMPTY_TRASH_DAYS', 7 );
define( 'DISABLE_WP_CRON', true );

/* That's all, stop editing! Happy publishing. */

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
