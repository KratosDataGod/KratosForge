"""This PC's schedule locks at their boundaries (mutation batch 206, a boundary re-score of batch 12,
docs/quality/mutation-testing.md): a PID of 0 is never asked about, a takeover guard exactly a minute old
is still someone deciding, a lock exactly STALE_LOCK_SECONDS old with a live run is kept, and a stopped
run's start time of exactly 40 characters is kept."""

from __future__ import annotations

import os
import sys

import pytest

from fabric_desktop import schedule_service as ss
from test_schedule_locks_and_lists import lock_file, svc  # noqa: F401 - svc is a fixture


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill(pid, 0) is the POSIX check; Windows asks OpenProcess")
def test_a_pid_of_zero_counts_as_running_without_asking_the_system(monkeypatch):
    asked = []

    def kill(pid, sig):
        asked.append(pid)
        raise ProcessLookupError(3, "No such process")
    monkeypatch.setattr(ss.os, "kill", kill)
    monkeypatch.setattr(ss, "process_identity", lambda pid: None)
    assert ss._pid_alive(0) is True and asked == []
    assert ss._pid_alive(999999) is False and asked == [999999]  # the stand-in answers "ended"


def test_a_takeover_guard_exactly_a_minute_old_is_still_someone_deciding(svc, monkeypatch):  # noqa: F811
    path = lock_file(svc, svc.sid, {"pid": 999999999, "schedule_id": svc.sid})
    guard = path.with_name(path.name + ".takeover")
    guard.write_text("")
    at = guard.stat().st_mtime
    monkeypatch.setattr(ss.time, "time", lambda: at + 60)
    assert svc._take_over(svc.sid, path) == (False, False)
    assert path.exists() and guard.exists()


def test_a_live_runs_lock_exactly_stale_lock_seconds_old_is_kept(svc, monkeypatch):  # noqa: F811
    path = lock_file(svc, svc.sid, {"pid": os.getpid(), "process": ss.process_identity(os.getpid())})
    at = path.stat().st_mtime
    monkeypatch.setattr(ss.time, "time", lambda: at + ss.STALE_LOCK_SECONDS)
    assert svc._take_over(svc.sid, path) == (False, False) and path.exists()


@pytest.mark.parametrize("length, kept", [(40, True), (41, False)])
def test_a_stopped_runs_start_time_of_up_to_40_characters_is_kept(svc, length, kept):  # noqa: F811
    started = "2026-11-07T01:00:00+00:00".ljust(length, "0")
    path = lock_file(svc, svc.sid, {"pid": 999999999, "schedule_id": svc.sid, "started": started})
    assert svc._take_over(svc.sid, path) == (True, True)
    [row] = svc.pc_schedule_runs()
    assert row["started"] == (started if kept else None)
