"""Credentials stay out of the engine log on disk, and an agent key file anyone can read is flagged.

The support bundle already redacts sidecar.log on its way out; these checks are about the file the PC keeps.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fabric_desktop import credentials, server

SIDECAR = Path(__file__).resolve().parents[1]
# Built from parts so no scanner takes the test for a leak.
JWT = "eyJ" + "hbGciOiJSUzI1NiJ9" + "." + "eyJ" + "zdWIiOiJhYmMifQ" + "." + "c2lnbmF0dXJlLXZhbHVl"
URL = "https://" + "someone:" + "hunter2pass" + "@dev.azure.com/org/_git/repo"


def test_the_redacted_stream_removes_credentials_and_keeps_the_rest():
    raw = io.StringIO()
    stream = server._RedactedStream(raw)
    assert stream.write(f"401 Bearer {JWT}\n") == len(f"401 Bearer {JWT}\n")
    stream.write(f"fetch {URL} failed\n")
    stream.write("plain line\n")
    stream.flush()
    text = raw.getvalue()
    assert JWT not in text and "hunter2pass" not in text
    assert "plain line" in text and "dev.azure.com/org/_git/repo" in text


_SERVE = textwrap.dedent("""
    import sys, threading, warnings
    from fabric_desktop import server
    jwt, url = sys.argv[1], sys.argv[2]
    def serve(service, stdin, stdout):
        print(f"print {url}")
        warnings.warn(f"warning Bearer {jwt}")
        sys.stderr.write(f"stderr Bearer {jwt}\\n")
        t = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError(f"thread {url}")))
        t.start(); t.join()
    server.serve = serve
    server.Service = lambda: type("S", (), {"policy_record": lambda self: None})()
    server._start_integration_http = lambda service: None
    server.main()
""")


def test_the_engine_log_gets_no_raw_credentials(tmp_path):
    home = str(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(SIDECAR), "FABRIC_DESKTOP_HOME": home, "HOME": home,
           "USERPROFILE": home, "LOCALAPPDATA": home, "APPDATA": home}
    done = subprocess.run([sys.executable, "-c", _SERVE, JWT, URL], capture_output=True, text=True, env=env,
                          timeout=120, check=False, stdin=subprocess.DEVNULL)
    log = done.stderr
    assert done.returncode == 0, log[-2000:]
    for seen in ("print ", "warning Bearer", "stderr Bearer", "RuntimeError: thread"):
        assert seen in log, (seen, log[-2000:])
    assert JWT not in log and "hunter2pass" not in log, log[-2000:]
    assert done.stdout == ""  # fd 1 still goes to the log, never to the protocol


@pytest.mark.skipif(os.name == "nt", reason="Windows ACLs aren't file modes")
def test_an_agent_key_file_every_account_can_read_is_flagged(tmp_path):
    key = tmp_path / "agent.pem"
    key.write_text("key")
    auth = {"type": "service_principal", "certificate_path": str(key)}
    key.chmod(0o640)
    assert not credentials.key_file_open(auth)
    key.chmod(0o644)
    assert credentials.key_file_open(auth)
    assert not credentials.key_file_open({**auth, "type": "managed_identity"})
    assert not credentials.key_file_open({**auth, "certificate_path": str(tmp_path / "missing.pem")})
    assert not credentials.key_file_open({"type": "service_principal", "client_secret_env": "X"})
