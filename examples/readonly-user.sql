-- A least-privilege MySQL/MariaDB account for audits (wp-db-doctor audit and fix
-- without --apply). Replace the names and choose a strong password.
CREATE USER 'wpdd_audit'@'localhost' IDENTIFIED BY 'change-me';
GRANT SELECT ON `wordpress`.* TO 'wpdd_audit'@'localhost';
-- information_schema is readable for objects the account has privileges on;
-- nothing else is needed.
