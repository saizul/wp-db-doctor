from __future__ import annotations

from pathlib import Path

import pytest

from wp_db_doctor.config import ConfigError, config_from_dict, load_config
from wp_db_doctor.models import Status
from wp_db_doctor.utils import (
    InvalidPrefixError,
    format_bytes,
    parse_id_list,
    plural,
    quote_ident,
    sql_literal,
    to_int,
    validate_prefix,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "wp-db-doctor.toml"


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.thresholds.autoload_warning_kb == 800
    assert cfg.fail_on == "critical"


def test_config_validation():
    with pytest.raises(ConfigError, match="unknown section"):
        config_from_dict({"nope": {}})
    with pytest.raises(ConfigError, match="non-negative integer"):
        config_from_dict({"thresholds": {"autoload_top": -1}})
    with pytest.raises(ConfigError, match="non-negative integer"):
        config_from_dict({"thresholds": {"autoload_top": True}})
    with pytest.raises(ConfigError, match="list of check ids"):
        config_from_dict({"checks": {"skip": "autoload"}})
    with pytest.raises(ConfigError, match="fail_on"):
        config_from_dict({"checks": {"fail_on": "sometimes"}})
    with pytest.raises(ConfigError, match="unknown key"):
        config_from_dict({"checks": {"skipp": []}})


def test_config_invalid_toml(tmp_path):
    p = tmp_path / "x.toml"
    p.write_text("[thresholds\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(p)
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.toml")


def test_quote_ident():
    assert quote_ident("wp_posts") == "`wp_posts`"
    assert quote_ident("we`ird") == "`we``ird`"
    with pytest.raises(ValueError):
        quote_ident("a\x00b")


def test_sql_literal():
    assert sql_literal("it's") == "'it\\'s'"
    assert sql_literal("a\\b\nc") == "'a\\\\b\\nc'"
    assert sql_literal(None) == "NULL"
    assert sql_literal(5) == "5"
    assert sql_literal(True) == "1"


@pytest.mark.parametrize("prefix", ["wp_", "WP2_", "a"])
def test_valid_prefix(prefix):
    assert validate_prefix(prefix) == prefix


@pytest.mark.parametrize("prefix", ["", "wp-", "wp_;", "wp prefix", "wp`"])
def test_invalid_prefix(prefix):
    with pytest.raises(InvalidPrefixError):
        validate_prefix(prefix)


def test_format_helpers():
    assert format_bytes(512) == "512 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(5 * 1024 ** 3) == "5.0 GB"
    assert plural(1, "row") == "1 row"
    assert plural(2000, "row") == "2,000 rows"
    assert parse_id_list(" a, b,,c ") == ["a", "b", "c"]
    assert parse_id_list(None) == []


@pytest.mark.parametrize(("value", "expected"),
                         [(None, 0), ("12", 12), (b"7", 7), ("3.0", 3), ("x", 0), (True, 1)])
def test_to_int(value, expected):
    assert to_int(value) == expected


def test_status_parse_and_rank():
    assert Status.parse("WARNING") is Status.WARNING
    assert Status.CRITICAL.rank > Status.WARNING.rank > Status.INFO.rank > Status.OK.rank
    with pytest.raises(ValueError):
        Status.parse("bad")
