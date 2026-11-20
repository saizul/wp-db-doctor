"""Thresholds and optional TOML configuration."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.9/3.10 only
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


class ConfigError(Exception):
    pass


@dataclass
class Thresholds:
    # Autoloaded options (WordPress Site Health warns at 800 KB).
    autoload_warning_kb: int = 800
    autoload_critical_kb: int = 3072
    autoload_top: int = 15
    autoload_large_option_kb: int = 64

    expired_transients_warning: int = 500

    revisions_warning: int = 2000
    revisions_critical: int = 20000
    revisions_min_age_days: int = 30
    revisions_top: int = 10

    auto_draft_age_days: int = 7
    auto_drafts_warning: int = 100

    trash_min_age_days: int = 30
    trashed_warning: int = 200

    oembed_cache_warning: int = 2000

    comments_warning: int = 1000
    comments_critical: int = 50000

    orphans_warning: int = 1000
    orphans_top: int = 10

    action_scheduler_retention_days: int = 30
    action_scheduler_warning: int = 20000
    action_scheduler_critical: int = 500000
    action_scheduler_overdue_warning: int = 100

    wc_sessions_expired_warning: int = 1000

    large_table_mb: int = 256
    table_overhead_mb: int = 64
    tables_top: int = 10


@dataclass
class Config:
    thresholds: Thresholds = field(default_factory=Thresholds)
    skip: list[str] = field(default_factory=list)
    only: list[str] = field(default_factory=list)
    fail_on: str | None = None
    source: Path | None = None


_THRESHOLD_NAMES = {f.name for f in fields(Thresholds)}


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if tomllib is None:  # pragma: no cover
        raise ConfigError("reading TOML on Python < 3.11 needs the 'tomli' package")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {p}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: invalid TOML: {exc}") from exc
    return config_from_dict(data, source=p)


def config_from_dict(data: dict[str, Any], source: Path | None = None) -> Config:
    config = Config(source=source)
    unknown = set(data) - {"thresholds", "checks"}
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")
    thresholds = data.get("thresholds", {})
    if not isinstance(thresholds, dict):
        raise ConfigError("[thresholds] must be a table")
    for key, value in thresholds.items():
        if key not in _THRESHOLD_NAMES:
            raise ConfigError(f"unknown threshold {key!r}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"threshold {key!r} must be a non-negative integer")
        setattr(config.thresholds, key, value)
    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        raise ConfigError("[checks] must be a table")
    for key in ("skip", "only"):
        value = checks.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"checks.{key} must be a list of check ids")
        setattr(config, key, list(value))
    fail_on = checks.get("fail_on")
    if fail_on is not None:
        if fail_on not in ("info", "warning", "critical", "never"):
            raise ConfigError("checks.fail_on must be info, warning, critical or never")
        config.fail_on = fail_on
    extra = set(checks) - {"skip", "only", "fail_on"}
    if extra:
        raise ConfigError(f"unknown key(s) in [checks]: {', '.join(sorted(extra))}")
    return config
