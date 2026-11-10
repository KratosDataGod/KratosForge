"""A native crash in the headless agent leaves a traceback in agent-crash.log, and nothing is sent."""

import faulthandler
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fabric_desktop import logs

SIDECAR = Path(__file__).resolve().parents[1]

CRASHES = textwrap.dedent("""
    import faulthandler, sys
    from pathlib import Path
    from fabric_desktop import logs
    assert logs.report_native_crashes(Path(sys.argv[1]))
    faulthandler._sigsegv()
""")


@pytest.mark.skipif(not hasattr(faulthandler, "_sigsegv"), reason="needs faulthandler._sigsegv")
def test_a_native_crash_leaves_a_traceback_in_the_agents_crash_log(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(SIDECAR)}
    env.pop("PYTHONFAULTHANDLER", None)  # the agent turns it on itself; its scheduled task doesn't set this
    done = subprocess.run([sys.executable, "-c", CRASHES, str(tmp_path / "logs")], stdin=subprocess.DEVNULL,
                          capture_output=True, env=env, timeout=120)
    assert done.returncode != 0
    text = (tmp_path / "logs" / logs.CRASH_LOG).read_text(errors="replace")
    assert "Fatal Python error" in text or "fatal exception" in text, text
    assert "File " in text and "<string>" in text, text  # where it happened
    start = text.index(logs.START_LINE)
    assert start < text.index("File "), text  # the start line comes first, so a trace after it is this run's


def test_a_big_crash_log_is_set_aside_at_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    turned_on = []
    monkeypatch.setattr(faulthandler, "enable", lambda file, all_threads: turned_on.append(file.name))
    monkeypatch.setattr(logs, "_crash_file", None)
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / logs.CRASH_LOG).write_bytes(b"x" * (logs.CRASH_LOG_BYTES + 1))
    assert logs.report_native_crashes(folder)
    assert (folder / "agent-crash.prev.log").stat().st_size == logs.CRASH_LOG_BYTES + 1
    assert turned_on == [str(folder / logs.CRASH_LOG)]
    [line] = (folder / logs.CRASH_LOG).read_text(encoding="utf-8").splitlines()
    assert logs.START_LINE in line and f"pid {os.getpid()}" in line
    logs._crash_file.close()


def test_each_start_adds_one_line_and_keeps_what_was_there(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", lambda file, all_threads: None)
    monkeypatch.setattr(logs, "_crash_file", None)
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / logs.CRASH_LOG).write_text("Windows fatal exception: access violation\n", encoding="utf-8")
    assert logs.report_native_crashes(folder) and logs.report_native_crashes(folder)
    lines = (folder / logs.CRASH_LOG).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Windows fatal exception") and [logs.START_LINE in x for x in lines[1:]] == [True, True]
    logs._crash_file.close()


def test_a_folder_that_cant_be_written_still_starts_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    (tmp_path / "logs").write_text("a file where the folder would be", encoding="utf-8")
    assert logs.report_native_crashes(tmp_path / "logs") is False


def test_faulthandler_already_on_is_left_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: True)  # PYTHONFAULTHANDLER, or pytest itself
    assert logs.report_native_crashes(tmp_path / "logs") is False
    assert not (tmp_path / "logs").exists()


def test_agent_run_turns_crash_reports_on_and_they_are_never_sent():
    source = (SIDECAR / "fabric_desktop" / "cli.py").read_text(encoding="utf-8")
    start = source.index("def _agent(")
    assert "logs.report_native_crashes(config.home / \"logs\")" in source[start:source.index("\ndef ", start + 1)]
    body = Path(logs.__file__).read_text(encoding="utf-8").split("def report_native_crashes")[1]
    for network in ("http", "urlopen", "requests", "socket", "telemetry"):
        assert network not in body, network
