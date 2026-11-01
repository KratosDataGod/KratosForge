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


# ---------------------------------------------------------------- a close that fails


class _Stuck:
    """A session whose close fails (a worker that won't stop, a remote that answers with an error)."""

    closed = False

    def __init__(self, message="worker wouldn't stop"):
        self.message = message
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        raise RuntimeError(self.message)


def test_a_switch_that_happened_isnt_reported_as_failed_when_the_old_close_fails(manager, caplog):
    manager.remote.fail = False
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = stuck = _Stuck()
    with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
        s2 = manager.switch("nb", "fake-remote", decision="close", target="wh-1", authorization=OK)
    assert stuck.close_calls == 1 and manager.session("nb") is s2 and manager.engine_of("nb") == "fake-remote"
    assert "Closing a local-duckdb session failed (RuntimeError): worker wouldn't stop" in caplog.text


def test_closing_a_notebook_closes_every_session_even_when_one_fails(manager, caplog):
    s1 = manager.switch("nb", "local-duckdb")
    manager.switch("nb", "local-sqlite", decision="keep")
    nb = manager._notebooks["nb"]
    nb.sessions = {"local-stuck": _Stuck(), **nb.sessions}
    other = manager.switch("nb2", "local-duckdb")
    with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
        manager.close_all()
    assert s1.closed and other.closed and manager.open_sessions() == []
    assert "Closing a local-stuck session failed" in caplog.text


def test_a_close_failure_is_logged_without_a_secret_it_carried(manager, caplog):
    token = "dapi" + "0123456789abcdef" * 2
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = _Stuck(f"401 for Bearer {token}")
    with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
        manager.close("nb")
    assert "Closing a local-duckdb session failed" in caplog.text and token not in caplog.text


def test_a_close_failure_is_logged_on_one_line(manager, caplog):
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = _Stuck("first line\nINFO forged: all good\r\n  last")
    with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
        manager.close("nb")
    (record,) = [r for r in caplog.records if "Closing a" in r.getMessage()]
    assert record.getMessage().endswith("(RuntimeError): first line INFO forged: all good last")
    assert "\n" not in record.getMessage() and "\r" not in record.getMessage()


def test_a_remote_session_that_couldnt_close_is_noted_once_for_the_person(manager):
    manager.remote.fail = False
    manager.switch("nb", "fake-remote", target="wh-1", authorization=OK)
    manager._notebooks["nb"].sessions["fake-remote"] = _Stuck()
    manager.switch("nb", "local-duckdb", decision="close")
    assert manager.engine_of("nb") == "local-duckdb"
    assert manager.notes("nb") == ["Couldn't close the Fake remote session. It ends on its own after its idle "
                                   "timeout, and may use compute (DBUs) until then."]
    assert manager.notes("nb") == []


def test_a_local_session_that_couldnt_close_is_noted_without_the_capacity_line(manager):
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = _Stuck()
    manager.close("nb")
    assert manager.notes("nb") == ["Couldn't close the DuckDB (this PC) session."]


def test_a_clean_switch_or_close_has_no_notes(manager):
    manager.switch("nb", "local-duckdb")
    manager.switch("nb", "local-sqlite", decision="close")
    manager.close("nb")
    assert manager.notes("nb") == []


@pytest.mark.parametrize("engine, says", [
    ("fabric-spark", "may use capacity until then. To stop it now, cancel it in Fabric's Monitor hub."),
    ("databricks-connect", "may use compute (DBUs) until then. To stop it now, end it on the cluster's page in "
                           "Databricks."),
    ("databricks-sql", "may use compute (DBUs) until then. To stop it now, stop it on the SQL warehouse's page "
                       "in Databricks."),
    ("snowflake-sql", "may use compute or capacity until then."),
])
def test_a_remote_note_names_what_it_may_use_and_where_to_stop_it(engine, says):
    from types import SimpleNamespace
    m = SessionManager(lambda eid: SimpleNamespace(describe=lambda: registry.get(eid)))
    note = m._note(engine)
    assert note.startswith(f"Couldn't close the {registry.get(engine).label} session. It ends on its own")
    assert note.endswith(says)


def test_close_all_tries_once_more_the_sessions_whose_close_failed(manager, caplog):
    class Flaky:
        closed = False

        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("busy")
            self.closed = True
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = flaky = Flaky()
    manager.close("nb")
    assert flaky.calls == 1 and manager.notes("nb")
    manager.close_all()
    assert flaky.calls == 2 and flaky.closed
    manager.close_all()
    assert flaky.calls == 2       # tried once more, not on every close_all


def test_a_session_that_fails_again_at_exit_is_logged_and_let_go(manager, caplog):
    manager.switch("nb", "local-duckdb")
    manager._notebooks["nb"].sessions["local-duckdb"] = stuck = _Stuck()
    manager.close("nb")
    with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
        manager.close_all()
    assert stuck.close_calls == 2 and "failed again at exit (RuntimeError)" in caplog.text
    assert manager._unclosed == []
