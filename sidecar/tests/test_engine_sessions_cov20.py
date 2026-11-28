"""Engine sessions: the path the other tests leave out (coverage pass 20). At app exit, a session whose
close failed earlier but which has since closed on its own is not closed a second time."""

from __future__ import annotations

from fabric_desktop.engines import registry
from fabric_desktop.engines.sessions import SessionManager


class _ClosesLater:
    """Its close fails once; then it ends on its own (an idle timeout), before the app exits."""

    def __init__(self):
        self.closed = False
        self.calls = 0

    def close(self):
        self.calls += 1
        raise RuntimeError("busy")


def test_close_all_skips_a_failed_session_that_has_since_closed_on_its_own(caplog):
    manager = SessionManager(registry.adapter)
    manager.switch("nb", "local-duckdb")
    real = manager._notebooks["nb"].sessions["local-duckdb"]
    manager._notebooks["nb"].sessions["local-duckdb"] = later = _ClosesLater()
    try:
        manager.close("nb")
        assert later.calls == 1 and manager.notes("nb") == ["Couldn't close the DuckDB (this PC) session."]
        later.closed = True
        with caplog.at_level("WARNING", logger="fabric_desktop.engines.sessions"):
            manager.close_all()
        assert later.calls == 1   # not tried again
        assert "failed again at exit" not in caplog.text
        assert manager._unclosed == []
    finally:
        real.close()
