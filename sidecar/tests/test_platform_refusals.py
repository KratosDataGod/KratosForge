"""A change the platform refuses leaves a "refused <operation>" row on its own audit (catalog.audit),
whoever called: the app, or a script calling UDF_Platform directly with no rights. The row has who and
which operation, never the arguments or the refusal's words; it goes in after the change is rolled
back; a person and operation get at most a row a minute, the platform at most REFUSAL_CAP an hour; and
the refusal is raised as it was, even when the row can't be written."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from contextlib import closing

import pytest

from fabric_desktop.central import core, udf
from fabric_desktop.central.client import LocalPlatform

ADMIN, MALLORY = "admin@example.com", "mallory@example.com"


def _rows(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM "catalog__audit" ORDER BY id')]


def _refused(path):
    return [r for r in _rows(path) if r["action"].startswith("refused ")]


@pytest.fixture
def platform(tmp_path):
    who = {"user": ADMIN}
    p = LocalPlatform(tmp_path / "platform.sqlite", user=lambda: who["user"])
    p.call("save_domain", domain={"id": "sales", "name": "Sales", "owners": ADMIN})
    who["user"] = MALLORY
    p.who = who
    return p


@pytest.fixture
def function_app(platform, monkeypatch):
    """UDF_Platform's own source, run as Fabric runs it, over the same platform file: a direct caller."""
    fake = types.ModuleType("fabric.functions")

    class Functions:
        def connection(self, **_):
            return lambda f: f
        context = connection

        def function(self):
            return lambda f: f

    class Thrown(Exception):
        def __init__(self, message, props=None):
            super().__init__(message)
            self.props = props

    fake.UserDataFunctions, fake.FabricSqlConnection, fake.UserDataFunctionContext = Functions, object, object
    fake.UserThrownError = Thrown
    monkeypatch.setitem(sys.modules, "fabric", types.SimpleNamespace(functions=fake))
    monkeypatch.setitem(sys.modules, "fabric.functions", fake)
    app: dict = {"__name__": "function_app"}
    exec(compile(udf.function_app(), "function_app.py", "exec"), app)
    real = app["Db"]
    app["Db"] = lambda conn, _dialect: real(conn, "sqlite")  # SQLite stands in for SQL_Platform
    app["sql"] = types.SimpleNamespace(connect=lambda: sqlite3.connect(platform.path))
    app["Thrown"] = Thrown
    return app


def _ctx(user):
    return types.SimpleNamespace(executing_user={"PreferredUsername": user, "Oid": "0000"})


def test_a_direct_caller_with_no_rights_leaves_a_refused_row(platform, function_app):
    with pytest.raises(function_app["Thrown"], match="Only platform admins can close or reopen key issuing"):
        function_app["close_keys"](function_app["sql"], _ctx(MALLORY), reason="secret-reason-text")
    (row,) = _refused(platform.path)
    assert (row["by_user"], row["action"], row["target"]) == (MALLORY, "refused close_keys", "")
    assert json.loads(row["detail"]) == {"count": 1}
    assert "secret-reason-text" not in json.dumps(row)
    assert not platform.call("keys_state")["closed"]  # the change itself didn't land


def test_a_refusal_sent_through_call_once_names_the_change_not_its_arguments(platform, function_app):
    with pytest.raises(function_app["Thrown"]):
        function_app["call_once"](function_app["sql"], _ctx(MALLORY), callId="ab" * 16, operation="delete_domain",
                                  args={"did": "sales"})
    (row,) = _refused(platform.path)
    assert (row["by_user"], row["action"]) == (MALLORY, "refused delete_domain")
    assert "sales" not in json.dumps(row)
    assert [d["id"] for d in platform.call("list_domains")] == ["sales"]


def test_the_caller_named_operation_is_never_written_as_it_was_sent(platform, function_app):
    with pytest.raises(function_app["Thrown"]):
        function_app["call_once"](function_app["sql"], _ctx(MALLORY), callId="cd" * 16, operation="drop everything",
                                  args={})
    assert [r["action"] for r in _refused(platform.path)] == ["refused call_once"]


def test_who_is_the_platforms_identity_not_an_argument(platform, function_app):
    with pytest.raises(function_app["Thrown"]):
        function_app["call_once"](function_app["sql"], _ctx(MALLORY), callId="ef" * 16, operation="delete_domain",
                                  args={"did": "sales", "by": ADMIN})
    assert [r["by_user"] for r in _refused(platform.path)] == [MALLORY]


def test_the_app_on_this_pc_records_refusals_too(platform):
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("close_keys", reason="x")
    assert [(r["by_user"], r["action"]) for r in _refused(platform.path)] == [(MALLORY, "refused close_keys")]


def test_a_refused_read_or_a_check_on_a_read_leaves_no_row(platform):
    with pytest.raises(core.PlatformError):
        platform.call("lookup_key", name="Not A Name!", value="1")
    assert _refused(platform.path) == []


def test_one_row_a_minute_per_person_and_operation_with_how_many_it_stands_for(platform):
    for _ in range(5):
        with pytest.raises(core.PlatformError):
            platform.call("close_keys", reason="x")
    with pytest.raises(core.PlatformError):
        platform.call("clear_keys")  # another operation gets its own row
    assert [r["action"] for r in _refused(platform.path)] == ["refused close_keys", "refused clear_keys"]
    with closing(sqlite3.connect(platform.path)) as conn, conn:  # a minute on
        conn.execute("""UPDATE "catalog__audit" SET at = '2000-01-01T00:00:00+00:00' WHERE action LIKE 'refused %'""")
    with pytest.raises(core.PlatformError):
        platform.call("close_keys", reason="x")
    last = _refused(platform.path)[-1]
    assert last["action"] == "refused close_keys" and json.loads(last["detail"]) == {"count": 5}


def test_the_platform_writes_at_most_its_cap_of_refusal_rows_an_hour(platform, monkeypatch):
    monkeypatch.setattr(core, "REFUSAL_CAP", 2)
    for n in range(4):
        platform.who["user"] = f"user{n}@example.com"
        with pytest.raises(core.PlatformError):
            platform.call("close_keys", reason="x")
    assert len(_refused(platform.path)) == 2


def test_the_refusal_is_raised_unchanged_when_its_row_cant_be_written(platform, function_app, monkeypatch):
    with closing(sqlite3.connect(platform.path)) as conn, conn:
        conn.execute('ALTER TABLE "catalog__audit" RENAME TO "catalog__audit_gone"')
    with pytest.raises(core.PlatformError, match="^Only platform admins can close or reopen key issuing"):
        platform.call("close_keys", reason="x")
    with pytest.raises(function_app["Thrown"], match="^Only platform admins can close or reopen key issuing") as raised:
        function_app["close_keys"](function_app["sql"], _ctx(MALLORY), reason="x")
    assert raised.value.props == {"operation": "close_keys"}


def test_an_allowed_change_leaves_no_refused_row(platform):
    platform.who["user"] = ADMIN
    platform.call("close_keys", reason="maintenance")
    assert _refused(platform.path) == []
