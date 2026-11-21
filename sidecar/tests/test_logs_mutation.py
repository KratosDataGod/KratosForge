"""Agent JSON logs and the native crash report: an object written as text is cleaned too, the crash log's
size limit at its edge, its folder made with its parents, and every thread traced. Mutation batch 58."""

from __future__ import annotations

import faulthandler
import logging

from fabric_desktop import logs

SECRET = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlLXZhbHVl"  # pragma: allowlist secret (a made-up token shape)


class _Holder:
    def __str__(self) -> str:
        return f"Authorization: Bearer {SECRET}"


def test_an_object_written_as_text_is_cleaned_too():
    record = logging.LogRecord("fabric_desktop.agent", logging.INFO, __file__, 1, "run", None, None)
    record.fields = {"obj": _Holder()}
    written = logs.JsonFormatter("w").format(record)
    assert SECRET not in written and "Authorization" in written


def test_the_crash_log_is_set_aside_past_one_mebibyte():
    assert logs.CRASH_LOG_BYTES == 1048576


def _quiet_faulthandler(monkeypatch, seen=None):
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable",
                        lambda file, all_threads: (seen if seen is not None else []).append(all_threads))
    monkeypatch.setattr(logs, "_crash_file", None)


def test_a_crash_log_of_exactly_the_limit_is_kept(tmp_path, monkeypatch):
    _quiet_faulthandler(monkeypatch)
    monkeypatch.setattr(logs, "CRASH_LOG_BYTES", 10)
    (tmp_path / logs.CRASH_LOG).write_bytes(b"x" * 10)
    assert logs.report_native_crashes(tmp_path)
    logs._crash_file.close()
    assert not (tmp_path / "agent-crash.prev.log").exists()
    assert (tmp_path / logs.CRASH_LOG).read_bytes().startswith(b"x" * 10)


def test_the_crash_log_folder_is_made_with_its_parents_and_every_thread_is_traced(tmp_path, monkeypatch):
    seen: list[bool] = []
    _quiet_faulthandler(monkeypatch, seen)
    folder = tmp_path / "a" / "b" / "logs"
    assert logs.report_native_crashes(folder)
    logs._crash_file.close()
    assert (folder / logs.CRASH_LOG).is_file()
    assert seen == [True]
