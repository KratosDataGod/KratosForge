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

    monkeypatch.setattr(jsonfile.os, "replace", busy_twice)
    monkeypatch.setattr(jsonfile, "_REPLACE_WAIT", 0)
    jsonfile.write_json(tmp_path / "s.json", {"a": 1})
    assert len(calls) == 3 and json.loads((tmp_path / "s.json").read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_replace_gives_up_after_bounded_retries(tmp_path, monkeypatch):
    def always_busy(src, dst):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(jsonfile.os, "replace", always_busy)
    monkeypatch.setattr(jsonfile, "_REPLACE_WAIT", 0)
    with pytest.raises(PermissionError):
        jsonfile.write_json(tmp_path / "s.json", {"a": 1})
    assert list(tmp_path.iterdir()) == []
