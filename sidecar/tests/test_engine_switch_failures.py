"""Switching a notebook's engine when the new session can't open (KUDP hardening): the notebook keeps
the engine and session it had, so its next cell runs there or says why, never an internal error."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support
from fabric_desktop.engines.contract import CellRequest, Unsupported
from fabric_desktop.engines.databricks_auth import DatabricksAuthError
from fabric_desktop.engines.sessions import SessionManager

T = "unit_tests"


class Remote:
    """A remote engine whose session won't open (a sign-in that failed, a cluster that's gone)."""

    desc = EngineDescriptor("fake-remote", "Fake remote", "remote", "databricks", "databricks_dbu", adapter=True,
                            languages={"sql": Support("native", evidence=T)},
                            capabilities={"execute": Support("native", evidence=T)}, preview=True)

    def __init__(self, fail=True):
        self.fail = fail

    def describe(self):
        return self.desc

    def open_session(self, config, authorization=None):
        if self.fail:
            raise DatabricksAuthError("The Databricks CLI couldn't give a token.")
        return registry.adapter("local-duckdb").open_session(config)


@pytest.fixture
def manager():
    remote = Remote()
    m = SessionManager(lambda eid: remote if eid == "fake-remote" else registry.adapter(eid))
    m.remote = remote
    yield m
    m.close_all()


def sql(code):
    return CellRequest(code, "sql")


OK = auth.confirmed("fake-remote", "wh-1", "chris")


def test_a_session_that_cant_open_leaves_the_old_one_running(manager):
    s1 = manager.switch("nb", "local-duckdb")
    manager.execute("nb", sql("CREATE TABLE t AS SELECT 7 AS x"))
    with pytest.raises(DatabricksAuthError, match="couldn't give a token"):
        manager.switch("nb", "fake-remote", decision="close", target="wh-1", authorization=OK)
    assert manager.engine_of("nb") == "local-duckdb" and manager.session("nb") is s1 and not s1.closed
    assert manager.execute("nb", sql("SELECT x FROM t")).outputs[0]["rows"] == [[7]]


def test_a_local_switch_that_cant_open_keeps_the_old_one_too(monkeypatch):
    sqlite = registry.adapter("local-sqlite")

    def broken(config, authorization=None):
        raise OSError("disk full")
    monkeypatch.setattr(sqlite, "open_session", broken)
    m = SessionManager(lambda eid: sqlite if eid == "local-sqlite" else registry.adapter(eid))
    s1 = m.switch("nb", "local-duckdb")
    with pytest.raises(OSError):
        m.switch("nb", "local-sqlite", decision="close")
    assert m.engine_of("nb") == "local-duckdb" and m.session("nb") is s1 and not s1.closed
    assert m.execute("nb", sql("SELECT 1 AS x")).outputs[0]["rows"] == [[1]]
    m.close_all()


def test_switching_closes_the_old_session_once_the_new_one_is_open(manager):
    manager.remote.fail = False
    s1 = manager.switch("nb", "local-duckdb")
    s2 = manager.switch("nb", "fake-remote", decision="close", target="wh-1", authorization=OK)
    assert s1.closed and not s2.closed and manager.engine_of("nb") == "fake-remote"
    assert manager.open_sessions() == [{"notebook": "nb", "engine": "fake-remote", "active": True}]


def test_a_kept_session_stays_kept_when_the_new_one_cant_open(manager):
    s1 = manager.switch("nb", "local-duckdb")
    with pytest.raises(DatabricksAuthError):
        manager.switch("nb", "fake-remote", decision="keep", target="wh-1", authorization=OK)
    assert manager.session("nb") is s1 and not s1.closed


def test_a_first_session_that_cant_open_leaves_no_engine(manager):
    with pytest.raises(DatabricksAuthError):
        manager.switch("nb", "fake-remote", target="wh-1", authorization=OK)
    assert manager.engine_of("nb") is None and manager.open_sessions() == []
    with pytest.raises(Unsupported, match="Pick an engine"):
        manager.execute("nb", sql("SELECT 1"))


def test_a_cell_with_no_open_session_says_so_instead_of_an_internal_error(manager):
    manager.switch("nb", "local-duckdb")
    nb = manager._notebooks["nb"]
    nb.sessions.clear()     # however it happened, the engine has no session
    with pytest.raises(Unsupported, match="has no open session"):
        manager.execute("nb", sql("SELECT 1"))
