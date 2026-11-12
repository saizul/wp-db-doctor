"""DBAPIDatabase against a fake DB-API cursor, and connect() with a fake PyMySQL."""

from __future__ import annotations

import sys
import types

import pytest

from wp_db_doctor.db import ConnectionSettings, DatabaseError, DBAPIDatabase, connect


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rowcount = -1
        self.closed = False
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        if self.conn.error and self.conn.error[0] in sql:
            raise self.conn.error[1]
        result = self.conn.results.get(sql.split()[0].upper(), ((), []))
        cols, rows = result
        self.description = [(c, None, None, None, None, None, None) for c in cols] or None
        self._rows = rows
        self.rowcount = self.conn.rowcount

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.results = {}
        self.cursors = []
        self.rowcount = 3
        self.commits = 0
        self.error = None
        self.closed = False

    def cursor(self):
        cur = FakeCursor(self)
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class DriverError(Exception):
    pass


def test_query_maps_tuples_to_lowercase_dicts():
    conn = FakeConnection()
    conn.results["SELECT"] = (("TABLE_NAME", "Engine"), [("wp_posts", "InnoDB")])
    db = DBAPIDatabase(conn)
    assert db.query("SELECT 1") == [{"table_name": "wp_posts", "engine": "InnoDB"}]
    assert all(c.closed for c in conn.cursors)


def test_query_accepts_dict_rows():
    conn = FakeConnection()
    conn.results["SELECT"] = (("X",), [{"X": 1}])
    assert DBAPIDatabase(conn).query("SELECT 1") == [{"x": 1}]


def test_params_passed_only_when_given():
    conn = FakeConnection()
    db = DBAPIDatabase(conn)
    db.query("SELECT a LIKE '\\_x%'")
    db.query("SELECT %s", ("v",))
    assert conn.statements == [("SELECT a LIKE '\\_x%'", None), ("SELECT %s", ("v",))]


def test_execute_returns_rowcount_and_commits():
    conn = FakeConnection()
    db = DBAPIDatabase(conn)
    assert db.execute("DELETE FROM t") == 3
    assert conn.commits == 1
    conn.rowcount = -1
    assert db.execute("DELETE FROM t") == 0


def test_read_only_session():
    conn = FakeConnection()
    DBAPIDatabase(conn, read_only=True)
    assert conn.statements[0][0] == "SET SESSION TRANSACTION READ ONLY"


def test_read_only_failure_is_ignored():
    conn = FakeConnection()
    conn.error = ("SET", DriverError(1064, "syntax"))
    db = DBAPIDatabase(conn, read_only=True)
    assert db is not None


def test_driver_errors_are_wrapped():
    conn = FakeConnection()
    conn.error = ("SELECT", DriverError(1146, "Table 'wordpress.wp_x' doesn't exist"))
    db = DBAPIDatabase(conn)
    with pytest.raises(DatabaseError, match="MySQL error 1146"):
        db.query("SELECT * FROM wp_x")
    conn.error = ("DELETE", DriverError("boom"))
    with pytest.raises(DatabaseError, match="boom"):
        db.execute("DELETE FROM wp_x")


def test_close():
    conn = FakeConnection()
    DBAPIDatabase(conn).close()
    assert conn.closed


@pytest.fixture
def fake_pymysql(monkeypatch):
    calls = {}
    module = types.ModuleType("pymysql")

    def fake_connect(**kwargs):
        calls.update(kwargs)
        if kwargs.get("user") == "denied":
            raise DriverError(1045, "Access denied for user 'denied'@'localhost'")
        return FakeConnection()

    module.connect = fake_connect
    monkeypatch.setitem(sys.modules, "pymysql", module)
    return calls


def test_connect_tcp(fake_pymysql):
    s = ConnectionSettings(host="db.example.com", port=3307, user="u", password="secret",
                           database="wp", ssl_ca="/etc/ca.pem")
    db = connect(s)
    assert isinstance(db, DBAPIDatabase)
    assert fake_pymysql["host"] == "db.example.com"
    assert fake_pymysql["port"] == 3307
    assert fake_pymysql["ssl"] == {"ca": "/etc/ca.pem"}
    assert fake_pymysql["autocommit"] is True
    assert "unix_socket" not in fake_pymysql


def test_connect_socket(fake_pymysql):
    connect(ConnectionSettings(socket="/run/mysqld/mysqld.sock", user="u", database="wp"))
    assert fake_pymysql["unix_socket"] == "/run/mysqld/mysqld.sock"
    assert "host" not in fake_pymysql
    assert fake_pymysql["password"] == ""


def test_connect_error_has_no_password(fake_pymysql):
    s = ConnectionSettings(user="denied", password="hunter2", database="wp")
    with pytest.raises(DatabaseError) as info:
        connect(s)
    assert "hunter2" not in str(info.value)
    assert "1045" in str(info.value)
    assert "denied@localhost" in str(info.value)


def test_describe():
    assert ConnectionSettings(user="u", host="h", port=1).describe() == "u@h:1"
    assert ConnectionSettings(user="u", socket="/s").describe() == "u@/s"
