"""Database access: a tiny interface over a DB-API connection.

Checks only need two operations, ``query`` (rows as dictionaries) and
``execute`` (affected row count), so tests can replace the database with a
fake and the real implementation can wrap any DB-API 2.0 connection.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Protocol


class DatabaseError(Exception):
    """A query or connection failed. Messages never contain the password."""


class Database(Protocol):
    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: Any = None) -> int: ...

    def close(self) -> None: ...


@dataclass
class ConnectionSettings:
    host: str | None = "localhost"
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    socket: str | None = None
    charset: str = "utf8mb4"
    ssl_ca: str | None = None
    connect_timeout: int = 10

    def describe(self) -> str:
        """Human-readable target, safe to print."""
        if self.socket:
            return f"{self.user or '?'}@{self.socket}"
        port = f":{self.port}" if self.port else ""
        return f"{self.user or '?'}@{self.host or 'localhost'}{port}"


class DBAPIDatabase:
    """Adapter for a DB-API 2.0 connection (PyMySQL in practice)."""

    def __init__(self, connection: Any, read_only: bool = False) -> None:
        self._conn = connection
        if read_only:
            self._set_read_only()

    def _set_read_only(self) -> None:
        # Belt and braces for audits: the session refuses writes. Older
        # servers without the syntax just keep going.
        with contextlib.suppress(DatabaseError):
            self.execute("SET SESSION TRANSACTION READ ONLY")

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        try:
            try:
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, params)
                fetched = cursor.fetchall()
            except Exception as exc:  # driver specific error classes
                raise DatabaseError(_describe_error(exc)) from exc
            description = cursor.description or ()
            columns = [str(col[0]).lower() for col in description]
            rows: list[dict[str, Any]] = []
            for row in fetched or ():
                if isinstance(row, dict):
                    rows.append({str(k).lower(): v for k, v in row.items()})
                else:
                    rows.append(dict(zip(columns, row)))
            return rows
        finally:
            _close_quietly(cursor)

    def execute(self, sql: str, params: Any = None) -> int:
        cursor = self._conn.cursor()
        try:
            try:
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, params)
                count = cursor.rowcount
                commit = getattr(self._conn, "commit", None)
                if commit is not None:
                    commit()
            except Exception as exc:
                raise DatabaseError(_describe_error(exc)) from exc
            return int(count) if count is not None and count >= 0 else 0
        finally:
            _close_quietly(cursor)

    def close(self) -> None:
        _close_quietly(self._conn)


def _close_quietly(obj: Any) -> None:
    close = getattr(obj, "close", None)
    if close is None:
        return
    with contextlib.suppress(Exception):
        close()


def _describe_error(exc: Exception) -> str:
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        return f"MySQL error {args[0]}: {args[1]}"
    return str(exc) or exc.__class__.__name__


def connect(settings: ConnectionSettings, read_only: bool = True) -> DBAPIDatabase:
    """Open a PyMySQL connection described by ``settings``."""
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DatabaseError("PyMySQL is not installed: pip install PyMySQL") from exc

    kwargs: dict[str, Any] = {
        "user": settings.user,
        "password": settings.password or "",
        "database": settings.database,
        "charset": settings.charset or "utf8mb4",
        "connect_timeout": settings.connect_timeout,
        "autocommit": True,
    }
    if settings.socket:
        kwargs["unix_socket"] = settings.socket
    else:
        kwargs["host"] = settings.host or "localhost"
        kwargs["port"] = settings.port or 3306
    if settings.ssl_ca:
        kwargs["ssl"] = {"ca": settings.ssl_ca}
    try:
        conn = pymysql.connect(**kwargs)
    except Exception as exc:
        raise DatabaseError(
            f"cannot connect to {settings.describe()}: {_describe_error(exc)}"
        ) from exc
    return DBAPIDatabase(conn, read_only=read_only)
