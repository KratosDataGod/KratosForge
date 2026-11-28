"""The Python engine's session (engines/local_python.py): what mutation testing (batch 85,
docs/quality/mutation-testing.md) showed no test checked. A fake kernel stands in for the process;
nothing starts one."""

import pytest

from fabric_desktop.engines import local_python as lp
from fabric_desktop.engines.contract import CellRequest, SessionConfig, Unsupported


class Kernel:
    """Finishes on the first poll; records the version each poll asked for."""

    def __init__(self, first):
        self.first = first
        self.asked = []
        self.notebook_name = ""
        self.pid = 7

    def start(self, code):
        return self.first

    def poll(self, version, wait):
        self.asked.append((version, wait))
        return {"status": "ok", "outputs": []}

    def close(self):
        pass


def test_a_first_reply_with_no_version_polls_from_minus_one():
    k = Kernel({"status": "running"})
    lp.PythonSession(SessionConfig("nb"), k).execute(CellRequest("1"))
    assert k.asked == [(-1, 0.5)]


def test_a_first_reply_with_a_version_polls_from_it():
    k = Kernel({"status": "running", "version": 5})
    lp.PythonSession(SessionConfig("nb"), k).execute(CellRequest("1"))
    assert k.asked == [(5, 0.5)]


def test_a_cell_takes_the_time_between_its_start_and_its_end(monkeypatch):
    ticks = iter([10.0, 12.5])
    monkeypatch.setattr(lp.time, "perf_counter", lambda: next(ticks))
    r = lp.PythonSession(SessionConfig("nb"), Kernel({"status": "ok", "outputs": []})).execute(CellRequest("1"))
    assert r.seconds == 2.5 and r.engine == "local-python"


def test_the_python_engine_has_no_catalog_and_says_where_tables_are():
    s = lp.PythonSession(SessionConfig("nb"), Kernel({"status": "ok"}))
    with pytest.raises(Unsupported, match="Data panel"):
        s.catalog()
    with pytest.raises(Unsupported, match="no query plan"):
        s.plan("x")


def test_the_process_adapter_describes_the_python_engine_and_uses_its_factory():
    k = Kernel({"status": "ok"})
    adapter = lp.PythonAdapter(lambda: k)
    assert adapter.describe() is lp.PYTHON
    s = adapter.open_session(SessionConfig("sales"))
    assert s._kernel is k and k.notebook_name == "sales" and s.pid == 7 and not s.closed
