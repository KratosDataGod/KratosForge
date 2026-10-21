"""QA-011: the agent's settings.json and the PC's schedules.json are written atomically, and a torn
file is set aside instead of being silently overwritten by the next save."""

import json
import logging
import os
from pathlib import Path

import pytest

from fabric_desktop import agent_config, jsonfile
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.meta.templates import init_project
from fabric_desktop.runtime import Runtime

SETTINGS = {"platform": {"workspace_id": "ws-1"}, "costs": {"currency": "USD"}, "alerts": {"on_failure": True}}
SCHEDULES = {"engine": "C:/engine/pythonw.exe", "schedules": [
    {"id": "abc123", "target": {"kind": "notebook", "name": "n"}, "config": {}, "enabled": True, "created": "x"}]}


class FakeScheduler:
    available = True

    def __init__(self):
        self.tasks = {}

    def register(self, schedule, executable, workdir):
        self.tasks[schedule["id"]] = schedule["enabled"]

    def unregister(self, schedule_id):
        self.tasks.pop(schedule_id, None)


@pytest.fixture
def runtime(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(f"""
[agent]
name = "etl-01"
home = {json.dumps(str(tmp_path / "home"))}
project = {json.dumps(str(project))}
environments = ["dev"]

[auth]
type = "user"
""", encoding="utf-8")
    return Runtime(agent_config.load(path))


@pytest.fixture
def schedules(tmp_path):
    return LocalSchedules(tmp_path / "app", FakeScheduler(), "C:/engine/python.exe")


def torn(path: Path, data) -> bytes:
    text = json.dumps(data, indent=2).encode("utf-8")
    half = text[: len(text) // 2]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(half)
    return half


def set_aside_files(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


def test_torn_agent_settings_are_set_aside_not_overwritten(runtime, caplog):
    half = torn(runtime.settings_file, SETTINGS)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.jsonfile"):
        runtime._save_settings(last_seen="2026-10-20")
    aside = set_aside_files(runtime.settings_file)
    assert len(aside) == 1 and aside[0].read_bytes() == half
    assert json.loads(runtime.settings_file.read_text(encoding="utf-8")) == {"last_seen": "2026-10-20"}
    assert "moved to" in caplog.text and aside[0].name in caplog.text
    runtime._save_settings(other=1)  # the new file is fine, so nothing more is set aside
    assert len(set_aside_files(runtime.settings_file)) == 1


def test_torn_schedules_are_set_aside_not_overwritten(schedules, caplog):
    half = torn(schedules.file, SCHEDULES)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.jsonfile"):
        item = schedules.add({"kind": "notebook", "name": "n2"}, {"kind": "cron", "every_minutes": 15})
    aside = set_aside_files(schedules.file)
    assert len(aside) == 1 and aside[0].read_bytes() == half
    saved = json.loads(schedules.file.read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["schedules"]] == [item["id"]]
    assert "moved to" in caplog.text


@pytest.fixture
def write_fails_midway(monkeypatch):
    """The disk fills up halfway through writing, whichever way the file is written."""
    def half_write_text(self, data, encoding=None, errors=None, newline=None):
        with open(self, "w", encoding=encoding) as f:
            f.write(data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    real_fdopen = os.fdopen

    class HalfWriter:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, data):
            self.f.write(data[: len(data) // 2])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", half_write_text)
    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: HalfWriter(real_fdopen(fd, *a, **k)))


def test_a_failed_settings_write_leaves_the_old_file(runtime, write_fails_midway):
    runtime.settings_file.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(SETTINGS, indent=2).encode("utf-8")
    runtime.settings_file.write_bytes(original)
    with pytest.raises(OSError):
        runtime._save_settings(last_seen="2026-10-20")
    assert runtime.settings_file.read_bytes() == original
    assert [p.name for p in runtime.settings_file.parent.iterdir()] == ["settings.json"]  # no temp file left


def test_a_failed_schedules_write_leaves_the_old_file(schedules, write_fails_midway):
    schedules.root.mkdir(parents=True, exist_ok=True)
    original = json.dumps(SCHEDULES, indent=1).encode("utf-8")
    schedules.file.write_bytes(original)
    with pytest.raises(OSError):
        schedules.set_enabled("abc123", False)
    assert schedules.file.read_bytes() == original
    assert [p.name for p in schedules.root.iterdir()] == ["schedules.json"]


def test_replace_waits_out_a_reader_holding_the_file(tmp_path, monkeypatch):
    real_replace, calls = os.replace, []

    def busy_twice(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    waits = []
    monkeypatch.setattr(jsonfile.os, "replace", busy_twice)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    jsonfile.write_json(tmp_path / "s.json", {"a": 1})
    assert waits == [0.05, 0.1]
    assert len(calls) == 3 and json.loads((tmp_path / "s.json").read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_replace_gives_up_after_bounded_retries(tmp_path, monkeypatch):
    def always_busy(src, dst):
        raise PermissionError(13, "The process cannot access the file")

    waits = []
    monkeypatch.setattr(jsonfile.os, "replace", always_busy)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        jsonfile.write_json(tmp_path / "s.json", {"a": 1})
    assert waits == [0.05, 0.1, 0.2, 0.4, 0.8]  # about 1.5 s, as retrying.local_retry
    assert list(tmp_path.iterdir()) == []


# ---- slice 2: lakehouse favorites, workspace marks and saved side-by-side comparisons

@pytest.fixture
def svc(tmp_path):
    from fabric_desktop.service import Service
    return Service(client_id="x", root=tmp_path / "app")


def test_torn_favorites_are_set_aside_not_overwritten(svc):
    half = torn(svc.favorites_file, ["lh1", "lh2", "lh3"])
    assert svc.set_favorite("lh9", True) == ["lh9"]
    aside = set_aside_files(svc.favorites_file)
    assert len(aside) == 1 and aside[0].read_bytes() == half
    assert json.loads(svc.favorites_file.read_text(encoding="utf-8")) == ["lh9"]


def test_torn_workspace_marks_are_set_aside_not_overwritten(svc):
    half = torn(svc.workspace_marks_file, {"favorites": ["w1"], "pins": ["w2", "w1"]})
    assert svc.set_workspace_mark("w3", "pins", True) == {"favorites": [], "pins": ["w3"]}
    aside = set_aside_files(svc.workspace_marks_file)
    assert len(aside) == 1 and aside[0].read_bytes() == half


def test_a_torn_comparison_line_keeps_the_others_and_the_original(svc):
    good = [{"id": "a", "local": {}}, {"id": "b", "local": {}}]
    text = "".join(json.dumps(r) + "\n" for r in good) + '{"id": "c", "loc'
    svc.side_by_side_file.parent.mkdir(parents=True, exist_ok=True)
    svc.side_by_side_file.write_text(text, encoding="utf-8")
    svc._sbs_save({"id": "d", "local": {"cells": [1], "rows": 2}})
    aside = set_aside_files(svc.side_by_side_file)
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == text
    assert [r["id"] for r in svc._sbs_saved()] == ["a", "b", "d"]
    assert svc._sbs_saved()[-1]["local"] == {"rows": 2}


def test_a_failed_comparison_save_leaves_the_old_file(svc, write_fails_midway):
    original = json.dumps({"id": "a", "local": {}}) + "\n"
    svc.side_by_side_file.parent.mkdir(parents=True, exist_ok=True)
    svc.side_by_side_file.write_bytes(original.encode("utf-8"))
    with pytest.raises(OSError):
        svc._sbs_save({"id": "b", "name": "a longer comparison", "local": {}})
    assert svc.side_by_side_file.read_text(encoding="utf-8") == original
    assert [p.name for p in svc.side_by_side_file.parent.iterdir()] == [svc.side_by_side_file.name]


def test_reading_a_torn_comparison_file_twice_keeps_the_good_rows(svc):
    text = json.dumps({"id": "a", "local": {}}) + "\n" + json.dumps({"id": "b", "local": {}}) + "\n" + '{"id": "c'
    svc.side_by_side_file.parent.mkdir(parents=True, exist_ok=True)
    svc.side_by_side_file.write_text(text, encoding="utf-8")
    first, second = svc.side_by_side_list(), svc.side_by_side_list()
    assert sorted(r["id"] for r in first) == sorted(r["id"] for r in second) == ["a", "b"]
    aside = set_aside_files(svc.side_by_side_file)
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == text


def test_a_failed_repair_still_shows_the_good_rows_on_every_read(svc, monkeypatch):
    text = json.dumps({"id": "a", "local": {}}) + "\n" + json.dumps({"id": "b", "local": {}}) + "\n" + '{"id": "c'
    svc.side_by_side_file.parent.mkdir(parents=True, exist_ok=True)
    svc.side_by_side_file.write_text(text, encoding="utf-8")

    calls = []

    def disk_full(path, data):
        calls.append(path)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jsonfile, "write_text", disk_full)
    first, second = svc.side_by_side_list(), svc.side_by_side_list()
    assert sorted(r["id"] for r in first) == sorted(r["id"] for r in second) == ["a", "b"]
    assert svc.side_by_side_file.read_text(encoding="utf-8") == text  # the original stays until a repair works
    assert [p.read_text(encoding="utf-8") for p in set_aside_files(svc.side_by_side_file)] == [text]  # one copy, not one per read
    assert calls == [svc.side_by_side_file]  # and one failed swap: later reads don't retry an unchanged file


def test_after_a_failed_repair_the_next_save_still_rewrites_the_file(svc, monkeypatch):
    text = json.dumps({"id": "a", "local": {}}) + "\n" + '{"id": "c'
    svc.side_by_side_file.parent.mkdir(parents=True, exist_ok=True)
    svc.side_by_side_file.write_text(text, encoding="utf-8")
    real = jsonfile.write_text

    def fail_once(path, data):
        monkeypatch.setattr(jsonfile, "write_text", real)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jsonfile, "write_text", fail_once)
    assert [r["id"] for r in svc.side_by_side_list()] == ["a"]
    svc._sbs_save({"id": "d", "local": {}})
    assert [json.loads(line)["id"] for line in svc.side_by_side_file.read_text(encoding="utf-8").splitlines()] == ["a", "d"]


def busy_reads(monkeypatch, path, times):
    """Path.read_text fails ``times`` times for ``path``, as on Windows while a writer swaps it in."""
    real, waits, failed = Path.read_text, [], []

    def read_text(self, *a, **k):
        if self == path and len(failed) < times:
            failed.append(self)
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    return waits


def test_read_waits_out_a_writer_swapping_the_file(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    jsonfile.write_json(path, {"a": 1})
    waits = busy_reads(monkeypatch, path, 2)
    assert jsonfile.read_json(path, None) == {"a": 1}
    assert waits == [0.05, 0.1]
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]  # not set aside as unreadable


def test_read_gives_up_after_bounded_retries(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    jsonfile.write_json(path, {"a": 1})
    waits = busy_reads(monkeypatch, path, 99)
    with pytest.raises(PermissionError):
        jsonfile.read_json(path, None)
    assert waits == [0.05, 0.1, 0.2, 0.4, 0.8]


def test_listing_comparisons_waits_out_a_comparison_being_saved(svc, monkeypatch):
    """Windows CI: side_by_side_list read the file while the run's thread was swapping in its save."""
    svc._sbs_save({"id": "a", "local": {}})
    busy_reads(monkeypatch, svc.side_by_side_file, 2)
    assert [r["id"] for r in svc.side_by_side_list()] == ["a"]


def test_comparisons_saved_at_once_are_all_kept(svc):
    import threading
    threads = [threading.Thread(target=svc._sbs_save, args=({"id": str(i), "local": {}},)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["id"] for r in svc._sbs_saved()) == [str(i) for i in range(8)]
