from __future__ import annotations

from pathlib import Path

import pytest

from wp_db_doctor.wpconfig import (
    HostSpec,
    WPConfigError,
    find_wp_config,
    load_wp_config,
    parse_db_host,
    parse_wp_config,
    tokenize,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_standard_wp_config():
    cfg = load_wp_config(FIXTURES / "wp-config-standard.php", env={})
    assert cfg.db_name == "shop_wp"
    assert cfg.db_user == "shop_user"
    assert cfg.db_password == "p@ss'word;with\"stuff"
    assert cfg.db_host == "localhost:3307"
    assert cfg.db_charset == "utf8mb4"
    assert cfg.db_collate == ""
    assert cfg.table_prefix == "shop_"
    assert cfg.get("WP_POST_REVISIONS") == 5
    assert cfg.get("EMPTY_TRASH_DAYS") == 7
    assert cfg.get("DISABLE_WP_CRON") is True
    assert cfg.get("WP_DEBUG") is False
    # ABSPATH depends on __DIR__ and cannot be resolved statically.
    assert "ABSPATH" in cfg.unresolved


def test_commented_defines_are_ignored():
    cfg = load_wp_config(FIXTURES / "wp-config-standard.php", env={})
    assert cfg.db_user != "hash_comment_user"
    assert cfg.db_host != "slash-comment-host"
    assert cfg.db_name != "commented_out_in_docblock"


def test_docker_config_uses_defaults_without_env():
    cfg = load_wp_config(FIXTURES / "wp-config-docker.php", env={})
    assert cfg.db_name == "wordpress"
    assert cfg.db_host == "mysql"
    assert cfg.db_password == "example password"
    assert cfg.table_prefix == "wp_"


def test_docker_config_reads_environment():
    env = {"WORDPRESS_DB_NAME": "prod", "WORDPRESS_DB_HOST": "db:3306",
           "WORDPRESS_TABLE_PREFIX": "site_", "WORDPRESS_DB_USER": "u"}
    cfg = load_wp_config(FIXTURES / "wp-config-docker.php", env=env)
    assert cfg.db_name == "prod"
    assert cfg.db_host == "db:3306"
    assert cfg.db_user == "u"
    assert cfg.table_prefix == "site_"


def test_docker_secret_file(tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("from-secret\n", encoding="utf-8")
    cfg = load_wp_config(FIXTURES / "wp-config-docker.php",
                         env={"WORDPRESS_DB_PASSWORD_FILE": str(secret)})
    assert cfg.db_password == "from-secret"


def test_tricky_config():
    cfg = load_wp_config(FIXTURES / "wp-config-tricky.php", env={})
    assert cfg.db_name == "tricky_db"  # concatenation, and the first define wins
    assert cfg.db_user == "userA"  # \x41 escape in a double-quoted string
    assert cfg.db_password == "pa$$word"
    assert cfg.db_host == "p:[::1]:3308"
    assert cfg.table_prefix == "final_"  # last assignment wins
    assert cfg.get("MULTISITE") is True
    assert cfg.get("AUTOSAVE_INTERVAL") == 300
    assert "WP_HOME" in cfg.unresolved
    assert "WP_SITEURL" in cfg.unresolved


def test_server_superglobal_from_env():
    cfg = parse_wp_config("<?php define('WP_HOME', 'https://' . $_SERVER['HTTP_HOST']);",
                          env={"HTTP_HOST": "example.com"})
    assert cfg.get("WP_HOME") == "https://example.com"


def test_getenv_and_fallback_operator():
    src = "<?php define('DB_HOST', getenv('MY_HOST') ?: 'fallback'); " \
          "define('DB_NAME', getenv('MY_DB') ?: 'x');"
    cfg = parse_wp_config(src, env={"MY_DB": "real"})
    assert cfg.db_host == "fallback"
    assert cfg.db_name == "real"


def test_null_coalescing_and_parentheses():
    cfg = parse_wp_config("<?php define('A', ($x ?? 'no')); define('B', ('a' . 'b'));", env={})
    assert "A" in cfg.unresolved
    assert cfg.get("B") == "ab"


def test_numbers_and_negative():
    cfg = parse_wp_config("<?php define('WP_POST_REVISIONS', -1); define('X', 0x1F); "
                          "define('Y', 1.5); define('Z', 1_000);", env={})
    assert cfg.get("WP_POST_REVISIONS") == -1
    assert cfg.get("X") == 31
    assert cfg.get("Y") == 1.5
    assert cfg.get("Z") == 1000


def test_single_quote_escapes_are_minimal():
    cfg = parse_wp_config(r"<?php define('DB_PASSWORD', 'a\nb\\c\'d');", env={})
    assert cfg.db_password == "a\\nb\\c'd"


def test_unterminated_string_raises():
    with pytest.raises(WPConfigError):
        parse_wp_config("<?php define('DB_NAME', 'oops);")


def test_masked_never_shows_password():
    cfg = load_wp_config(FIXTURES / "wp-config-standard.php", env={})
    masked = cfg.masked()
    assert masked["DB_PASSWORD"] == "********"
    assert "p@ss" not in repr(masked)
    assert masked["WP_POST_REVISIONS"] == 5


def test_tokenize_tracks_lines():
    toks = tokenize("<?php\n/* a\nb */\ndefine('X', 1);")
    define = next(t for t in toks if t.value == "define")
    assert define.line == 4


def test_find_wp_config_in_dir_and_parent(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    (root / "wp-config.php").write_text("<?php", encoding="utf-8")
    assert find_wp_config(public) == root / "wp-config.php"
    (public / "wp-config.php").write_text("<?php", encoding="utf-8")
    assert find_wp_config(public) == public / "wp-config.php"


def test_find_wp_config_errors(tmp_path):
    with pytest.raises(WPConfigError):
        find_wp_config(tmp_path)
    with pytest.raises(WPConfigError):
        find_wp_config(tmp_path / "missing.php")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, HostSpec(host="localhost")),
        ("localhost", HostSpec(host="localhost")),
        ("db.example.com:3307", HostSpec(host="db.example.com", port=3307)),
        ("localhost:/var/run/mysqld/mysqld.sock",
         HostSpec(host="localhost", socket="/var/run/mysqld/mysqld.sock")),
        ("/tmp/mysql.sock", HostSpec(socket="/tmp/mysql.sock")),
        ("[::1]:3308", HostSpec(host="::1", port=3308)),
        ("[2001:db8::5]", HostSpec(host="2001:db8::5")),
        ("2001:db8::5", HostSpec(host="2001:db8::5")),
        ("p:127.0.0.1:3306", HostSpec(host="127.0.0.1", port=3306)),
        (":3306", HostSpec(host="localhost", port=3306)),
        ("localhost:mysql.sock", HostSpec(host="localhost", socket="mysql.sock")),
    ],
)
def test_parse_db_host(value, expected):
    assert parse_db_host(value) == expected
