"""agent_start.record paths that mutation testing (batch 71, docs/quality/mutation-testing.md) showed no
test pinned: the size limit, the reason's length, which stops list their frames, how many frames, and
where the trim starts."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import agent_start
from fabric_desktop.agent_start import START_LOG


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_the_start_record_is_kept_under_256_kb():
    assert agent_start.MAX_BYTES == 256 * 1024


def test_a_text_reason_is_cut_to_2000_characters(tmp_path):
    path = agent_start.record(tmp_path, "start", "a" * 1999 + "b", 2)
    assert _lines(path)[-1] == "  " + "a" * 1999 + "b"   # 2000 fits whole
    path = agent_start.record(tmp_path, "start", "c" * 2001, 2)
    assert _lines(path)[-1] == "  " + "c" * 1999 + "…"


def test_a_crash_given_as_text_has_no_frames(tmp_path):
    path = agent_start.record(tmp_path, "crash", "it stopped", 1)
    assert [line for line in _lines(path) if line.startswith("  at ")] == []
    assert _lines(path)[-1] == "  it stopped"


def test_only_a_crash_lists_its_frames(tmp_path):
    try:
        raise RuntimeError("no store")
    except RuntimeError as exc:
        error = exc
    path = agent_start.record(tmp_path / "a", "start", error, 2)
    assert not any(line.startswith("  at ") for line in _lines(path))
    path = agent_start.record(tmp_path / "b", "crash", error, 1)
    assert [line for line in _lines(path) if line.startswith("  at ")][-1].endswith(
        " in test_only_a_crash_lists_its_frames")


def _deep(n: int):
    if n == 0:
        raise ValueError("deep")
    _deep(n - 1)


def test_a_crash_lists_its_last_twelve_frames(tmp_path):
    try:
        _deep(20)
    except ValueError as exc:
        error = exc
    path = agent_start.record(tmp_path, "crash", error, 1)
    frames = [line for line in _lines(path) if line.startswith("  at ")]
    assert len(frames) == 12 and all(line.endswith(" in _deep") for line in frames)


def _entry_size(tmp_path: Path) -> int:
    probe = agent_start.record(tmp_path / "probe", "start", "in use", 2)
    return len(probe.read_bytes())


def test_a_record_that_just_fits_is_added_and_one_byte_more_trims(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_start, "MAX_BYTES", 400)
    entry = _entry_size(tmp_path)
    log = tmp_path / "logs" / START_LOG
    log.parent.mkdir(parents=True)
    old = b"o" * (400 - entry - 1) + b"\n"
    log.write_bytes(old)
    agent_start.record(tmp_path, "start", "in use", 2)
    data = log.read_bytes()
    assert len(data) == 400 and data.startswith(old)   # exactly at the limit: appended, nothing dropped
    log.write_bytes(b"o" + old)
    agent_start.record(tmp_path, "start", "in use", 2)
    assert not log.read_bytes().startswith(b"o")       # one byte over: the old half goes


def test_the_trim_drops_the_older_half_and_starts_on_the_next_whole_line(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_start, "MAX_BYTES", 300)
    log = tmp_path / "logs" / START_LOG
    log.parent.mkdir(parents=True)
    old = b"".join(b"line%05d\n" % i for i in range(30))   # 30 lines of 10 bytes
    log.write_bytes(old)
    entry = _entry_size(tmp_path)
    agent_start.record(tmp_path, "start", "in use", 2)
    data = log.read_bytes()
    # Half is byte 150, inside line 15; the record starts at line 16, the first whole line after it.
    kept = b"".join(b"line%05d\n" % i for i in range(16, 30))
    assert data.startswith(kept) and len(data) == len(kept) + entry and data.endswith(b"  in use\n")
