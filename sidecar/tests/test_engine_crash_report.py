"""A native crash in the engine leaves a traceback in its log, and the log is all it leaves: nothing is sent."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fabric_desktop import server

SIDECAR = Path(__file__).resolve().parents[1]

# The engine as the app starts it (stderr to sidecar.log), cut down to its start: the stores and the local
# API are left out, and the request loop is replaced by a native crash.
CRASHES = textwrap.dedent("""
    import faulthandler
    from fabric_desktop import server
    class Engine:
        def policy_record(self):
            pass
    server.Service = Engine
    server._start_integration_http = lambda service: None
    server._refresh_integration_launcher = lambda: None
    def crash(*args, **kwargs):
        print("engine: about to crash", flush=True)
        faulthandler._sigsegv()
    server.serve = crash
    server.main()
""")


@pytest.mark.skipif(not hasattr(__import__("faulthandler"), "_sigsegv"), reason="needs faulthandler._sigsegv")
def test_a_native_crash_leaves_a_traceback_in_the_engines_log(tmp_path):
    log = tmp_path / "sidecar.log"
    env = {**os.environ, "PYTHONPATH": str(SIDECAR), "LOCALAPPDATA": str(tmp_path / "la"), "HOME": str(tmp_path)}
    env.pop("PYTHONFAULTHANDLER", None)  # the engine turns it on itself; the app doesn't set this
    with log.open("w") as stderr:
        done = subprocess.run([sys.executable, "-c", CRASHES], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=stderr, env=env, timeout=120)
    assert done.returncode != 0
    text = log.read_text(errors="replace")
    assert "engine: about to crash" in text
    assert "Fatal Python error" in text or "fatal exception" in text, text
    assert "File " in text and "crash" in text, text  # where it happened, by file and function


def test_a_log_without_a_real_file_still_starts_the_engine(capsys):
    import io

    class NoFile(io.StringIO):
        def fileno(self):
            raise io.UnsupportedOperation("no file")

    stream = NoFile()
    server._report_native_crashes(stream)
    assert "native crashes won't be reported" in stream.getvalue()


def test_crash_reports_are_never_sent_anywhere():
    """The shell's crash handling and the engine's only write files on this PC."""
    crash_rs = (SIDECAR.parent / "app" / "src-tauri" / "src" / "crash.rs").read_text(encoding="utf-8").split("#[cfg(test)]")[0]
    for network in ("http", "TcpStream", "reqwest", "ureq", "UdpSocket"):
        assert network not in crash_rs, network
    source = Path(server.__file__).read_text(encoding="utf-8")
    start = source.index("def _report_native_crashes")
    body = source[start:source.index("\ndef ", start + 1)]
    for network in ("http", "urlopen", "requests", "socket", "telemetry"):
        assert network not in body, network
