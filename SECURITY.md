# Security policy

## Supported versions

Security fixes are made for the latest release.

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/saizul/wp-db-doctor/security/advisories/new)
for this repository instead. Include the version, the command you ran and how
to reproduce the problem. You should get a reply within a week.

Examples of what counts:

- SQL that the tool generates or executes and that could be influenced by data
  stored in the database (option names, meta keys, table names) in an unsafe way
- credentials appearing in reports, fix plans, error messages or logs
- `--apply` executing anything other than the reviewed plan

## Handling credentials

- Prefer `--wp-config`, `--ask-password` or the `WPDD_DB_PASSWORD` environment
  variable. There is no `--password` option on purpose.
- Audits need only `SELECT` privileges. Use a separate read-only account
  (see `examples/readonly-user.sql`) unless you intend to `--apply`.
- Reports contain table names, option names and sizes, and the fix plan
  contains option names. Treat them as internal documents.
