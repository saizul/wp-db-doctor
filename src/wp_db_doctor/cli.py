"""Command-line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from ._version import __version__
from .audit import run_audit, utc_now
from .checks import ALL_CHECKS, UnknownCheckError
from .config import Config, ConfigError, load_config
from .db import ConnectionSettings, Database, DatabaseError, connect
from .fixes import (
    AppliedStatement,
    BackupNotConfirmed,
    apply_plan,
    build_plan,
    confirm_backup,
    render_sql,
)
from .models import Status
from .report import FORMATS, format_for_path, render
from .utils import InvalidPrefixError, format_int, parse_id_list, validate_prefix
from .wpconfig import WPConfigError, load_wp_config, parse_db_host

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_DATABASE = 3

Connector = Callable[[ConnectionSettings, bool], Database]


class CLIError(Exception):
    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


def _default_connector(settings: ConnectionSettings, read_only: bool) -> Database:
    return connect(settings, read_only=read_only)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wp-db-doctor",
        description="Audit a WordPress MySQL/MariaDB database for bloat and problems.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    conn = argparse.ArgumentParser(add_help=False)
    g = conn.add_argument_group("connection")
    g.add_argument("--wp-config", metavar="PATH",
                   help="read credentials and table prefix from wp-config.php "
                        "(a file or the WordPress directory)")
    g.add_argument("--host", help="server host, also accepts host:port or host:/socket")
    g.add_argument("--port", type=int)
    g.add_argument("--socket", help="Unix socket path")
    g.add_argument("-u", "--user")
    g.add_argument("-d", "--database", help="database (schema) name")
    g.add_argument("-p", "--ask-password", action="store_true",
                   help="prompt for the password (otherwise WPDD_DB_PASSWORD, wp-config.php "
                        "or MYSQL_PWD is used)")
    g.add_argument("--prefix", help="table prefix (default: from wp-config.php, else wp_)")
    g.add_argument("--ssl-ca", metavar="FILE", help="CA certificate to require TLS")
    g.add_argument("--connect-timeout", type=int, default=10, metavar="SECONDS")

    sel = argparse.ArgumentParser(add_help=False)
    s = sel.add_argument_group("check selection")
    s.add_argument("--only", metavar="IDS", help="comma separated check ids to run")
    s.add_argument("--skip", metavar="IDS", help="comma separated check ids to skip")
    s.add_argument("-c", "--config", metavar="FILE", help="TOML file with thresholds")

    audit = sub.add_parser("audit", parents=[conn, sel], help="run the checks and report",
                           description="Run the checks and print or write a report.")
    audit.add_argument("-f", "--format", choices=FORMATS,
                       help="report format for standard output (default text)")
    audit.add_argument("-o", "--output", action="append", default=[], metavar="FILE",
                       help="write a report file; format from the extension (.json, .md, "
                            ".html, .txt). May be repeated")
    audit.add_argument("-q", "--quiet", action="store_true",
                       help="do not print the report when writing files")
    audit.add_argument("--fail-on", choices=("info", "warning", "critical", "never"),
                       help="exit with status 1 if any check is at or above this level "
                            "(default never)")

    fix = sub.add_parser(
        "fix", parents=[conn, sel], help="write the cleanup SQL plan (dry run by default)",
        description="Build a cleanup SQL plan from the checks. Nothing is executed unless "
                    "--apply is given and a backup is confirmed.")
    fix.add_argument("-o", "--output", metavar="FILE",
                     help="write the SQL plan to FILE instead of standard output")
    fix.add_argument("--include-schema", action="store_true",
                     help="include ALTER TABLE / OPTIMIZE TABLE statements")
    fix.add_argument("--no-review", action="store_true",
                     help="leave out the commented-out REVIEW suggestions")
    fix.add_argument("--apply", action="store_true",
                     help="execute the plan (REVIEW statements are never executed)")
    fix.add_argument("--i-have-a-backup", action="store_true",
                     help="skip the interactive backup confirmation when using --apply")

    sub.add_parser("checks", help="list the available checks")

    pc = sub.add_parser("parse-config", help="show what is read from wp-config.php "
                                             "(password masked)")
    pc.add_argument("path", help="wp-config.php or the WordPress directory")
    return parser


@dataclass
class Target:
    settings: ConnectionSettings
    prefix: str
    constants: dict[str, Any]


def resolve_target(args: argparse.Namespace, env: Mapping[str, str],
                   ask_password: Callable[[str], str] = getpass.getpass) -> Target:
    settings = ConnectionSettings(connect_timeout=args.connect_timeout)
    prefix = None
    constants: dict[str, Any] = {}
    password: str | None = None
    if args.wp_config:
        try:
            wp = load_wp_config(args.wp_config, env=env)
        except WPConfigError as exc:
            raise CLIError(str(exc)) from exc
        constants = dict(wp.constants)
        spec = parse_db_host(wp.db_host)
        settings.host, settings.port, settings.socket = spec.host, spec.port, spec.socket
        settings.user = wp.db_user
        settings.database = wp.db_name
        password = wp.db_password
        if wp.db_charset:
            settings.charset = wp.db_charset
        prefix = wp.table_prefix
        for key in ("DB_NAME", "DB_USER", "DB_HOST"):
            if key in wp.unresolved:
                print(f"warning: {key} in {wp.path} could not be resolved "
                      f"({wp.unresolved[key]}); pass it on the command line",
                      file=sys.stderr)
    if args.host:
        spec = parse_db_host(args.host)
        settings.host, settings.port, settings.socket = spec.host, spec.port, spec.socket
    if args.port:
        settings.port = args.port
    if args.socket:
        settings.socket = args.socket
    if args.user:
        settings.user = args.user
    if args.database:
        settings.database = args.database
    if args.ssl_ca:
        settings.ssl_ca = args.ssl_ca
    if args.ask_password:
        password = ask_password(f"Password for {settings.describe()}: ")
    elif env.get("WPDD_DB_PASSWORD") is not None:
        password = env["WPDD_DB_PASSWORD"]
    elif password is None:
        password = env.get("MYSQL_PWD")
    settings.password = password
    prefix = args.prefix or prefix or "wp_"
    try:
        validate_prefix(prefix)
    except InvalidPrefixError as exc:
        raise CLIError(str(exc)) from exc
    if not settings.database:
        raise CLIError("no database name: pass --database or --wp-config")
    if not settings.user:
        raise CLIError("no database user: pass --user or --wp-config")
    return Target(settings=settings, prefix=prefix, constants=constants)


def _load_config(args: argparse.Namespace) -> Config:
    if getattr(args, "config", None):
        try:
            return load_config(args.config)
        except ConfigError as exc:
            raise CLIError(str(exc)) from exc
    return Config()


def _selection(args: argparse.Namespace, config: Config) -> tuple[list[str], list[str]]:
    only = parse_id_list(args.only) or config.only
    skip = list(dict.fromkeys(parse_id_list(args.skip) + config.skip))
    return only, skip


def _audit(args: argparse.Namespace, db: Database, target: Target, config: Config):
    only, skip = _selection(args, config)
    try:
        return run_audit(
            db, prefix=target.prefix, schema=target.settings.database,
            host_label=target.settings.describe(), thresholds=config.thresholds,
            only=only, skip=skip, constants=target.constants)
    except UnknownCheckError as exc:
        raise CLIError(str(exc)) from exc
    except DatabaseError as exc:
        raise CLIError(str(exc), EXIT_DATABASE) from exc


def cmd_audit(args, target, config, connector, out: TextIO, err: TextIO) -> int:
    fmt = args.format or "text"
    outputs = []
    for path in args.output:
        out_fmt = format_for_path(path)
        if out_fmt is None:
            raise CLIError(f"cannot tell the format of {path!r}; use .json, .md, .html or .txt")
        outputs.append((path, out_fmt))
    db = _connect(connector, target, read_only=True)
    try:
        report = _audit(args, db, target, config)
    finally:
        db.close()
    for path, out_fmt in outputs:
        try:
            Path(path).write_text(render(report, out_fmt), encoding="utf-8")
        except OSError as exc:
            raise CLIError(f"cannot write {path}: {exc.strerror or exc}") from exc
        print(f"wrote {path}", file=err)
    if not (args.quiet and outputs):
        out.write(render(report, fmt))
    fail_on = args.fail_on or config.fail_on or "never"
    if fail_on != "never" and report.worst.rank >= Status.parse(fail_on).rank:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_fix(args, target, config, connector, out: TextIO, err: TextIO,
            interactive: bool, ask: Callable[[str], str]) -> int:
    db = _connect(connector, target, read_only=not args.apply)
    try:
        report = _audit(args, db, target, config)
        plan = build_plan(report.results, database=report.database.name, prefix=target.prefix,
                          host=report.database.host, created_at=utc_now(),
                          include_schema=args.include_schema,
                          include_review=not args.no_review)
        sql = render_sql(plan, user=target.settings.user)
        if args.output:
            try:
                Path(args.output).write_text(sql, encoding="utf-8")
            except OSError as exc:
                raise CLIError(f"cannot write {args.output}: {exc.strerror or exc}") from exc
            print(f"wrote {args.output}: {len(plan.executable)} statement(s), "
                  f"{len(plan.review_only)} for review", file=err)
        elif not args.apply:
            out.write(sql)
        if not args.apply:
            if plan.executable:
                print("Dry run: nothing was executed. Review the SQL, take a backup, then run "
                      "it yourself or re-run with --apply.", file=err)
            return EXIT_OK
        if not plan.executable:
            print("Nothing to apply.", file=err)
            return EXIT_OK
        try:
            confirm_backup(report.database.name, assume_yes=args.i_have_a_backup,
                           interactive=interactive, ask=ask)
        except BackupNotConfirmed as exc:
            raise CLIError(str(exc)) from exc

        def progress(item: AppliedStatement) -> None:
            if item.error:
                print(f"FAILED  {item.fix.description}: {item.error}", file=err)
            else:
                print(f"ok      {item.fix.description}: {format_int(item.affected or 0)} "
                      "row(s)", file=err)

        applied = apply_plan(db, plan, progress=progress)
    finally:
        db.close()
    failed = [a for a in applied if a.error]
    if failed:
        remaining = len(plan.executable) - len(applied)
        print(f"Stopped after an error; {remaining} statement(s) not run.", file=err)
        return EXIT_DATABASE
    print(f"Applied {len(applied)} statement(s). Run `wp-db-doctor audit` again to compare.",
          file=err)
    return EXIT_OK


def _connect(connector: Connector, target: Target, read_only: bool) -> Database:
    try:
        return connector(target.settings, read_only)
    except DatabaseError as exc:
        raise CLIError(str(exc), EXIT_DATABASE) from exc


def cmd_checks(out: TextIO) -> int:
    width = max(len(c.id) for c in ALL_CHECKS)
    for check in ALL_CHECKS:
        fixable = "fix" if check.fixable else "   "
        out.write(f"{check.id.ljust(width)}  {check.category.ljust(8)}  {fixable}  "
                  f"{check.title}\n")
        out.write(f"{' ' * width}  {check.description}\n")
    return EXIT_OK


def cmd_parse_config(args, env: Mapping[str, str], out: TextIO) -> int:
    try:
        wp = load_wp_config(args.path, env=env)
    except WPConfigError as exc:
        raise CLIError(str(exc)) from exc
    data = wp.masked()
    spec = parse_db_host(wp.db_host)
    data["connection"] = {"host": spec.host, "port": spec.port, "socket": spec.socket}
    out.write(json.dumps(data, indent=2, default=str) + "\n")
    return EXIT_OK


def main(argv: Sequence[str] | None = None, *, connector: Connector | None = None,
         env: Mapping[str, str] | None = None, stdout: TextIO | None = None,
         stderr: TextIO | None = None, interactive: bool | None = None,
         ask: Callable[[str], str] = input,
         ask_password: Callable[[str], str] = getpass.getpass) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = os.environ if env is None else env
    parser = build_parser()
    args = parser.parse_args(argv)
    if interactive is None:
        interactive = sys.stdin.isatty()
    connector = connector or _default_connector
    try:
        if args.command is None:
            parser.print_help(out)
            return EXIT_USAGE
        if args.command == "checks":
            return cmd_checks(out)
        if args.command == "parse-config":
            return cmd_parse_config(args, env, out)
        config = _load_config(args)
        target = resolve_target(args, env, ask_password=ask_password)
        if args.command == "audit":
            return cmd_audit(args, target, config, connector, out, err)
        return cmd_fix(args, target, config, connector, out, err, interactive, ask)
    except CLIError as exc:
        print(f"wp-db-doctor: error: {exc}", file=err)
        return exc.code
    except KeyboardInterrupt:
        print("interrupted", file=err)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
