"""Mutation batch 24: side by side's cores choice and how long it waits on Fabric, with fakes only."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fabric_desktop import side_by_side as sbs
from fabric_desktop import side_by_side_service as sbss
from fabric_desktop.side_by_side_service import SideBySideMixin

LAKEHOUSE = {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class Notebooks:
    def __init__(self, saved: Any = None):
        self.saved = {} if saved is None else {"sbs_cores": saved}

    def extra(self, name: str, key: str) -> Any:
        return self.saved.get(key)

    def set_extra(self, name: str, key: str, value: Any) -> None:
        self.saved[key] = value


class Host(SideBySideMixin):
    def __init__(self, root: Path, saved: Any = None):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.notebooks = Notebooks(saved)


@pytest.mark.parametrize("saved, choice", [("all", "all"), ("match", "match"), (2, 2),
                                           (True, sbs.DEFAULT_CORES), (0, sbs.DEFAULT_CORES),
                                           (-3, sbs.DEFAULT_CORES), ("half", sbs.DEFAULT_CORES),
                                           (None, sbs.DEFAULT_CORES)])
def test_a_saved_cores_choice_is_kept_only_when_it_is_one_of_the_choices(tmp_path, saved, choice):
    assert Host(tmp_path / "app", saved)._sbs_cores_choice("NB") == choice


@pytest.mark.parametrize("choice", ["all", "match", 1])
def test_setting_a_good_cores_choice_saves_it_and_answers_with_the_new_card(tmp_path, choice):
    host = Host(tmp_path / "app")
    answer = host.side_by_side_cores_set("NB", [], choice)
    assert host.notebooks.saved == {"sbs_cores": choice}
    assert answer["choice"] == choice
    assert answer["pc_cores"] == os.cpu_count()
    assert set(answer) == {"choice", "pc_cores", "engine", "fabric_vcores", "cores"}


@pytest.mark.parametrize("bad", ["half", True, False, 0, None])
def test_setting_a_bad_cores_choice_saves_nothing(tmp_path, bad):
    host = Host(tmp_path / "app")
    with pytest.raises(ValueError, match="Cores must be all, match, or a number from 1 to"):
        host.side_by_side_cores_set("NB", [], bad)
    assert host.notebooks.saved == {}


class FabricHost(Host):
    def __init__(self, root: Path):
        super().__init__(root)
        self.fabric = SimpleNamespace(job_instance=lambda *a: {"status": "InProgress"})
        self.fabric_jobs = SimpleNamespace(run_item_job=lambda *a: "job1")
        self.auth = SimpleNamespace(storage_token=lambda: "t")

    def _publish(self, *a, **kw):
        return {"workspace_id": "w", "id": "nb", "url": "u", "workspace_name": "WS"}

    def _log_fabric_run(self, entry):
        pass

    def _cost_settings(self):
        return {"python_cu": 1, "spark_cu": 2, "price_per_cu_hour": 1, "currency": "USD"}


class NoTiming:
    def __init__(self, token):
        pass

    def read_bytes(self, *a):
        raise FileNotFoundError("timing file")


class Clock:
    """A clock that moves an hour on each sleep, so six hours pass without waiting."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += 3600


def test_fabric_is_asked_every_five_seconds_and_given_up_on_after_six_hours(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sbss, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setattr(sbss, "FileStore", NoTiming)
    host = FabricHost(tmp_path / "app")
    r = {"id": "r1", "name": "NB", "fabric": {"status": "pending", "engine": "python", "vcores": 4}}
    host._sbs_fabric(r, [], LAKEHOUSE, None, {})
    assert clock.sleeps == [5] * 6
    assert r["fabric"]["total_seconds"] == 6 * 3600
    assert r["fabric"]["status"] == "failed" and r["fabric"]["job_status"] == "InProgress"


# ---- the history file

def _row(i: int) -> dict[str, Any]:
    return {"id": f"r{i:03d}", "name": "NB", "started_at": f"2026-11-07T10:{i // 60:02d}:{i % 60:02d}+00:00",
            "local": {"status": "ok"}}


def _write_rows(host: Host, rows: list[dict[str, Any]]) -> None:
    host.side_by_side_file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_a_clean_history_file_is_read_without_being_set_aside(tmp_path):
    host = Host(tmp_path / "app")
    _write_rows(host, [_row(1), _row(2)])
    before = host.side_by_side_file.read_bytes()
    assert [r["id"] for r in host._sbs_saved()] == ["r001", "r002"]
    assert sorted(p.name for p in host.root.iterdir()) == ["side_by_side.jsonl"]
    assert host.side_by_side_file.read_bytes() == before


def test_a_torn_file_stays_in_place_when_the_repair_cant_write(tmp_path, monkeypatch):
    host = Host(tmp_path / "app")
    torn = json.dumps(_row(1)) + "\n{torn\n"
    host.side_by_side_file.write_text(torn, encoding="utf-8")

    def refuse(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(sbss.jsonfile, "write_text", refuse)
    assert [r["id"] for r in host._sbs_saved()] == ["r001"]
    assert host.side_by_side_file.read_text(encoding="utf-8") == torn
    copies = [p for p in host.root.iterdir() if ".corrupt-" in p.name]
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == torn


def test_the_history_keeps_the_newest_200_comparisons(tmp_path):
    host = Host(tmp_path / "app")
    _write_rows(host, [_row(i) for i in range(200)])
    new = dict(_row(999), local={"status": "ok", "cells": {"0": {"outputs": ["big"]}}})
    host._sbs_save(new)
    ids = [r["id"] for r in host._sbs_saved()]
    assert len(ids) == 200 and ids[0] == "r001" and ids[-1] == "r999"
    assert host._sbs_saved()[-1]["local"] == {"status": "ok"}


def test_the_list_shows_the_newest_20_by_default(tmp_path):
    host = Host(tmp_path / "app")
    _write_rows(host, [_row(i) for i in range(25)])
    rows = host.side_by_side_list()
    assert [r["id"] for r in rows] == [f"r{i:03d}" for i in range(24, 4, -1)]


# ---- cores on this PC

def test_the_cores_card_says_what_a_choice_comes_to_on_this_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "os", SimpleNamespace(cpu_count=lambda: 8))
    assert Host(tmp_path / "a", 2).side_by_side_cores("NB", [])["cores"] == 2
    assert Host(tmp_path / "b", "all").side_by_side_cores("NB", [])["cores"] == 8


def test_a_pc_that_cant_count_its_cores_takes_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "os", SimpleNamespace(cpu_count=lambda: None))
    host = Host(tmp_path / "app")
    host.side_by_side_cores_set("NB", [], 1)
    assert host.notebooks.saved == {"sbs_cores": 1}
    with pytest.raises(ValueError, match="a number from 1 to 1$"):
        host.side_by_side_cores_set("NB", [], 2)


def test_a_count_up_to_this_pcs_cores_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "os", SimpleNamespace(cpu_count=lambda: 8))
    host = Host(tmp_path / "app")
    assert host.side_by_side_cores_set("NB", [], 8)["choice"] == 8
    with pytest.raises(ValueError, match="a number from 1 to 8$"):
        host.side_by_side_cores_set("NB", [], 9)


# ---- starting a run

class FakeThread:
    made: list[dict[str, Any]] = []

    def __init__(self, **kw):
        FakeThread.made.append(kw)

    def start(self):
        pass


class StartHost(Host):
    def __init__(self, root: Path, cell_ids: list[str] | None = None):
        super().__init__(root)
        self.cell_ids = cell_ids
        self.notebooks.save = self._save
        self.auth = SimpleNamespace(storage_token=lambda: "t", fabric_token=lambda: "t")

    def _save(self, name, cells, *a):
        return {"cell_ids": self.cell_ids if self.cell_ids is not None else [f"c{i}" for i in range(len(cells))]}

    def _replay_refuse(self, name, doing):
        pass

    def _replay_live(self):
        return False

    def _checks(self, name):
        return {}

    def _bar_parameters(self, name, cells):
        return None

    def _bar_suffixes(self, name, cells):
        return []

    def _publish_workspace(self, name, ref, ws):
        return {"workspace_id": "w", "workspace_name": "WS"}


def _checked(local: list[dict[str, Any]] | None = None):
    return lambda cells, ref, **kw: {"local": {"reasons": local or []}, "fabric": {"reasons": []}}


CELLS = [{"type": "code", "source": "x = 1"}]


@pytest.mark.parametrize("reason, said", [({"cell": 3, "text": "uses a GPU"}, "Cell 3 uses a GPU"),
                                          ({"text": "uses a GPU"}, "It uses a GPU")])
def test_a_reason_it_cant_run_here_names_its_cell_when_it_has_one(tmp_path, monkeypatch, reason, said):
    monkeypatch.setattr(sbss, "compute_check", _checked([reason]))
    with pytest.raises(ValueError) as err:
        StartHost(tmp_path / "app").side_by_side_start("NB", CELLS, LAKEHOUSE)
    assert str(err.value) == f"This notebook can't run on this PC: {said}."


def test_a_save_that_doesnt_name_every_cell_stops_the_start(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "compute_check", _checked())
    monkeypatch.setattr(sbss, "threading", SimpleNamespace(Thread=FakeThread))
    with pytest.raises(ValueError):
        StartHost(tmp_path / "app", cell_ids=[]).side_by_side_start("NB", CELLS, LAKEHOUSE)


def test_a_started_run_has_a_dated_id_and_runs_on_a_background_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "compute_check", _checked())
    monkeypatch.setattr(sbss, "threading", SimpleNamespace(Thread=FakeThread))
    FakeThread.made.clear()
    status = StartHost(tmp_path / "app").side_by_side_start("NB", CELLS, LAKEHOUSE)
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", status["id"])
    assert len(FakeThread.made) == 1
    made = FakeThread.made[0]
    assert made["daemon"] is True and made["name"] == f"side-by-side-{status['id']}"


# ---- the run on this PC

class PollKernel:
    def __init__(self, record: dict[str, Any], snaps: list[dict[str, Any]]):
        self.record, self.snaps = record, list(snaps)
        self.polls: list[tuple[Any, float]] = []
        self.seen: list[dict[str, Any]] = []
        self.started: list[str] = []
        self.closed = False

    def start(self, source):
        self.started.append(source)

    def poll(self, version, timeout):
        self.polls.append((version, timeout))
        self.seen.append(json.loads(json.dumps(self.record["local"]["cells"])))
        return self.snaps.pop(0)

    def close(self):
        self.closed = True


class LocalHost(Host):
    def __init__(self, root: Path, kernel: PollKernel):
        super().__init__(root)
        self.kernel, self.made_with = kernel, None

    def _attach(self, ref):
        pass

    def _new_kernel(self, **kw):
        self.made_with = kw
        return self.kernel

    def _prepare_kernel(self, name, kernel):
        pass

    def _resources_dir(self, name):
        return None

    def _onelake_writes(self, name):
        return None

    def _saved_inputs(self, name):
        return None


def _local_record(**over) -> dict[str, Any]:
    r = {"id": "r1", "name": "NB", "_cancel": False, "_cores": 2,
         "local": {"status": "pending", "seconds": None, "cell": None, "cells_total": 1, "cells": {}}}
    return r | over


def test_a_held_run_gets_a_kernel_process_of_its_own_and_closes_it(tmp_path):
    r = _local_record()
    kernel = PollKernel(r, [])
    host = LocalHost(tmp_path / "app", kernel)
    host._sbs_local(r, [], [], LAKEHOUSE)
    assert host.made_with == {"process": True} and kernel.cores == 2 and kernel.closed
    assert r["local"]["status"] == "ok"


def test_each_cell_is_polled_from_the_start_and_its_last_output_is_kept(tmp_path, monkeypatch):
    clock = iter([10.0, 11.23456])
    monkeypatch.setattr(sbss, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda s: None))
    error = {"type": "error", "ename": "ValueError", "evalue": "v" * 2000}
    r = _local_record()
    kernel = PollKernel(r, [
        {"status": "running", "seconds": 0.1, "version": 1, "outputs": ["first"]},
        {"status": "running", "seconds": 0.2, "version": 2},
        {"status": "failed", "seconds": 0.3, "version": 3},
        {"status": "failed", "seconds": 0.3, "outputs": ["first", error], "execution_count": 1},
    ])
    host = LocalHost(tmp_path / "app", kernel)
    host._sbs_cells(r, kernel, [{"type": "code", "source": "boom()"}], [0])
    assert kernel.polls == [(-1, 0.0), (1, 1.0), (2, 1.0), (-1, 0.0)]
    assert kernel.seen[1]["0"] == {"status": "running", "seconds": 0.1, "outputs": ["first"]}
    local = r["local"]
    assert local["cells"]["0"]["outputs"] == ["first", error]
    assert local["cell"] == 1 and local["failed_cell"] == 1 and local["status"] == "failed"
    assert local["message"] == ("ValueError: " + "v" * 2000)[:1000] and len(local["message"]) == 1000
    assert local["seconds"] == 1.23


def test_every_cell_that_runs_is_numbered_from_one(tmp_path, monkeypatch):
    r = _local_record()
    done = {"status": "ok", "seconds": 0.1, "outputs": []}
    kernel = PollKernel(r, [done, done, done, done])
    host = LocalHost(tmp_path / "app", kernel)
    host._sbs_cells(r, kernel, [{"source": "a = 1"}, {"source": "b = 2"}], [0, 1])
    assert r["local"]["cell"] == 2 and r["local"]["status"] == "ok"
    assert kernel.started == ["a = 1", "b = 2"]


# ---- the Fabric half's timings and cost

class StepClock(Clock):
    def __init__(self, start: float, step: float):
        super().__init__()
        self.now, self.step = start, step

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += self.step


def _timing(seconds: float):
    class Timing:
        def __init__(self, token):
            pass

        def read_bytes(self, *a):
            return json.dumps({"seconds": seconds}).encode()

        def delete(self, *a):
            pass

    return Timing


def _fabric_run(tmp_path, monkeypatch, start: str, end: str, run_seconds: float, engine: str = "spark"):
    clock = StepClock(100.0, 1.23456)
    monkeypatch.setattr(sbss, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setattr(sbss, "FileStore", _timing(run_seconds))
    host = FabricHost(tmp_path / "app")
    host.fabric = SimpleNamespace(job_instance=lambda *a: {"status": "Completed", "startTimeUtc": start, "endTimeUtc": end})
    r = {"id": "r1", "name": "NB", "fabric": {"status": "pending", "engine": engine, "vcores": 4}}
    host._sbs_fabric(r, [], LAKEHOUSE, None, {})
    return r["fabric"]


def test_a_short_fabric_run_is_timed_to_the_hundredth_and_costed(tmp_path, monkeypatch):
    f = _fabric_run(tmp_path, monkeypatch, "2026-11-07T10:00:00Z", "2026-11-07T10:00:01.23456Z", 0.001)
    assert f["status"] == "ok"
    assert f["total_seconds"] == 1.23
    assert f["job_seconds"] == 1.23 and f["run_seconds"] == 0.001 and f["start_seconds"] == 1.23
    assert f["cu_seconds"] == 2.5  # 1.23 s at 2 CU for Spark
    assert f["cost"] == 0.0007


def test_a_long_fabric_run_is_costed_by_the_hour(tmp_path, monkeypatch):
    f = _fabric_run(tmp_path, monkeypatch, "2026-11-07T10:00:00Z", "2026-11-07T15:00:00Z", 17999.5)
    assert f["job_seconds"] == 18000 and f["start_seconds"] == 0.5
    assert f["cu_seconds"] == 36000.0 and f["cost"] == 10.0 and f["currency"] == "USD"


def test_a_tiny_fabric_cost_keeps_four_decimals(tmp_path, monkeypatch):
    f = _fabric_run(tmp_path, monkeypatch, "2026-11-07T10:00:00Z", "2026-11-07T10:00:01Z", 0.5, engine="python")
    assert f["cu_seconds"] == 1.0 and f["cost"] == 0.0003 and f["start_seconds"] == 0.5


def test_a_start_up_split_is_never_below_nothing(tmp_path, monkeypatch):
    f = _fabric_run(tmp_path, monkeypatch, "2026-11-07T10:00:00Z", "2026-11-07T10:00:10Z", 12.0)
    assert f["start_seconds"] == 0


# ---- helpers

def test_upload_time_and_speed_are_rounded_to_the_hundredth():
    split = sbss._split(10, [{"wait_seconds": 2, "upload_seconds": 1.23456, "bytes": 1}])
    assert split["upload"]["seconds"] == 1.23 and split["processing_seconds"] == 8
    assert sbss._split(10, [{"upload_seconds": 3, "bytes": 2_000_000}])["upload"]["mb_per_second"] == 0.67


def test_upload_speed_is_given_for_an_upload_under_a_second():
    assert sbss._split(10, [{"upload_seconds": 0.5, "bytes": 1_000_000}])["upload"]["mb_per_second"] == 2.0
    assert sbss._split(10, [{"upload_seconds": 0, "bytes": 1_000_000}])["upload"]["mb_per_second"] is None


def test_writes_are_read_from_code_cells_only_and_named_from_cell_one():
    w = sbss._writes([{"type": "code", "source": "exec('x')"},
                      {"type": "markdown", "source": "df.write.saveAsTable('t1')"},
                      {"type": "code", "source": "df.write.saveAsTable('t2')", "meta": {"frozen": True}},
                      {"type": "code", "source": "   "}])
    assert w["tables"] == [] and w["unknown"] is True
    assert w["reasons"] == ["Cell 1: exec() can read or define any name"]


def test_a_record_that_keeps_changing_is_tried_20_times_then_called_busy(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(sbss, "time", SimpleNamespace(sleep=sleeps.append))

    class Busy(dict):
        def items(self):
            raise RuntimeError("dictionary changed size during iteration")

    with pytest.raises(RuntimeError, match="busy"):
        sbss._copy(Busy(id="r1"))
    assert sleeps == [0.01] * 20
