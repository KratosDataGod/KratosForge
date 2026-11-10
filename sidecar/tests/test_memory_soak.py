"""The short memory soak (examples/benchmark/memory_soak.py, run by .github/workflows/soak.yml): what counts
as steady growth, how it reports, and a few loops of the real thing."""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "examples" / "benchmark"


def load():
    spec = importlib.util.spec_from_file_location("memory_soak", BENCH / "memory_soak.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restored():
    """main points HOME, TEMP and PYTHONPATH at its own folder and adds the sidecar to sys.path: undo that."""
    env, tmp, path = dict(os.environ), tempfile.tempdir, list(sys.path)
    yield tempfile.gettempdir()
    os.environ.clear()
    os.environ.update(env)
    tempfile.tempdir = tmp
    sys.path[:] = path


def points(value, minutes=20, every=15):
    return [(t, value(t)) for t in range(0, minutes * 60 + 1, every)]


def test_a_flat_noisy_measurement_is_flat_and_a_steady_climb_is_growing():
    soak = load()
    noise = random.Random(4)
    flat = soak.growth(points(lambda t: 300 + noise.uniform(-12, 12)), 60, 25, 300)
    assert flat["status"] == "flat", flat
    leak = soak.growth(points(lambda t: 300 + t / 3600 * 200 + noise.uniform(-12, 12)), 60, 25, 300)
    assert leak["status"] == "growing" and 150 < leak["per_hour"] < 250, leak
    # Just past the minimum rise: 30 MB over the 6 minutes after a 2-minute warm-up (a planted 64 KB per loop).
    small = soak.growth(points(lambda t: 310 + max(t - 120, 0) / 12, minutes=8), 60, 25, 120)
    assert small["status"] == "growing", small


def test_one_jump_or_a_sawtooth_is_not_steady_growth():
    soak = load()
    # One step up late in the run: the slope is past the limit, but the medians of the thirds barely move.
    jump = soak.growth(points(lambda t: 300 + (100 if t > 1100 else 0)), 60, 25, 300)
    assert jump["per_hour"] > 60 and jump["status"] == "flat", jump
    # A kernel that grows and is restarted: up and down, no climb.
    saw = soak.growth(points(lambda t: 200 + (t % 300) / 10), 60, 25, 300)
    assert saw["status"] == "flat", saw


def test_a_climb_below_the_minimum_rise_or_inside_the_warmup_does_not_count():
    soak = load()
    threads = soak.growth(points(lambda t: 40 + min(t, 240) / 4), 40, 10, 300)  # pools filling, then level
    assert threads["status"] == "flat" and threads["first"] == threads["last"] == 100, threads
    small = soak.growth(points(lambda t: 20 + t / 600), 1, 10, 300)  # past 1 per hour, but only 1.5 in all
    assert small["status"] == "flat", small
    assert soak.growth(points(lambda t: 1, minutes=1), 1, 1, 300)["status"] == "too few samples"


def test_judge_skips_what_this_platform_cannot_measure():
    soak = load()
    samples = [{"t": t, **dict.fromkeys(soak.LIMITS, 5), "os_threads": None, "children": None} for t in range(0, 1200, 15)]
    out = soak.judge(samples, 300)
    assert out["os_threads"]["status"] == out["children"]["status"] == "not measured here"
    assert out["engine_mb"]["status"] == "flat" and out["engine_mb"]["unit"] == "MB"


def test_engine_memory_is_judged_on_its_low_so_big_write_buffers_are_not_growth():
    soak = load()
    noise = random.Random(7)
    # Windows private bytes with a Delta write's short-lived buffers: a level floor, highs up to 350 MB above it,
    # and more of them late in the run. Raw samples climb; the lows over a minute don't.
    swings = [(t, 420 + noise.uniform(0, 20) if t % 60 == 0 else 420 + noise.uniform(0, 1) ** (2 if t < 600 else 0.5) * 350)
              for t, _ in points(lambda t: 0)]
    raw = soak.growth(swings, 60, 25, 180)
    assert raw["status"] == "growing", raw
    samples = [{"t": t, **dict.fromkeys(soak.LIMITS, 5), "engine_mb": v} for t, v in swings]
    assert soak.judge(samples, 180)["engine_mb"]["status"] == "flat"
    # A leak raises the floor too: 120 MB an hour under the same swings.
    leak = [{"t": t, **dict.fromkeys(soak.LIMITS, 5), "engine_mb": v + t / 30} for t, v in swings]
    assert soak.judge(leak, 180)["engine_mb"]["status"] == "growing"
    assert soak.lows([(0, 5.0), (15, 3.0), (30, 9.0), (90, 8.0)], 60) == [(0, 5.0), (15, 3.0), (30, 3.0), (90, 8.0)]


def fake_run(errors=0, leak=False):
    samples = [{"t": t, **dict.fromkeys(soak_limits(), 5), "engine_mb": 300 + (t / 10 if leak else 0)}
               for t in range(0, 1200, 15)]
    return {"loops": 80, "counts": {}, "errors": errors, "last_error": {"sql": "boom"} if errors else {},
            "samples": samples}


def soak_limits():
    return load().LIMITS


@pytest.mark.parametrize("leak, errors, gate, code", [(False, 0, False, 0), (True, 0, False, 0), (True, 0, True, 1),
                                                     (False, 0, True, 0), (False, 2, False, 2)])
def test_growth_is_advisory_unless_gated_and_a_failed_step_always_fails(tmp_path, monkeypatch, capsys, restored, leak,
                                                                        errors, gate, code):
    soak = load()
    monkeypatch.setattr(soak, "soak", lambda *a: fake_run(errors, leak))
    args = ["--json", str(tmp_path / "r.json"), "--summary", str(tmp_path / "s.md")] + (["--gate"] if gate else [])
    assert soak.main(args) == code
    out = capsys.readouterr().out
    assert ("::warning title=Memory soak::engine_mb grew" in out) is leak
    assert ("::error title=Memory soak::2 steps failed" in out) is bool(errors)
    result = json.loads((tmp_path / "r.json").read_text())
    assert result["growth"]["engine_mb"]["status"] == ("growing" if leak else "flat")
    assert "| engine_mb | " + ("growing" if leak else "flat") in (tmp_path / "s.md").read_text()


def test_the_workflow_runs_linux_and_windows_and_is_advisory():
    workflow = (BENCH.parents[1] / ".github" / "workflows" / "soak.yml").read_text(encoding="utf-8")
    jobs = workflow.split("jobs:", 1)[1]
    assert "os: ubuntu-24.04" in jobs and "os: windows-2025-vs2026" in jobs
    assert "--minutes ${{ matrix.minutes }}" in jobs and "minutes: 20" in jobs and "minutes: 10" in jobs
    assert "--gate" not in jobs  # advisory until its limits have held on main for a while
    on = workflow.split("jobs:", 1)[0]
    assert "schedule:" in on and "workflow_dispatch:" in on and "push:" not in on  # nightly, not per merge


def test_a_few_loops_of_the_real_soak_fail_no_step_and_clean_up(tmp_path, restored):
    soak = load()
    before = set(Path(restored).glob("kf-soak-*"))
    assert soak.main(["--minutes", "0.2", "--warmup", "0", "--sample", "2", "--restart-every", "3", "--fresh-every", "2",
                      "--json", str(tmp_path / "r.json")]) == 0
    result = json.loads((tmp_path / "r.json").read_text())
    assert result["errors"] == 0 and result["loops"] >= 3, result["last_error"]
    assert result["counts"]["restarts"] >= 1 and result["counts"]["sql"] == result["loops"]
    s = result["samples"][-1]
    assert s["engine_mb"] > 0 and s["py_threads"] >= 1 and s["handles"]
    assert set(Path(restored).glob("kf-soak-*")) == before  # its folder is removed
