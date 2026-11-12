from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from wp_db_doctor.cli import EXIT_DATABASE, EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from wp_db_doctor.db import DatabaseError

from .conftest import FakeDatabase, bloated_responses, clean_responses

FIXTURES = Path(__file__).parent / "fixtures"


class Harness:
    def __init__(self, responses=None):
        self.db = FakeDatabase(responses or bloated_responses())
        self.settings = None
        self.read_only = None
        self.out = io.StringIO()
        self.err = io.StringIO()

    def connector(self, settings, read_only):
        self.settings = settings
        self.read_only = read_only
        return self.db

    def run(self, *argv, env=None, interactive=False, ask=None, ask_password=None):
        return main(list(argv), connector=self.connector, env=env or {}, stdout=self.out,
                    stderr=self.err, interactive=interactive, ask=ask or input,
                    ask_password=ask_password or (lambda _: "prompted"))


BASE = ("-u", "wpuser", "-d", "wordpress")


def test_audit_text_default():
    h = Harness()
    assert h.run("audit", *BASE) == EXIT_OK
    assert "Autoloaded options" in h.out.getvalue()
    assert h.read_only is True
    assert h.db.closed


def test_audit_fail_on():
    h = Harness()
    assert h.run("audit", *BASE, "--fail-on", "warning") == EXIT_FINDINGS
    h = Harness(clean_responses())
    assert h.run("audit", *BASE, "--fail-on", "warning") == EXIT_OK
    h = Harness(clean_responses())
    assert h.run("audit", *BASE, "--fail-on", "info") == EXIT_FINDINGS


def test_audit_json_stdout():
    h = Harness()
    h.run("audit", *BASE, "-f", "json", "--only", "autoload,table_sizes")
    data = json.loads(h.out.getvalue())
    assert [c["id"] for c in data["checks"]] == ["autoload", "table_sizes"]


def test_audit_writes_files(tmp_path):
    h = Harness()
    html = tmp_path / "r.html"
    js = tmp_path / "r.json"
    assert h.run("audit", *BASE, "-o", str(html), "-o", str(js), "-q") == EXIT_OK
    assert html.read_text().startswith("<!DOCTYPE html>")
    assert json.loads(js.read_text())["database"]["name"] == "wordpress"
    assert h.out.getvalue() == ""


def test_audit_unknown_output_extension():
    h = Harness()
    assert h.run("audit", *BASE, "-o", "report.pdf") == EXIT_USAGE
    assert "cannot tell the format" in h.err.getvalue()


def test_audit_unknown_check():
    h = Harness()
    assert h.run("audit", *BASE, "--only", "nope") == EXIT_USAGE
    assert "unknown check" in h.err.getvalue()


def test_wp_config_supplies_connection():
    h = Harness(clean_responses(prefix="shop_"))
    code = h.run("audit", "--wp-config", str(FIXTURES / "wp-config-standard.php"))
    assert code == EXIT_OK
    s = h.settings
    assert (s.host, s.port, s.user, s.database) == ("localhost", 3307, "shop_user", "shop_wp")
    assert s.password == "p@ss'word;with\"stuff"
    assert "prefix" in h.out.getvalue() and "shop_" in h.out.getvalue()


def test_cli_overrides_wp_config_and_env_password():
    h = Harness(clean_responses(prefix="x_"))
    h.run("audit", "--wp-config", str(FIXTURES / "wp-config-standard.php"),
          "--host", "db.internal:3310", "--prefix", "x_", "-d", "other",
          env={"WPDD_DB_PASSWORD": "from-env"})
    s = h.settings
    assert (s.host, s.port, s.database) == ("db.internal", 3310, "other")
    assert s.password == "from-env"


def test_ask_password():
    h = Harness()
    h.run("audit", *BASE, "-p")
    assert h.settings.password == "prompted"


def test_mysql_pwd_fallback():
    h = Harness()
    h.run("audit", *BASE, env={"MYSQL_PWD": "legacy"})
    assert h.settings.password == "legacy"


def test_missing_database_name():
    h = Harness()
    assert h.run("audit", "-u", "x") == EXIT_USAGE
    assert "no database name" in h.err.getvalue()


def test_invalid_prefix():
    h = Harness()
    assert h.run("audit", *BASE, "--prefix", "wp-") == EXIT_USAGE


def test_connection_error_exit_code():
    def failing(settings, read_only):
        raise DatabaseError("cannot connect to wpuser@localhost: MySQL error 2003: refused")

    err = io.StringIO()
    code = main(["audit", *BASE], connector=failing, env={}, stdout=io.StringIO(), stderr=err)
    assert code == EXIT_DATABASE
    assert "2003" in err.getvalue()


def test_fix_dry_run_prints_sql_and_executes_nothing():
    h = Harness()
    assert h.run("fix", *BASE) == EXIT_OK
    sql = h.out.getvalue()
    assert "DELETE p FROM `wp_posts` p" in sql
    assert "ALTER TABLE" not in sql
    assert h.db.executed == []
    assert h.read_only is True
    assert "Dry run" in h.err.getvalue()


def test_fix_include_schema_to_file(tmp_path):
    h = Harness()
    out = tmp_path / "fix.sql"
    assert h.run("fix", *BASE, "--include-schema", "-o", str(out)) == EXIT_OK
    text = out.read_text()
    assert "ALTER TABLE `wp_postmeta` ADD INDEX `meta_key`" in text
    assert "OPTIMIZE TABLE" in text
    assert "statement(s)" in h.err.getvalue()


def test_fix_apply_requires_backup_confirmation():
    h = Harness()
    assert h.run("fix", *BASE, "--apply") == EXIT_USAGE
    assert "--i-have-a-backup" in h.err.getvalue()
    assert h.db.executed == []


def test_fix_apply_interactive_wrong_answer():
    h = Harness()
    assert h.run("fix", *BASE, "--apply", interactive=True, ask=lambda _: "nope") == EXIT_USAGE
    assert h.db.executed == []


def test_fix_apply_interactive_confirmed():
    h = Harness()
    code = h.run("fix", *BASE, "--apply", "--only", "woocommerce_sessions",
                 interactive=True, ask=lambda _: "wordpress")
    assert code == EXIT_OK
    assert h.read_only is False
    assert h.db.executed == ["DELETE FROM `wp_woocommerce_sessions` "
                             "WHERE session_expiry < UNIX_TIMESTAMP()"]
    assert "Applied 1 statement(s)" in h.err.getvalue()


def test_fix_apply_with_flag_and_error():
    h = Harness()
    h.db.fail_execute_on = "wp_comments"
    code = h.run("fix", *BASE, "--apply", "--i-have-a-backup")
    assert code == EXIT_DATABASE
    assert "FAILED" in h.err.getvalue()
    assert "not run" in h.err.getvalue()


def test_fix_apply_nothing_to_do():
    h = Harness(clean_responses())
    assert h.run("fix", *BASE, "--apply", "--i-have-a-backup") == EXIT_OK
    assert "Nothing to apply" in h.err.getvalue()


def test_config_file(tmp_path):
    cfg = tmp_path / "wpdd.toml"
    cfg.write_text('[thresholds]\nautoload_warning_kb = 100\n[checks]\nskip = ["table_sizes"]\n'
                   'fail_on = "warning"\n', encoding="utf-8")
    h = Harness(clean_responses())
    assert h.run("audit", *BASE, "-c", str(cfg), "-f", "json") == EXIT_FINDINGS
    ids = [c["id"] for c in json.loads(h.out.getvalue())["checks"]]
    assert "table_sizes" not in ids


def test_bad_config_file(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("[thresholds]\nnot_a_threshold = 1\n", encoding="utf-8")
    h = Harness()
    assert h.run("audit", *BASE, "-c", str(cfg)) == EXIT_USAGE


def test_checks_command():
    h = Harness()
    assert h.run("checks") == EXIT_OK
    assert "orphaned_postmeta" in h.out.getvalue()


def test_parse_config_masks_password():
    h = Harness()
    assert h.run("parse-config", str(FIXTURES / "wp-config-standard.php")) == EXIT_OK
    data = json.loads(h.out.getvalue())
    assert data["DB_PASSWORD"] == "********"
    assert data["connection"] == {"host": "localhost", "port": 3307, "socket": None}
    assert "p@ss" not in h.out.getvalue()


def test_parse_config_missing(tmp_path):
    h = Harness()
    assert h.run("parse-config", str(tmp_path)) == EXIT_USAGE


def test_no_command_prints_help():
    h = Harness()
    assert h.run() == EXIT_USAGE
    assert "audit" in h.out.getvalue()


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "wp-db-doctor 0.1.0" in capsys.readouterr().out
