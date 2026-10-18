"""The attached lakehouse is one per engine: a running cell keeps the target it started with. A refused
second start, or an external tool's run against another lakehouse, must not re-point it (HD-004)."""

import threading
import time

import pytest

from fabric_desktop import lakehouse
from fabric_desktop.integrations.executions import Execution, ExecutionManager
from fabric_desktop.service import Service

DEV = {"workspace_id": "dev-ws", "lakehouse_id": "dev-lh", "name": "dev", "workspace_name": "dev-ws"}
PROD = {"workspace_id": "prod-ws", "lakehouse_id": "prod-lh", "name": "prod", "workspace_name": "prod-ws"}
CELL = ("from fabric_desktop import lakehouse\n"
        "before = lakehouse._current().workspace_id\n"
        "gate.wait(10)\n"
        "print(before, '->', lakehouse._current().workspace_id)")


@pytest.fixture
def svc(tmp_path):
    s = Service(client_id="x", root=tmp_path / "data")
    s.kernel = s._new_kernel(False)  # cells run in the engine and read the attached lakehouse as they go
    s._schema_cache = {"dev-lh": True, "prod-lh": True}
    s.kernel.namespace["gate"] = gate = threading.Event()
    yield s, gate
    gate.set()
    lakehouse.attach(None)


def running_cell(s):
    s.start_cell(CELL, DEV, notebook="nb")
    deadline = time.monotonic() + 5
    while "before" not in s.kernel.namespace and time.monotonic() < deadline:
        time.sleep(0.01)


def finish(s, gate):
    gate.set()
    snap = s.poll_cell(-1, 0)
    deadline = time.monotonic() + 10
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = s.poll_cell(-1, 0.5)
    return "".join(o.get("text", "") for o in snap["outputs"])


def test_refused_start_leaves_the_running_target(svc):
    s, gate = svc
    running_cell(s)
    with pytest.raises(RuntimeError, match="already running"):
        s.start_cell("print('second')", PROD, notebook="other")
    assert lakehouse.name() == "dev"
    assert finish(s, gate).strip() == "dev-ws -> dev-ws"


def test_external_run_cannot_repoint_a_running_cell(svc):
    s, gate = svc
    manager = ExecutionManager(s)
    running_cell(s)
    run = Execution("sql", "cl_1", "q", PROD, 30)
    with pytest.raises(RuntimeError, match="cell is running"):
        manager.run_sql(run, "SELECT 1", PROD, 10)
    assert lakehouse.name() == "dev"
    assert finish(s, gate).strip() == "dev-ws -> dev-ws"
    # Once the cell has finished, the external run may attach its own lakehouse.
    s._attach(PROD)
    assert lakehouse.name() == "prod"


def test_desktop_start_waits_for_an_external_run_holding_the_target(svc):
    s, gate = svc
    manager = ExecutionManager(s)
    assert manager._attach_lock is s.attach_lock
    with s.attach_lock:  # an external run is using its lakehouse
        s._attach(PROD)
        started = threading.Event()
        threading.Thread(target=lambda: (s.start_cell("x = 1", DEV), started.set()), daemon=True).start()
        assert not started.wait(0.3)
        assert lakehouse.name() == "prod"
    assert started.wait(5)
    assert lakehouse.name() == "dev"
