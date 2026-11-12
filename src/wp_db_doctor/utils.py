"""Small helpers: SQL quoting, size formatting and value coercion."""

from __future__ import annotations

import re
from typing import Any

_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


class InvalidPrefixError(ValueError):
    pass


def validate_prefix(prefix: str) -> str:
    """Return ``prefix`` if WordPress would accept it, else raise.

    WordPress itself refuses prefixes with anything other than letters,
    digits and underscores, so that is also all we ever splice into SQL.
    """
    if not prefix or not _PREFIX_RE.match(prefix):
        raise InvalidPrefixError(
            f"invalid table prefix {prefix!r}: only letters, digits and underscores are allowed"
        )
    return prefix


def quote_ident(name: str) -> str:
    """Quote a MySQL identifier with backticks."""
    if "\x00" in name:
        raise ValueError("identifier contains a NUL byte")
    return "`" + name.replace("`", "``") + "`"


def sql_literal(value: Any) -> str:
    """Render a Python value as a MySQL literal for a fix plan.

    Only used for values written into reviewed SQL files, never for queries
    sent with parameters (those use the driver's escaping).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    replacements = {
        "\\": "\\\\",
        "'": "\\'",
        "\x00": "\\0",
        "\n": "\\n",
        "\r": "\\r",
        "\x1a": "\\Z",
    }
    return "'" + "".join(replacements.get(ch, ch) for ch in text) + "'"


def to_int(value: Any) -> int:
    """Coerce driver output (int, Decimal, str, bytes, None) to ``int``."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def format_bytes(num: int | float) -> str:
    num = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"  # pragma: no cover - loop always returns


def format_int(num: int) -> str:
    return f"{num:,}"


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    if count == 1:
        return f"{format_int(count)} {word}"
    return f"{format_int(count)} {plural_word or word + 's'}"


def parse_id_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
