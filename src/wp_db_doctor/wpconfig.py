"""Read database settings from ``wp-config.php`` without running PHP.

The file is tokenised (strings, comments, identifiers, punctuation) and only
two statement shapes are interpreted:

* ``define( 'NAME', <expr> );``
* ``$table_prefix = <expr>;``

``<expr>`` may be a string, number, ``true``/``false``/``null``, a
concatenation with ``.``, ``getenv('X')``, the ``getenv_docker('X', 'default')``
helper used by the official Docker image, ``$_ENV['X']``/``$_SERVER['X']`` and
the ``?:`` shorthand. Anything else is recorded as unresolved instead of
guessed, so command-line options can fill the gap.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DB_KEYS = ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_CHARSET", "DB_COLLATE")


class WPConfigError(Exception):
    pass


class _Unresolved:
    def __init__(self, source: str) -> None:
        self.source = source

    def __repr__(self) -> str:
        return f"<unresolved {self.source}>"


@dataclass
class Token:
    kind: str  # str, num, ident, var, punct
    value: str
    line: int
    interpolated: bool = False


@dataclass
class WPConfig:
    path: Path | None
    constants: dict[str, Any] = field(default_factory=dict)
    table_prefix: str | None = None
    unresolved: dict[str, str] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.constants.get(name, default)

    @property
    def db_name(self) -> str | None:
        return _as_text(self.constants.get("DB_NAME"))

    @property
    def db_user(self) -> str | None:
        return _as_text(self.constants.get("DB_USER"))

    @property
    def db_password(self) -> str | None:
        return _as_text(self.constants.get("DB_PASSWORD"))

    @property
    def db_host(self) -> str | None:
        return _as_text(self.constants.get("DB_HOST"))

    @property
    def db_charset(self) -> str | None:
        return _as_text(self.constants.get("DB_CHARSET"))

    @property
    def db_collate(self) -> str | None:
        return _as_text(self.constants.get("DB_COLLATE"))

    def masked(self) -> dict[str, Any]:
        """Settings safe to print: the password is replaced by a marker."""
        out: dict[str, Any] = {"path": str(self.path) if self.path else None}
        for key in DB_KEYS:
            value = self.constants.get(key)
            if key == "DB_PASSWORD" and value is not None:
                value = "********" if value != "" else ""
            out[key] = value
        out["table_prefix"] = self.table_prefix
        for key in ("WP_POST_REVISIONS", "EMPTY_TRASH_DAYS", "DISABLE_WP_CRON", "MULTISITE",
                    "WP_CACHE", "AUTOSAVE_INTERVAL"):
            if key in self.constants:
                out[key] = self.constants[key]
        if self.unresolved:
            out["unresolved"] = dict(self.unresolved)
        return out


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value)


# --------------------------------------------------------------------------
# Tokeniser
# --------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "v": "\v", "e": "\x1b", "f": "\f",
    "\\": "\\", "$": "$", '"': '"',
}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)
    line = 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if source.startswith("<?php", i):
            i += 5
            continue
        if source.startswith("<?=", i):
            i += 3
            continue
        if source.startswith("?>", i):
            i += 2
            continue
        if ch == "#" or source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end == -1 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            chunk = source[i:] if end == -1 else source[i:end + 2]
            line += chunk.count("\n")
            i = n if end == -1 else end + 2
            continue
        if ch == "'":
            value, i, lines = _read_single(source, i)
            tokens.append(Token("str", value, line))
            line += lines
            continue
        if ch == '"':
            value, i, lines, interp = _read_double(source, i)
            tokens.append(Token("str", value, line, interpolated=interp))
            line += lines
            continue
        if ch == "$" and i + 1 < n and (source[i + 1].isalpha() or source[i + 1] == "_"):
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            tokens.append(Token("var", source[i + 1:j], line))
            i = j
            continue
        if ch.isdigit():
            j = i
            while j < n and (source[j].isalnum() or source[j] in "._"):
                j += 1
            tokens.append(Token("num", source[i:j], line))
            i = j
            continue
        if ch.isalpha() or ch == "_" or ch == "\\":
            j = i
            while j < n and (source[j].isalnum() or source[j] in "_\\"):
                j += 1
            tokens.append(Token("ident", source[i:j].lstrip("\\"), line))
            i = j
            continue
        if source.startswith("?:", i):
            tokens.append(Token("punct", "?:", line))
            i += 2
            continue
        if source.startswith("??", i):
            tokens.append(Token("punct", "??", line))
            i += 2
            continue
        tokens.append(Token("punct", ch, line))
        i += 1
    return tokens


def _read_single(source: str, start: int) -> tuple[str, int, int]:
    out = []
    i = start + 1
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n and source[i + 1] in "\\'":
            out.append(source[i + 1])
            i += 2
            continue
        if ch == "'":
            text = "".join(out)
            return text, i + 1, source.count("\n", start, i)
        out.append(ch)
        i += 1
    raise WPConfigError("unterminated single-quoted string")


def _read_double(source: str, start: int) -> tuple[str, int, int, bool]:
    out = []
    i = start + 1
    n = len(source)
    interpolated = False
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            nxt = source[i + 1]
            if nxt in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "x" and i + 2 < n and source[i + 2] in "0123456789abcdefABCDEF":
                j = i + 2
                while j < min(i + 4, n) and source[j] in "0123456789abcdefABCDEF":
                    j += 1
                out.append(chr(int(source[i + 2:j], 16)))
                i = j
                continue
            if nxt in "01234567":
                j = i + 1
                while j < min(i + 4, n) and source[j] in "01234567":
                    j += 1
                out.append(chr(int(source[i + 1:j], 8) & 0xFF))
                i = j
                continue
            if nxt == "u" and i + 2 < n and source[i + 2] == "{":
                end = source.find("}", i + 3)
                if end != -1:
                    try:
                        out.append(chr(int(source[i + 3:end], 16)))
                        i = end + 1
                        continue
                    except ValueError:
                        pass
            out.append(ch)
            i += 1
            continue
        if ch == "$" and i + 1 < n and (source[i + 1].isalpha() or source[i + 1] in "_{"):
            interpolated = True
        if ch == "{" and i + 1 < n and source[i + 1] == "$":
            interpolated = True
        if ch == '"':
            return "".join(out), i + 1, source.count("\n", start, i), interpolated
        out.append(ch)
        i += 1
    raise WPConfigError("unterminated double-quoted string")


# --------------------------------------------------------------------------
# Expression evaluation
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[Token], env: Mapping[str, str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.env = env

    def peek(self, offset: int = 0) -> Token | None:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else None

    def is_punct(self, value: str, offset: int = 0) -> bool:
        tok = self.peek(offset)
        return tok is not None and tok.kind == "punct" and tok.value == value

    def skip_to(self, *values: str) -> None:
        """Advance to the next top-level punctuation in ``values``."""
        depth = 0
        while self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            if tok.kind == "punct":
                if tok.value in ("(", "[", "{"):
                    depth += 1
                elif tok.value in (")", "]", "}"):
                    if depth == 0:
                        if tok.value in values:
                            return
                    else:
                        depth -= 1
                elif depth == 0 and tok.value in values:
                    return
            self.pos += 1

    def source_between(self, start: int, end: int) -> str:
        return " ".join(t.value if t.kind != "str" else repr(t.value)
                        for t in self.tokens[start:end])

    def expression(self, stop: tuple[str, ...]) -> Any:
        start = self.pos
        try:
            value = self._fallback()
        except _ParseFail:
            value = None
            self.pos = start
            self.skip_to(*stop)
            return _Unresolved(self.source_between(start, self.pos))
        tok = self.peek()
        if tok is None or not (tok.kind == "punct" and tok.value in stop):
            self.skip_to(*stop)
            return _Unresolved(self.source_between(start, self.pos))
        return value

    def _fallback(self) -> Any:
        left = self._concat()
        while self.is_punct("?:") or self.is_punct("??"):
            op = self.peek().value  # type: ignore[union-attr]
            self.pos += 1
            right = self._concat()
            if isinstance(left, _Unresolved):
                left = right if op == "?:" else left
            elif (op == "?:" and not left) or (op == "??" and left is None):
                left = right
        return left

    def _concat(self) -> Any:
        parts = [self._primary()]
        while self.is_punct("."):
            self.pos += 1
            parts.append(self._primary())
        if len(parts) == 1:
            return parts[0]
        if any(isinstance(p, _Unresolved) for p in parts):
            return _Unresolved("concatenation")
        return "".join(_php_string(p) for p in parts)

    def _primary(self) -> Any:
        tok = self.peek()
        if tok is None:
            raise _ParseFail
        if tok.kind == "punct" and tok.value == "(":
            self.pos += 1
            value = self._fallback()
            if not self.is_punct(")"):
                raise _ParseFail
            self.pos += 1
            return value
        if tok.kind == "str":
            self.pos += 1
            if tok.interpolated:
                return _Unresolved("interpolated string")
            return tok.value
        if tok.kind == "num":
            self.pos += 1
            return _php_number(tok.value)
        if tok.kind == "punct" and tok.value == "-" and self.peek(1) and self.peek(1).kind == "num":
            self.pos += 2
            num = _php_number(self.tokens[self.pos - 1].value)
            return -num if isinstance(num, (int, float)) else _Unresolved("negative")
        if tok.kind == "ident":
            low = tok.value.lower()
            if low in ("true", "false", "null"):
                self.pos += 1
                return {"true": True, "false": False, "null": None}[low]
            if low in ("getenv", "getenv_docker") and self.is_punct("(", 1):
                self.pos += 2
                args = self._call_args()
                return self._env_lookup(low, args)
            raise _ParseFail
        if tok.kind == "var" and tok.value in ("_ENV", "_SERVER") and self.is_punct("[", 1):
            self.pos += 2
            key = self._fallback()
            if not self.is_punct("]"):
                raise _ParseFail
            self.pos += 1
            if isinstance(key, str) and key in self.env:
                return self.env[key]
            return _Unresolved(f"${tok.value}[{key!r}]")
        raise _ParseFail

    def _call_args(self) -> list[Any]:
        args: list[Any] = []
        if self.is_punct(")"):
            self.pos += 1
            return args
        while True:
            args.append(self._fallback())
            if self.is_punct(","):
                self.pos += 1
                continue
            if self.is_punct(")"):
                self.pos += 1
                return args
            raise _ParseFail

    def _env_lookup(self, func: str, args: list[Any]) -> Any:
        if not args or not isinstance(args[0], str):
            return _Unresolved(f"{func}()")
        name = args[0]
        if name in self.env:
            return self.env[name]
        if func == "getenv_docker":
            # The Docker image helper also reads NAME_FILE (a Docker secret).
            file_var = self.env.get(name + "_FILE")
            if file_var:
                try:
                    return Path(file_var).read_text(encoding="utf-8").rstrip("\r\n")
                except OSError:
                    return _Unresolved(f"{name}_FILE")
            if len(args) > 1:
                return args[1]
            return _Unresolved(f"getenv_docker({name!r})")
        return False  # PHP getenv() returns false for unset variables


class _ParseFail(Exception):
    pass


def _php_number(text: str) -> Any:
    clean = text.replace("_", "")
    try:
        if clean.lower().startswith("0x"):
            return int(clean, 16)
        if "." in clean or "e" in clean.lower():
            return float(clean)
        return int(clean)
    except ValueError:
        return _Unresolved(text)


def _php_string(value: Any) -> str:
    if value is True:
        return "1"
    if value is False or value is None:
        return ""
    return str(value)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse_wp_config(source: str, path: Path | None = None,
                    env: Mapping[str, str] | None = None) -> WPConfig:
    """Parse the text of a ``wp-config.php`` file."""
    tokens = tokenize(source)
    parser = _Parser(tokens, os.environ if env is None else env)
    config = WPConfig(path=path)
    while parser.pos < len(tokens):
        tok = tokens[parser.pos]
        if tok.kind == "ident" and tok.value.lower() == "define" and parser.is_punct("(", 1):
            parser.pos += 2
            name = parser.expression((",", ")"))
            if not parser.is_punct(","):
                parser.skip_to(";")
                continue
            parser.pos += 1
            value = parser.expression((",", ")"))
            parser.skip_to(";")
            if isinstance(name, str):
                # PHP keeps the first definition; later define() calls fail.
                if name in config.constants or name in config.unresolved:
                    continue
                if isinstance(value, _Unresolved):
                    config.unresolved[name] = value.source
                else:
                    config.constants[name] = value
            continue
        if tok.kind == "var" and tok.value == "table_prefix" and parser.is_punct("=", 1):
            parser.pos += 2
            value = parser.expression((";",))
            if isinstance(value, str):
                config.table_prefix = value
                config.unresolved.pop("$table_prefix", None)
            elif isinstance(value, _Unresolved):
                config.unresolved["$table_prefix"] = value.source
            continue
        parser.pos += 1
    return config


def find_wp_config(path: str | Path) -> Path:
    """Locate ``wp-config.php`` from a file path or a WordPress directory.

    Like WordPress, a directory is searched first and then its parent, because
    ``wp-config.php`` may live one level above the web root.
    """
    p = Path(path).expanduser()
    if p.is_file():
        return p
    if p.is_dir():
        for candidate in (p / "wp-config.php", p.parent / "wp-config.php"):
            if candidate.is_file():
                return candidate
        raise WPConfigError(f"no wp-config.php found in {p} or its parent directory")
    raise WPConfigError(f"{p} does not exist")


def load_wp_config(path: str | Path, env: Mapping[str, str] | None = None) -> WPConfig:
    file_path = find_wp_config(path)
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise WPConfigError(f"cannot read {file_path}: {exc.strerror or exc}") from exc
    return parse_wp_config(source, path=file_path, env=env)


@dataclass
class HostSpec:
    host: str | None = None
    port: int | None = None
    socket: str | None = None


def parse_db_host(value: str | None) -> HostSpec:
    """Split a ``DB_HOST`` value the way WordPress (wpdb) does.

    Accepts ``host``, ``host:port``, ``host:/path/to/socket``, ``/path/to.sock``,
    ``[ipv6]:port``, bare IPv6 addresses and the ``p:`` persistent-connection
    prefix (which is ignored).
    """
    if not value:
        return HostSpec(host="localhost")
    text = value.strip()
    if text.startswith("p:"):
        text = text[2:]
    if text.startswith("/"):
        return HostSpec(socket=text)
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            host = text[1:end]
            rest = text[end + 1:]
            port = _port(rest[1:]) if rest.startswith(":") else None
            return HostSpec(host=host, port=port)
    if text.count(":") > 1:
        return HostSpec(host=text)  # bare IPv6 address
    if ":" in text:
        host, _, rest = text.partition(":")
        host = host or "localhost"
        if rest.startswith("/"):
            return HostSpec(host=host, socket=rest)
        port = _port(rest)
        if port is None and rest:
            # wpdb treats a non-numeric suffix as a socket name.
            return HostSpec(host=host, socket=rest)
        return HostSpec(host=host, port=port)
    return HostSpec(host=text)


def _port(text: str) -> int | None:
    if text.isdigit():
        port = int(text)
        if 0 < port < 65536:
            return port
    return None
