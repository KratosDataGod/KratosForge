"""Home: each recent notebook's details, running several notebooks at once, and items pinned to Home."""

import json
import subprocess
import threading
import time

import pytest

from fabric_desktop import notebook_batch_service as nbs
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_schedules import FakeScheduler


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    return Service(root=tmp_path / "app")


@pytest.fixture
def in_process(svc, monkeypatch):
    """The background processes, run here: Popen records what would start, run calls the notebook."""
    started = []
    monkeypatch.setattr(nbs.subprocess, "Popen", lambda args, **kw: started.append(args))

    one_at_a_time = threading.Lock()  # each would be a process of its own; here they share one kernel

    def run(args, **kw):
        with one_at_a_time:
            row = svc.run_batch_notebook(args[-2], int(args[-1]))
        return subprocess.CompletedProcess(args, 0, stdout="cell output\n" + json.dumps(row) + "\n", stderr="")
    monkeypatch.setattr(nbs.subprocess, "run", run)
    return started


def test_details_say_where_each_notebook_publishes_and_how_it_last_ran(svc):
    lh = {"workspace_id": "w1", "workspace_name": "KF_Dev", "lakehouse_id": "l1", "name": "tpch"}
    svc.notebooks.save("Own pick", [{"type": "code", "source": "a = 1"}], lakehouse=lh, compute="fabric",
                       workspace={"workspace_id": "w2", "workspace_name": "Sales_Dev"})
    svc.notebooks.save("From lakehouse", [{"type": "code", "source": "a = 1"}], lakehouse=lh)
    svc.notebooks.save("Bare", [{"type": "code", "source": "a = 1"}])
    svc.pc_schedules.log_run({"schedule_id": "", "label": "Bare", "trigger": "home", "started": "2026-10-29T10:00:00+00:00",
                              "ended": "2026-10-29T10:00:03+00:00", "seconds": 3, "status": "ok", "message": "1 cell ran"})
    got = {d["name"]: d for d in svc.notebook_details(["Own pick", "From lakehouse", "Bare", "Missing"])}
    assert (got["Own pick"]["workspace_name"], got["Own pick"]["lakehouse"], got["Own pick"]["compute"]) == ("Sales_Dev", "tpch", "fabric")
    assert (got["From lakehouse"]["workspace_id"], got["From lakehouse"]["compute"]) == ("w1", "default")
    assert got["Bare"]["workspace_name"] == "" and got["Bare"]["lakehouse"] == ""
    assert got["Bare"]["last_run"]["status"] == "ok" and got["Bare"]["last_run"]["trigger"] == "home"
    assert got["Own pick"]["last_run"] is None
    assert "error" in got["Missing"]


def test_several_notebooks_run_in_the_background_and_are_logged(svc, in_process):
    svc.notebooks.save("Good", [{"type": "code", "source": "a = 2"}, {"type": "code", "source": "b = a * 3"}])
    svc.notebooks.save("Bad", [{"type": "code", "source": "1 / 0"}])
    batch = svc.notebook_batch_start(["Good", "Bad", "Good"], parallel=9)
    assert [i["name"] for i in batch["items"]] == ["Good", "Bad"] and batch["parallel"] == nbs.MAX_PARALLEL
    assert in_process == [[nbs.sys.executable, "-m", "fabric_desktop.cli", "run-notebooks", batch["id"]]]
    svc.run_notebook_batch(batch["id"])
    done = svc.notebook_batch(batch["id"])
    assert done["state"] == "done"
    items = {i["name"]: i for i in done["items"]}
    assert items["Good"]["status"] == "ok" and items["Good"]["message"] == "2 cells ran"
    assert items["Bad"]["status"] == "failed" and "ZeroDivisionError" in items["Bad"]["message"]
    runs = svc.notebook_runs("Good")
    assert runs[0]["trigger"] == "home" and runs[0]["status"] == "ok"
    assert not list((svc.root / nbs.BATCHES).glob("*.progress"))


def test_stop_leaves_the_waiting_notebooks_unstarted(svc, in_process):
    for n in ("One", "Two"):
        svc.notebooks.save(n, [{"type": "code", "source": "a = 1"}])
    batch = svc.notebook_batch_start(["One", "Two"], parallel=1)
    assert svc.notebook_batch_stop(batch["id"])["stopping"] is True
    svc.run_notebook_batch(batch["id"])
    assert {i["status"] for i in svc.notebook_batch(batch["id"])["items"]} == {"skipped"}
    assert svc.notebook_runs("One") == []


def test_a_batch_whose_process_went_quiet_says_so(svc, in_process):
    svc.notebooks.save("One", [{"type": "code", "source": "a = 1"}])
    batch = svc.notebook_batch_start(["One"])
    path = svc.root / nbs.BATCHES / f"{batch['id']}.json"
    data = json.loads(path.read_text())
    data["beat"] = time.time() - nbs.LOST_SECONDS - 5
    path.write_text(json.dumps(data))
    lost = svc.notebook_batch(batch["id"])
    assert lost["state"] == "lost" and lost["items"][0]["status"] == "failed"


def test_batch_ids_and_names_are_checked(svc, in_process):
    with pytest.raises(ValueError):
        svc.notebook_batch("../../secrets")
    with pytest.raises(ValueError):
        svc.notebook_batch_start([])
    with pytest.raises(ValueError):
        svc.notebook_batch_start(["Not there"])
    with pytest.raises(ValueError):
        svc.notebook_batch_start(["../escape"])
    assert in_process == []


def test_item_pins_keep_their_order_and_leave_workspace_marks_alone(svc):
    svc.set_workspace_mark("w1", "pins", True)
    svc.set_item_pin("notebook", "TPC-H data", True)
    svc.set_item_pin("Lakehouse", "l1", True, name="gold", workspace_id="w1", workspace_name="Sales_Prod")
    svc.set_workspace_mark("w2", "favorites", True)  # a workspace mark keeps the item pins
    assert [(p["kind"], p["id"]) for p in svc.item_pins()] == [("notebook", "TPC-H data"), ("Lakehouse", "l1")]
    assert svc.workspace_marks() == {"favorites": ["w2"], "pins": ["w1"]}
    assert svc.set_item_pin("notebook", "TPC-H data", False) == [svc.item_pins()[0]]
    with pytest.raises(ValueError):
        svc.set_item_pin("", "x", True)
    with pytest.raises(ValueError):
        svc.set_item_pin("notebook", "../x", True)


def test_a_pinned_notebook_follows_a_rename(svc):
    svc.notebooks.save("Old", [{"type": "code", "source": "a = 1"}])
    svc.set_item_pin("notebook", "Old", True)
    svc.rename_notebook("Old", "New")
    assert [(p["id"], p["name"]) for p in svc.item_pins()] == [("New", "New")]


def test_item_pins_are_capped(svc, monkeypatch):
    monkeypatch.setattr("fabric_desktop.service.MAX_ITEM_PINS", 2)
    svc.set_item_pin("Lakehouse", "a", True)
    svc.set_item_pin("Lakehouse", "b", True)
    with pytest.raises(ValueError):
        svc.set_item_pin("Lakehouse", "c", True)
    svc.set_item_pin("Lakehouse", "a", True)  # pinning again moves it, not a new one
    assert [p["id"] for p in svc.item_pins()] == ["b", "a"]
