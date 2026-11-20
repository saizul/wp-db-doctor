# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `oembed_cache` check: counts cached embeds (`oembed_cache` posts and legacy
  `_oembed_*` post meta), their size and the failed lookups stored as
  `{{unknown}}`. Warns above `oembed_cache_warning` (default 2000) entries.
  The fix plan deletes only failed lookups, with their post meta and, for
  legacy meta, the matching `_oembed_time_*` rows; working caches are kept
  because providers such as Facebook and Instagram cannot be fetched again
  without an access token.
- `compare` command: diff two JSON reports without a database connection and
  show checks that got worse, better or changed, numeric metric deltas and
  database size growth. Text, Markdown and JSON output, several output files
  in one run, `--all` to list unchanged checks.
- `--fail-on-regression` and `--max-growth PERCENT` exit codes for `compare`,
  so CI can fail on new problems only instead of on every existing finding.
- Report files are validated on load (tool name, schema version, check ids and
  statuses) with clear error messages.
- The GitHub Actions example keeps the previous report in the cache and
  compares against it.
- `CODE_OF_CONDUCT.md`, issue forms for bug reports and feature requests, and a
  pull request template.

## [0.1.0] - 2026-11-12

First public release.

### Added

- `audit` command with 18 checks: table prefix, server version and buffer
  pool, autoloaded options (with likely owners), expired transients, post
  revisions, auto-drafts, trashed posts, spam and trashed comments, orphaned
  post/term/comment/user meta, orphaned term relationships, Action Scheduler,
  WooCommerce sessions, missing indexes/primary keys/`AUTO_INCREMENT`, table
  engines and collations, and table sizes.
- Text, JSON, Markdown and self-contained HTML reports; several output files
  in one run.
- `fix` command that writes an ordered, commented SQL plan with row estimates.
  Schema changes only with `--include-schema`; risky suggestions as commented
  `REVIEW` lines.
- `--apply` with a typed backup confirmation (or `--i-have-a-backup`), stopping
  at the first failed statement.
- Credentials from `wp-config.php` without running PHP, including
  `getenv_docker()`, `$_ENV`/`$_SERVER`, `host:port`, sockets and IPv6.
- `parse-config` and `checks` helper commands.
- TOML configuration for thresholds and check selection; `--fail-on` exit
  codes for CI.
- Read-only database session for audits.
- Container image published to `ghcr.io/saizul/wp-db-doctor`.

[Unreleased]: https://github.com/saizul/wp-db-doctor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/wp-db-doctor/releases/tag/v0.1.0
