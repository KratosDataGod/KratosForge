"""Two limits left by the sleep and clock-change work: incident escalation doesn't move when a PC's
clock does, and a second agent pointed at a local control folder another agent works in is
refused, with a message that says which agent holds it."""

import dataclasses
import json
import subprocess
import sys
import threading

import pytest

from fabric_desktop.agent import Agent
from fabric_desktop.leases import LocalBackend
from fabric_desktop.mailbox import CONTROL_LOCK, ControlFolderInUse, hold_control_folder
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.incidents import KEY, IncidentLog

from test_agent import Clock, setup  # noqa: F401 - setup is a fixture

MIN = 60.0
EMPTY = json.dumps({"contract": 1, "kind": "incidents", "env": "prod", "incidents": []}).encode()


def failed(entity="silver.orders", run_id="r1"):
    run = {"entity": entity, "status": "failed", "run_id": run_id, "started_at": "2026-10-16T10:00:00+00:00",
           "message": "boom"}
    return evaluate.signals("sales", {entity: {}}, [run], "prod")


class StoreClock(LocalBackend):
    """A control folder that answers with a store's own clock, the way OneLake's Date header does."""

    def __init__(self, folder, store):
        super().__init__(folder)
        self.store = store

    def read(self, key):
        got = super().read(key)
        if got is not None:
            got.store_now = self.store()
        return got


# ---- escalation timing

def _log(backend, wall, mono, seen=None):
    return IncidentLog(backend, "prod", wall, monotonic=mono, first_seen={} if seen is None else seen)


def test_a_clock_jumping_forward_doesnt_escalate_early(tmp_path):
    wall, mono, seen = Clock(), Clock(1000.0), {}
    backend = LocalBackend(tmp_path / "control")
    _log(backend, wall, mono, seen).sync(failed(), evaluated=True, by="agent etl-01")
    assert _log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01") == []
    wall.t += 3 * 3600  # someone sets the clock three hours on; ten real minutes pass
    mono.t += 10 * MIN
    assert _log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01") == []
    mono.t += 50 * MIN  # an hour of real time
    [c] = _log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01")
    assert c["change"] == "escalated" and seen == {}  # done with: nothing kept for it


def test_a_clock_going_back_doesnt_hold_an_escalation(tmp_path):
    wall, mono, seen = Clock(), Clock(1000.0), {}
    backend = LocalBackend(tmp_path / "control")
    _log(backend, wall, mono, seen).sync(failed(), evaluated=True, by="agent etl-01")
    _log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01")
    wall.t -= 2 * 3600
    mono.t += 61 * MIN
    assert len(_log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01")) == 1


def test_an_incident_already_open_when_first_seen_counts_the_time_before(tmp_path):
    wall, mono = Clock(), Clock(1000.0)
    backend = LocalBackend(tmp_path / "control")
    _log(backend, wall, mono).sync(failed(), evaluated=True, by="the app")
    wall.t += 50 * MIN
    mono.t += 50 * MIN
    seen = {}  # an agent that starts now
    assert _log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01") == []
    mono.t += 10 * MIN
    wall.t += 10 * MIN
    assert len(_log(backend, wall, mono, seen).escalate(MIN * 60, "on-call", "agent etl-01")) == 1


def test_an_acknowledged_incident_leaves_nothing_behind(tmp_path):
    wall, mono, seen = Clock(), Clock(1000.0), {}
    backend = LocalBackend(tmp_path / "control")
    log = _log(backend, wall, mono, seen)
    log.sync(failed(), evaluated=True, by="agent etl-01")
    log.escalate(MIN * 60, "on-call", "agent etl-01")
    assert len(seen) == 1
    log.acknowledge(log.active()[0]["id"], "dana")
    log.escalate(MIN * 60, "on-call", "agent etl-01")
    assert seen == {}


def test_in_onelake_escalation_runs_on_onelakes_clock_whatever_each_pc_says(tmp_path):
    store = Clock(1_900_000_000.0)
    backend = StoreClock(tmp_path / "control", store)
    fast_pc = Clock(store.t + 3 * 3600)  # three hours ahead
    backend.create(KEY, EMPTY)  # the list exists, so reads carry the store's clock
    _log(backend, fast_pc, Clock(0.0)).sync(failed(), evaluated=True, by="agent etl-01")
    [inc] = _log(backend, fast_pc, Clock(0.0)).active()
    assert inc["opened_store_at"] and inc["opened_at"] != inc["opened_store_at"]
    store.t += 30 * MIN
    fast_pc.t += 30 * MIN
    slow_pc = Clock(store.t - 3 * 3600)
    # A PC that just started (nothing seen yet), whichever way its clock is off: not yet.
    assert _log(backend, fast_pc, Clock(0.0)).escalate(MIN * 60, "on-call", "agent etl-01") == []
    assert _log(backend, slow_pc, Clock(0.0)).escalate(MIN * 60, "on-call", "agent etl-02") == []
    store.t += 31 * MIN
    slow_pc.t += 31 * MIN
    assert len(_log(backend, slow_pc, Clock(0.0)).escalate(MIN * 60, "on-call", "agent etl-02")) == 1


def test_a_reported_incident_keeps_its_onelake_open_time_too(tmp_path):
    store = Clock(1_900_000_000.0)
    backend = StoreClock(tmp_path / "control", store)
    backend.create(KEY, EMPTY)
    log = _log(backend, Clock(store.t - 600), Clock(0.0))
    inc = log.report("Totals look wrong", "dana")
    assert inc["opened_store_at"].startswith("2030-03-17")


def test_the_agent_escalates_on_its_monotonic_clock(setup, monkeypatch):  # noqa: F811
    wall = Clock()
    agent = Agent(setup, clock=wall)
    agent.monotonic = mono = Clock(500.0)
    first = agent._incident_seen.setdefault("dev", {})
    log = IncidentLog(agent._control("dev"), "dev", wall, monotonic=mono, first_seen=first)
    log.sync(failed(), evaluated=True, by="agent etl-01")
    log.escalate(3600, "on-call", "agent etl-01")
    wall.t += 5 * 3600
    assert IncidentLog(agent._control("dev"), "dev", wall, monotonic=mono, first_seen=first).escalate(
        3600, "on-call", "agent etl-01") == []


# ---- one agent per local control folder

def test_a_second_holder_of_a_control_folder_is_refused_and_told_who_holds_it(tmp_path):
    folder = tmp_path / "_control"
    held = hold_control_folder(folder, {"agent": "etl-01", "pid": 4242, "host": "PC-7"})
    with pytest.raises(ControlFolderInUse) as err:
        hold_control_folder(folder, {"agent": "etl-02", "pid": 1, "host": "PC-7"})
    text = str(err.value)
    assert "agent etl-01 (process 4242 on PC-7)" in text and str(folder) in text
    assert "Stop that agent first" in text
    held.close()
    hold_control_folder(folder, {"agent": "etl-02"}).close()  # free once the first lets go


def test_a_lock_file_naming_nobody_still_refuses_plainly(tmp_path):
    folder = tmp_path / "_control"
    held = hold_control_folder(folder, {"agent": "../../bad name", "pid": "x"})
    with pytest.raises(ControlFolderInUse) as err:
        hold_control_folder(folder, {"agent": "etl-02"})
    assert "in use by another agent." in str(err.value)
    held.close()


def test_an_agent_that_was_killed_doesnt_leave_the_folder_held(tmp_path):
    folder = tmp_path / "_control"
    code = ("import sys, time; from fabric_desktop.mailbox import hold_control_folder; from pathlib import Path\n"
            "held = hold_control_folder(Path(sys.argv[1]), {'agent': 'etl-01'}); print('held', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(folder)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(ControlFolderInUse):
            hold_control_folder(folder, {"agent": "etl-02"})
    finally:
        proc.kill()
        proc.wait(30)
    hold_control_folder(folder, {"agent": "etl-02"}).close()


def test_a_second_agent_on_the_same_local_folder_doesnt_start(setup):  # noqa: F811
    first = Agent(dataclasses.replace(setup, poll_seconds=0.05))
    loop = threading.Thread(target=first.run_forever, kwargs={"install_signals": False})
    loop.start()
    lock = setup.project / ".local" / "dev" / "_control" / CONTROL_LOCK
    try:
        for _ in range(200):
            if lock.exists() and lock.stat().st_size > 1:
                break
            threading.Event().wait(0.05)
        second = Agent(dataclasses.replace(setup, name="etl-02"))
        with pytest.raises(ControlFolderInUse) as err:
            second.run_forever(install_signals=False)
        assert "agent etl-01" in str(err.value)
        assert not (lock.parent / "agents" / "etl-02.json").exists()  # it wrote nothing there
        assert second._held == {}
    finally:
        first.stop()
        loop.join(30)
    assert json.loads(lock.read_bytes()[1:])["agent"] == "etl-01"  # left as it was; the lock is gone
    again = Agent(dataclasses.replace(setup, name="etl-02"))
    again.hold_control_folders()  # free now
    assert len(again._held) == 1
    again.release_control_folders()


def test_agents_used_without_running_hold_nothing(setup):  # noqa: F811
    agent = Agent(setup)
    agent._control("dev")  # the app's checks and the CLI build agents without running them
    assert agent._held == {}
    assert not (setup.project / ".local" / "dev" / "_control" / CONTROL_LOCK).exists()
