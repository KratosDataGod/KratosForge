"""Mutation-killing tests for engines/sessions.py (batch 81): a session that can't be closed, on an
engine that is no longer registered, gives the short local note; the logged failure detail is cut
to 300 characters."""

from __future__ import annotations

import logging

from fabric_desktop.engines import registry
from fabric_desktop.engines.sessions import SessionManager


class _Stuck:
    """A session whose close fails with a long message."""

    closed = False

    def close(self):
        raise RuntimeError("x" * 400 + "END")


class _Adapter:
    def __init__(self):
        self.desc = registry.adapter("local-duckdb").describe()

    def describe(self):
        return self.desc

    def open_session(self, config, authorization=None):
        return _Stuck()


def _manager():
    state = {"gone": False}
    adapter = _Adapter()

    def adapter_for(eid):
        if state["gone"]:
            raise KeyError(eid)
        return adapter
    return SessionManager(adapter_for), state


def test_an_unregistered_engine_gets_the_short_local_note():
    m, state = _manager()
    m.switch("nb", "removed-engine")
    state["gone"] = True  # the engine left the registry before its session was closed
    m.close("nb")
    assert m.notes("nb") == ["Couldn't close the removed-engine session."]


def test_the_logged_close_failure_is_cut_to_300_characters(caplog):
    m, _ = _manager()
    m.switch("nb", "removed-engine")
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.engines.sessions"):
        m.close("nb")
    msg = [r.getMessage() for r in caplog.records if "Closing a" in r.getMessage()][0]
    detail = msg.split("(RuntimeError): ", 1)[1]
    assert detail == "x" * 300
