# Contributing

Thanks for taking the time to improve wp-db-doctor. Bug reports, new checks,
better recommendations and documentation fixes are all welcome.

## Reporting a bug

Open an issue with:

- the command you ran and the full output or traceback
- your Python version, and the database server and version
  (`SELECT VERSION();`)
- for a wrong result, the relevant part of `wp-db-doctor audit -f json`

**Never paste credentials, `wp-config.php` contents or real customer data.**
`wp-db-doctor parse-config` masks the password; table and option names are
usually enough to reproduce a problem.

## Development setup

You need Python 3.9 or newer. No database server is needed for the tests.

```console
$ git clone https://github.com/saizul/wp-db-doctor.git
$ cd wp-db-doctor
$ python -m venv .venv
$ . .venv/bin/activate
$ python -m pip install -e ".[dev]"
```

Then, before every commit:

```console
$ ruff check .
$ pytest
```

Both must pass; CI runs them on Python 3.9 to 3.12.

## Guidelines

- **Read-only checks.** A check may only run `SELECT` queries. Anything that
  changes data goes into `FixStatement`s, which are written to the plan.
- **Tag every query** with a leading `/* wpdd:<tag> */` comment and add a
  response for the tag to the fixtures in `tests/conftest.py`. The fake database
  rejects untagged queries and queries it has no answer for.
- **Mind the `%` sign.** Queries sent with parameters must not contain a literal
  `%` (PyMySQL formats them with `%`); pass `LIKE` patterns as parameters, or
  send the query without parameters. The fake database checks this.
- **Quote identifiers** with `ctx.t("posts")` / `quote_ident()`, and values in
  fix plans with `sql_literal()`. Never build SQL from unvalidated input.
- **Fix ordering matters.** Delete children (meta, relationships) before their
  parents, and give each statement the right `stage` so the plan runs content
  cleanup before orphan cleanup and schema work.
- **Anything that needs judgement is `review_only=True`.**
- **Every change comes with a test** that fails without it.
- Keep lines within 100 characters and let `ruff check .` decide style.
- Add a line to the `Unreleased` section of `CHANGELOG.md` for anything a user
  would notice.

## Project layout

```text
src/wp_db_doctor/
    wpconfig.py    wp-config.php tokenizer and DB_HOST parsing
    db.py          DB-API adapter and PyMySQL connection
    config.py      thresholds and TOML loading
    models.py      Status, CheckResult, FixStatement, Report
    checks/        base (context, grading), options, content, orphans,
                   queues, schema, and the registry in __init__.py
    audit.py       runs checks and builds the report
    fixes.py       fix plan ordering, SQL rendering, backup prompt, apply
    report/        text, JSON, Markdown and HTML renderers
    cli.py         command-line interface
examples/          sample reports, fix plan, config and CI workflow
tests/             pytest suite with a fake database and wp-config fixtures
```

## Adding a check

1. Subclass `Check` in the module that fits (or a new one), set `id`, `title`,
   `category`, `description`, `requires` (tables without prefix) and `fixable`.
2. Implement `run(ctx)` and return `self.result(status, summary, ...)`.
3. Register it in `ALL_CHECKS` in `checks/__init__.py`.
4. Add fixture responses for a clean and a bloated site, tests for each status,
   and a row in the README check reference.

## Pull requests

Keep pull requests focused on one change, describe what it does and why, and
mention anything you could not test (for example a server version you do not
have). By contributing you agree that your work is released under the MIT
license of this project.
