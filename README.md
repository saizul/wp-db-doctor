# wp-db-doctor

[![CI](https://github.com/saizul/wp-db-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/wp-db-doctor/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Find out what is bloating or breaking a WordPress database, and get cleanup SQL
you can review before anything runs.

A slow wp-admin, a 2 GB `wp_postmeta`, a WooCommerce store whose
`wp_actionscheduler_actions` table has millions of rows, an import that lost
its primary keys: all of these show up in the database long before they show up
in a ticket. `wp-db-doctor` connects to MySQL or MariaDB (with your
`wp-config.php` or plain credentials), runs read-only checks and writes a
report in text, JSON, Markdown or HTML.

It never changes anything by default. `wp-db-doctor fix` writes a commented SQL
plan for you to read; it only executes with `--apply`, after you confirm that a
backup exists.

## Features

- **18 checks**: autoloaded options size with the largest offenders and their
  likely owner, expired transients, post revisions, auto-drafts, trashed
  posts, spam and trashed comments, orphaned post/term/comment/user meta,
  orphaned term relationships, Action Scheduler backlog (completed, failed,
  canceled, overdue), WooCommerce sessions, missing indexes and primary keys
  (including lost `AUTO_INCREMENT`), table engines and collations, the largest
  and most fragmented tables, the table prefix (multisite and other installs
  in the same schema) and the server version against its end-of-life date.
- **Reports**: terminal text, JSON (stable schema for scripts and CI), Markdown
  (for tickets and pull requests) and a self-contained HTML file (inline CSS,
  no JavaScript, no external requests, light and dark mode).
- **Fix plans**: ordered SQL (content first, then the metadata it leaves
  behind, then schema work), with row estimates and a backup reminder.
  Risky suggestions such as turning off autoload for a plugin option or
  converting a collation are written as commented `REVIEW` lines and are never
  executed. Schema changes are left out unless you ask for them.
- **Safe by default**: audits run in a read-only session; the plan is a dry run;
  `--apply` asks you to type the database name to confirm a backup.
- **Connections**: TCP, Unix socket, TLS (`--ssl-ca`), or credentials parsed
  from `wp-config.php` without running PHP (supports the official Docker
  image's `getenv_docker()`, `host:port`, `host:/socket`, IPv6 and custom
  table prefixes).
- **Configurable** thresholds and check selection in a TOML file; exit codes
  for CI with `--fail-on`.

## Installation

The project is not on PyPI yet. Install it from a checkout:

```console
$ git clone https://github.com/saizul/wp-db-doctor.git
$ cd wp-db-doctor
$ python -m pip install .
```

This installs the `wp-db-doctor` command (and its one dependency, PyMySQL).
`python -m wp_db_doctor` works too. Python 3.9 and 3.10 also pull in `tomli`
for the configuration file.

## Quick start

Run it on the server, pointing at the site:

```console
$ wp-db-doctor audit --wp-config /var/www/example.com/public_html
```

`--wp-config` accepts the file or the WordPress directory (like WordPress, it
also looks one level up). The database name, user, password, host and table
prefix are read from it. Check what was found with:

```console
$ wp-db-doctor parse-config /var/www/example.com/public_html
{
  "path": "/var/www/example.com/public_html/wp-config.php",
  "DB_NAME": "example_wp",
  "DB_USER": "example_user",
  "DB_PASSWORD": "********",
  "DB_HOST": "localhost",
  ...
}
```

Without a `wp-config.php` (for example from your laptop through an SSH tunnel):

```console
$ export WPDD_DB_PASSWORD='...'
$ wp-db-doctor audit --host 127.0.0.1:3307 --user example_user --database example_wp --prefix wp_
```

Part of a terminal report:

```text
[CRIT]   Autoloaded options (autoload)
         4.0 MB autoloaded across 1,450 options

         option                          size      share  likely owner
         ------------------------------  --------  -----  ------------------------------------
         wpseo_premium_redirects_export  1.6 MB    39.1%  Yoast SEO
         cron                            900.0 KB  22.0%  WordPress core cron events (...)
         _transient_feed_abc             300.0 KB  7.3%   transient (should not be autoloaded)
         > Every page load reads all autoloaded options; with a persistent object cache the
           whole set is one cache item, so keep it well under 1 MB.

[CRIT]   Action Scheduler (action_scheduler)
         603,500 actions, 582,500 finished more than 30 days ago, 450 overdue
         ...
Summary: 5 critical, 10 warning, 1 info, 2 ok
26 cleanup statement(s) available: run `wp-db-doctor fix` to write the SQL plan for review.
```

Full examples generated from synthetic data are in
[`examples/`](examples/): [Markdown report](examples/sample-report.md),
[HTML report](examples/sample-report.html),
[JSON report](examples/sample-report.json) and
[fix plan](examples/sample-fix.sql).

## Usage

```text
wp-db-doctor audit         [connection] [selection] [-f FORMAT] [-o FILE ...] [-q] [--fail-on LEVEL]
wp-db-doctor fix           [connection] [selection] [-o FILE] [--include-schema] [--no-review]
                           [--apply [--i-have-a-backup]]
wp-db-doctor checks        list the checks
wp-db-doctor parse-config  PATH   show what is read from wp-config.php (password masked)
```

### Connection options

| Option | Meaning |
|---|---|
| `--wp-config PATH` | Read `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_CHARSET`, `DB_COLLATE`, `$table_prefix` and other constants from `wp-config.php` |
| `--host HOST` | Host name; `host:port`, `host:/path/to.sock` and `[ipv6]:port` work too |
| `--port`, `--socket` | Port, or Unix socket path |
| `-u`, `--user` / `-d`, `--database` | Account and schema |
| `-p`, `--ask-password` | Prompt for the password |
| `--prefix` | Table prefix (default: from `wp-config.php`, else `wp_`) |
| `--ssl-ca FILE` | Require TLS and verify the server with this CA |
| `--connect-timeout SECONDS` | Default 10 |

Command-line options override values from `wp-config.php`. The password is
taken from, in order: `--ask-password`, the `WPDD_DB_PASSWORD` environment
variable, `wp-config.php`, then `MYSQL_PWD`. There is deliberately no
`--password` option, because command-line arguments are visible to other users
in the process list.

A read-only account is enough for `audit` and for `fix` without `--apply`; see
[`examples/readonly-user.sql`](examples/readonly-user.sql).

### Reports

```console
# Text on the terminal (default)
$ wp-db-doctor audit --wp-config .

# JSON on standard output, for scripts
$ wp-db-doctor audit --wp-config . -f json | jq '.checks[] | select(.status == "critical") | .id'

# Files; the format follows the extension (.txt, .json, .md, .html). -q hides the terminal report
$ wp-db-doctor audit --wp-config . -o report.html -o report.json -q

# Only some checks
$ wp-db-doctor audit --wp-config . --only autoload,expired_transients,action_scheduler
$ wp-db-doctor audit --wp-config . --skip table_sizes
```

### Fix plans

```console
# Dry run: print the plan
$ wp-db-doctor fix --wp-config .

# Write it to a file, including ALTER TABLE / OPTIMIZE TABLE statements
$ wp-db-doctor fix --wp-config . --include-schema -o fix.sql

# Plan for one check only
$ wp-db-doctor fix --wp-config . --only woocommerce_sessions
```

Read the file, take a backup (`wp db export` or `mysqldump --single-transaction`),
then run it yourself with `mysql example_wp < fix.sql`, or let the tool do it:

```console
$ wp-db-doctor fix --wp-config . --apply
Applying changes to 'example_wp'. Take a backup first (e.g. `wp db export before-wp-db-doctor.sql`).
Type the database name to confirm a current backup exists: example_wp
ok      Delete 7,000 expired WooCommerce sessions: 7,000 row(s)
...
```

`--apply` executes the statements one by one, prints the affected rows, and
stops at the first error. `REVIEW` statements are never executed. In scripts,
`--i-have-a-backup` replaces the prompt; without a terminal and without that
flag, `--apply` refuses to run.

How the plan is built:

1. Deleting a post (revision, auto-draft, trashed post) also deletes its post
   meta and term relationships, and for trashed posts its comments and comment
   meta, children first.
2. Spam and trashed comments, with their comment meta.
3. Expired transients, using the same paired delete as WordPress's
   `delete_expired_transients()`.
4. Action Scheduler logs and finished actions older than the retention period,
   expired WooCommerce sessions.
5. Orphaned meta and term relationships.
6. Schema changes (only with `--include-schema`): missing indexes, InnoDB
   conversion, then `OPTIMIZE TABLE` for fragmented tables.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or no check at or above `--fail-on`) |
| 1 | A check reached the `--fail-on` level |
| 2 | Usage or configuration error, or `--apply` not confirmed |
| 3 | Connection or query error, or a statement failed during `--apply` |

`--fail-on` takes `info`, `warning`, `critical` or `never` (default).

## Check reference

| ID | Category | What it looks at | Fix plan |
|---|---|---|---|
| `table_prefix` | schema | The prefix matches `options`/`posts` tables; other installs and multisite sub-sites in the schema; missing core tables | none |
| `server_version` | server | MySQL/MariaDB version against end-of-life dates; InnoDB buffer pool smaller than the InnoDB data | none |
| `autoload` | options | Total size of autoloaded options (`yes`, `on`, `auto`, `auto-on`) and the largest ones, with the likely owning plugin | REVIEW: `autoload = 'off'` for large non-core options |
| `expired_transients` | options | Transients and site transients past their timeout | delete value and timeout rows |
| `post_revisions` | content | Revision count and size, posts with most revisions, `WP_POST_REVISIONS` | delete revisions older than 30 days with their meta |
| `auto_drafts` | content | Auto-drafts older than 7 days (a sign WP-Cron is broken) | delete them with their meta |
| `trashed_posts` | content | Trashed posts by post type | delete posts trashed more than 30 days ago, with meta, terms and comments |
| `spam_comments` | content | Spam, trash, post-trashed and pending comments | delete spam and trash with their meta |
| `orphaned_postmeta` | orphans | `postmeta` rows without a post, top meta keys | delete |
| `orphaned_termmeta` | orphans | `termmeta` rows without a term | delete |
| `orphaned_commentmeta` | orphans | `commentmeta` rows without a comment | delete |
| `orphaned_usermeta` | orphans | `usermeta` rows without a user | delete |
| `orphaned_term_relationships` | orphans | Relationships to missing posts (link categories excluded) or missing taxonomy rows | delete |
| `action_scheduler` | queues | Actions per status, finished actions older than the retention period, overdue pending actions, most failed hooks | delete old finished actions and their logs, orphaned logs |
| `woocommerce_sessions` | queues | Sessions and expired sessions | delete expired |
| `missing_indexes` | schema | Primary keys, `AUTO_INCREMENT` and secondary indexes of core tables, Action Scheduler and WooCommerce sessions | `ADD INDEX`; REVIEW for primary keys and unique indexes |
| `engine_collation` | schema | Non-InnoDB tables, non-utf8mb4 tables, collations that differ from `DB_COLLATE` or the majority | `ENGINE=InnoDB`; REVIEW for `CONVERT TO CHARACTER SET` |
| `table_sizes` | schema | Largest tables, tables over the size threshold, free space to reclaim, tables from other prefixes | `OPTIMIZE TABLE` |

Statuses, from least to most urgent: `ok`, `skipped` (a table the check needs
does not exist, for example Action Scheduler without WooCommerce), `info`,
`warning`, `error` (the query failed, usually missing privileges) and
`critical`.

## Configuration

Thresholds and check selection can be set in a TOML file passed with `-c`:

```toml
[thresholds]
autoload_warning_kb = 800
revisions_min_age_days = 14
action_scheduler_retention_days = 7
large_table_mb = 512

[checks]
skip = ["table_prefix"]
fail_on = "critical"
```

[`examples/wp-db-doctor.toml`](examples/wp-db-doctor.toml) lists every key
with its default. Unknown keys are an error, so typos do not pass silently.
Command-line `--only`, `--skip` and `--fail-on` take precedence.

## Examples

Weekly report by e-mail from cron on the web server:

```cron
0 6 * * 1  wp-db-doctor audit --wp-config /var/www/shop -f markdown | mail -s "DB report" ops@example.com
```

Fail a deployment pipeline if a staging database has a critical problem, see
[`examples/github-actions-audit.yml`](examples/github-actions-audit.yml).

Audit every site on a VPS:

```bash
for cfg in /var/www/*/public_html/wp-config.php; do
  site=$(basename "$(dirname "$(dirname "$cfg")")")
  wp-db-doctor audit --wp-config "$cfg" -o "reports/$site.html" -q
done
```

## Testing

```console
$ python -m pip install -e ".[dev]"
$ ruff check .
$ pytest
```

The tests need no database. Every query starts with a `/* wpdd:<tag> */`
comment; a fake database in `tests/conftest.py` answers by tag with fixtures for
a clean site and a bloated WooCommerce store, and fails on any query it has no
answer for. The DB-API adapter is tested against a fake cursor, and
`wp-config.php` parsing against fixture files (standard, Docker and edge
cases). The tag comment also makes the tool's queries easy to spot in the
MySQL process list and slow query log.

## Limitations

- **Read the plan.** Direct SQL skips WordPress and plugin hooks. Caches,
  search indexes (for example Yoast indexables or ElasticPress) and term or
  comment counts are not updated; the recommendations say which WP-CLI command
  refreshes what.
- **Row counts are estimates** where they come from `information_schema`
  (`TABLE_ROWS` is approximate for InnoDB); counts from the checks' own queries
  are exact at the time of the audit.
- **Some checks scan large tables.** Orphan and transient checks read the whole
  meta or options table. On very large databases run the audit against a
  replica or in a quiet period.
- **Multisite**: the main site's tables are audited. Audit a sub-site with
  `--prefix wp_2_`; network tables (`wp_blogs`, `wp_sitemeta`) are not checked.
- **WooCommerce HPOS** order tables (`wc_orders`, `wc_orders_meta`) are not
  checked yet, and trashed orders are cleaned up through the posts table only.
- **Deleting revisions keeps none**: the plan deletes revisions older than the
  age threshold rather than keeping the latest N per post, so that it works on
  servers without window functions.
- **`wp-config.php` is not executed.** Values computed at runtime (other than
  `getenv()`, `getenv_docker()`, `$_ENV`/`$_SERVER` and string concatenation)
  are reported as unresolved; pass them on the command line instead.
- **Primary key and collation fixes are suggestions only.** Adding a primary key
  needs duplicate and zero IDs removed first, and converting a character set
  can fail on long indexed columns. Test them on a copy.
- **Server coverage**: the queries use syntax common to MySQL 5.7+ and
  MariaDB 10.4+. The test suite runs against a fake database, not a live
  server, so please report any query that a particular server rejects.
  Percona Server and Aurora MySQL should behave like MySQL.

## Contributing

Bug reports and new checks are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
Security issues: see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Saizul Amin.

## Docker image

A multi-arch image (linux/amd64, linux/arm64) is published to GitHub Packages:

```bash
docker pull ghcr.io/saizul/wp-db-doctor:latest
docker run --rm ghcr.io/saizul/wp-db-doctor:latest --help
```

The container needs to reach the database server. For a database on the
Docker host use `--network host` (Linux); mount the WordPress directory to read
`wp-config.php`, and a folder at `/work` for report files:

```bash
docker run --rm --network host \
  -v /var/www/example.com/public_html:/site:ro \
  -v "$PWD:/work" \
  ghcr.io/saizul/wp-db-doctor:latest audit --wp-config /site -o report.html
```

For a database in another container, join its network
(`--network <compose-network>`) and pass `--host db`. Pass passwords with
`-e WPDD_DB_PASSWORD`, not on the command line.
