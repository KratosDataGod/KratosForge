"""A full or read-only profile disk, as on a shared or virtual desktop, is said plainly.

On Azure Virtual Desktop or Citrix the app's data folder sits on a profile disk (FSLogix or a
roaming profile) that can fill up or be attached read-only. The engine then says what happened
and what to do, instead of "OSError: [Errno 28]" or ending at start with no explanation.
"""

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

from fabric_desktop import disk_full, server


def test_a_full_disk_says_so_and_what_to_do(tmp_path):
    exc = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "settings.json"))
    text = disk_full.data_folder_message(tmp_path, exc)
    assert text and text.startswith("The disk is full") and "Free some space" in text


def test_a_message_that_already_says_the_disk_is_full_is_left_alone(tmp_path):
    assert disk_full.data_folder_message(tmp_path, OSError(disk_full.message(tmp_path, "Nothing changed."))) is None


def test_a_refused_write_counts_only_inside_the_data_folder(tmp_path):
    inside = PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "notebooks" / "a.ipynb"))
    text = disk_full.data_folder_message(tmp_path, inside)
    assert text and "data folder" in text and str(tmp_path) in text
    read_only = OSError(errno.EROFS, "Read-only file system", str(tmp_path / "app-settings.json"))
    assert disk_full.data_folder_message(tmp_path, read_only)
    elsewhere = PermissionError(errno.EACCES, "Permission denied", str(tmp_path.parent / "Downloads" / "x.csv"))
    assert disk_full.data_folder_message(tmp_path, elsewhere) is None  # an export's folder, not the profile's
    assert disk_full.data_folder_message(tmp_path, ValueError("bad name")) is None


def test_a_windows_access_denied_or_write_protect_code_counts(tmp_path):
    for code in (5, 19):
        exc = OSError(None, "refused", str(tmp_path / "x"))
        exc.winerror = code  # type: ignore[attr-defined]
        assert disk_full.is_unwritable(exc)


def test_an_rpc_call_that_hits_a_full_disk_gets_the_line(monkeypatch, tmp_path):
    class Full:
        def environment(self):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    reply = server.handle(Full(), json.dumps({"id": 1, "method": "environment", "params": {}}))  # type: ignore[arg-type]
    assert reply["error"].startswith("The disk is full")
    assert "Errno 28" in reply["error"]  # the original stays for support, after the line


def test_an_engine_that_cant_write_its_data_folder_answers_instead_of_ending(tmp_path):
    # LOCALAPPDATA\FabricDesktop is a file here, so nothing can be written under it: it stands in
    # for a full or read-only profile disk, which can't be made portably (CI may run as root).
    (tmp_path / "FabricDesktop").write_text("not a folder")
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "PYTHONPATH": str(Path(server.__file__).parents[1])}
    proc = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", "serve"], env=env, capture_output=True,
                          text=True, timeout=120, input=json.dumps({"id": 1, "method": "environment", "params": {}}) + "\n")
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert replies and replies[0]["id"] == 1
    assert "can't write to its data folder" in replies[0]["error"]
    assert "Traceback" not in proc.stderr
