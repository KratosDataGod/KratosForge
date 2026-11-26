"""Deadlines that time asleep doesn't use up (awake_timer.awake_monotonic).

A notebook kernel's cell time limit and a pipeline activity's timeouts (its policy timeout, an
Until's timeout) were deadlines on time.monotonic, which on Windows counts sleep, so a cell or an
activity going when the PC slept was stopped on waking. Here time.monotonic jumps forward by hours
mid-run, with timed waits counted on it as on Windows (sleep_jump, tests/conftest.py). Each test
below except the clock's own fails with time.monotonic in place of awake_monotonic.
"""

from __future__ import annotations

import json
import threading
import time

from fabric_desktop import awake_timer, kernel_proc
from fabric_desktop.awake_timer import AwakeClock
from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.runner import PipelineRun

HOURS = 3 * 3600.0


def pause(seconds: float) -> None:
    """Wait real time, on a clock the jump doesn't move."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0.02)


# ---- the clock

def test_the_awake_clock_leaves_a_jump_of_hours_out_and_never_goes_back(sleep_jump):
    clock = AwakeClock()
    readings = [clock.now()]
    pause(0.3)
    readings.append(clock.now())
    sleep_jump(HOURS)
    readings.append(clock.now())  # read before its next step: at most one step more
    pause(1.5)  # it steps past the jump
    readings.append(clock.now())
    pause(0.5)
    readings.append(clock.now())
    assert readings == sorted(readings)
    assert readings[-1] - readings[0] < 3 * awake_timer.STEP + 3  # not hours
    assert readings[-1] - readings[-2] >= 0.3  # and counts on once awake


def test_a_clock_going_back_isnt_counted_and_doesnt_move_it_back():
    now = [1000.0]
    clock = AwakeClock(clock=lambda: now[0])
    clock._ticker = threading.current_thread()  # stepped by hand here
    clock._step()
    first = clock.now()
    now[0] -= 50
    clock._step()
    assert clock.now() == first


# ---- the notebook kernel's cell time limit

class _Proc:
    pid = 4242

    def __init__(self):
        self.ended = False

    def poll(self):
        return 0 if self.ended else None


def test_a_kernel_cell_going_when_the_pc_slept_isnt_interrupted_on_waking(sleep_jump, monkeypatch):
    monkeypatch.setattr(kernel_proc, "WATCH_SECONDS", 0.05)
    k = kernel_proc.ProcessKernel()
    proc = _Proc()
    k._proc, k.pid = proc, proc.pid
    asked, ends = [], []
    monkeypatch.setattr(k, "_memory", lambda p, pid: 0)
    monkeypatch.setattr(k, "_request", lambda what, **kw: asked.append(what))
    monkeypatch.setattr(k, "_end", lambda p, why: ends.append(why))
    k.time_limit, k._running, k._started_at = 3, True, kernel_proc.awake_monotonic()
    watcher = threading.Thread(target=k._watch, args=(proc,), daemon=True)
    watcher.start()
    try:
        pause(0.5)
        sleep_jump(HOURS)
        pause(1.0)
        assert asked == [] and k.limit_hit == "" and ends == []
        # Past its limit in awake time, it is interrupted as before.
        deadline = time.perf_counter() + 10
        while not asked and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert asked == ["interrupt"] and k.limit_hit == "time"
    finally:
        proc.ended = True
        watcher.join(5)


# ---- pipeline activity timeouts

def _pipeline(*activities, variables=None):
    props = {"activities": list(activities)}
    if variables:
        props["variables"] = variables
    return json.dumps({"name": "p", "properties": props})


def _act(name, typ, **extra):
    return {"name": name, "type": typ, "dependsOn": [], **extra}


class _SlowRead:
    """A lakehouse read that takes 2 s of real time, not a wait the jump could cut short."""

    def check(self, a):
        return []

    def lookup(self, a, props):
        pause(2)
        return {"firstRow": {"n": 1}}

    metadata = lookup


def _run_with_a_jump(text, sleep_jump, after=0.5, **kw):
    r = PipelineRun(loads(text), wait_scale=1, **kw)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    pause(after)
    sleep_jump(HOURS)
    t.join(30)
    assert not t.is_alive()
    return r


def test_an_activity_going_when_the_pc_slept_isnt_timed_out_on_waking(sleep_jump):
    read = _act("L", "Lookup", typeProperties={"datasetSettings": {"type": "LakehouseTable"}, "source": {}},
                policy={"timeout": "0.00:00:05"})
    r = _run_with_a_jump(_pipeline(read), sleep_jump, data=_SlowRead())
    assert r.state == "succeeded", r.to_json()["activities"]


def test_an_activity_past_its_policy_timeout_is_still_timed_out():
    read = _act("L", "Lookup", typeProperties={"datasetSettings": {"type": "LakehouseTable"}, "source": {}},
                policy={"timeout": "0.00:00:01"})
    r = PipelineRun(loads(_pipeline(read)), wait_scale=1, data=_SlowRead()).run()
    assert r.state == "failed"
    assert r.to_json()["activities"][0]["error"]["errorCode"] == "LocalActivityTimeout"


def test_an_until_going_when_the_pc_slept_isnt_timed_out_on_waking(sleep_jump):
    inner = [_act("W", "Wait", typeProperties={"waitTimeInSeconds": 1}),
             {"name": "Add", "type": "AppendVariable", "dependsOn": [{"activity": "W", "dependencyConditions": ["Succeeded"]}],
              "typeProperties": {"variableName": "xs", "value": 1}}]
    until = _act("U", "Until", typeProperties={"expression": {"value": "@equals(length(variables('xs')), 2)", "type": "Expression"},
                                               "timeout": "0.00:00:06", "activities": inner})
    r = _run_with_a_jump(_pipeline(until, variables={"xs": {"type": "Array", "defaultValue": []}}), sleep_jump)
    assert r.state == "succeeded", r.to_json()["activities"]
    assert r.variables["xs"] == [1, 1]
