<?php
/* Edge cases (sample values only). */
define("DB_NAME", "tricky_" . "db");
define ( "DB_USER" , "user\x41" ) ;
define('DB_PASSWORD', "pa\$\$word");
define('DB_HOST', 'p:[::1]:3308');
define('DB_NAME', 'second_definition_is_ignored');
define('WP_HOME', 'https://' . $_SERVER['HTTP_HOST']);
define('WP_SITEURL', "https://{$host}/wp");
define('WP_MEMORY_LIMIT', '256M'); define('MULTISITE', true);
define('AUTOSAVE_INTERVAL', 300);
$table_prefix  = "first_";
$table_prefix  = 'final_';
?>
<p>Inline HTML after the closing tag is ignored.</p>
