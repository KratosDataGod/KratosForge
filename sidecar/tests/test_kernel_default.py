"""PB-136: notebooks run in a process of their own by default, and a notebook's cells give the
same output there as inside the engine. Someone who turned the setting off, or an organization
whose policy sets it, keeps what they chose."""

from __future__ import annotations

import pytest
from test_kernel_sessions import finish, out
from test_prove import call  # noqa: F401

from fabric_desktop import app_settings
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.service import Service

pytestmark = pytest.mark.real_kernel_default

CELLS = [
    "x = 2",
    "print(x * 21)",
    "import duckdb\nprint(duckdb.sql('SELECT sum(range) FROM range(10)').fetchone()[0])",
    "import polars as pl\ndf = pl.DataFrame({'a': [1, 2, 3]})\nprint(df['a'].sum())",
    "def f(n):\n    return n + x\nprint(f(40))",
    "1 / 0",
]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    yield s
    for k in s.sessions.kernels():
        close = getattr(k, "close", None)
        if close:
            close()


def _run(svc, notebook: str) -> list[tuple[str, str, list[str]]]:
    got = []
    for code in CELLS:
        call(svc, "start_cell", code=code, notebook=notebook)
        snap = finish(svc, notebook, timeout=120)  # a loaded CI runner starts processes and imports slowly
        errors = [f"{o.get('ename')}: {o.get('evalue')}" for o in snap.get("outputs") or [] if o.get("type") == "error"]
        got.append((snap["status"], out(snap).strip(), errors))
        assert snap["status"] != "running", (code, snap)
    return got


def test_the_process_kernel_is_the_default(svc):
    setting = app_settings.load()["kernel_process"]
    assert setting == {"value": True, "source": "default"}
    assert call(svc, "kernel_sessions")["process"] is True


def test_a_notebook_runs_the_same_in_its_own_process_as_inside_the_engine(svc):
    own = _run(svc, "nb")
    assert isinstance(svc.sessions.kernel("nb"), ProcessKernel)
    call(svc, "save_app_settings", changes={"kernel_process": False})
    inside = _run(svc, "nb")
    assert not isinstance(svc.sessions.kernel("nb"), ProcessKernel)
    # Whole tuples (status, output, errors), so a failure shows what each cell did.
    assert own[:5] == [("ok", "", []), ("ok", "42", []), ("ok", "45", []), ("ok", "6", []), ("ok", "42", [])]
    assert own[5][0] == "error" and own[5][2] == ["ZeroDivisionError: division by zero"]
    assert inside == own


def test_someone_who_turned_it_off_keeps_it_off(svc):
    call(svc, "save_app_settings", changes={"kernel_process": False})
    assert app_settings.load()["kernel_process"] == {"value": False, "source": "user"}
    assert call(svc, "kernel_sessions")["process"] is False
