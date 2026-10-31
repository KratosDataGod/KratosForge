"""Errors over the RPC write the home folder as ~ (#1173 follow-up): a pasted error or a screenshot
shouldn't carry the Windows account name. Results keep full paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import server
from fabric_desktop.service import Service


@pytest.mark.parametrize("home, message, shown", [
    ("/home/chris", "No such file: '/home/chris/app/notebooks/7.ipynb'", "No such file: '~/app/notebooks/7.ipynb'"),
    ("/home/chris", "see /home/chris/app/http.log)", "see ~/app/http.log)"),
    ("/home/chris", "/home/chrisother/x stays", "/home/chrisother/x stays"),
    ("C:\\Users\\chris", "[Errno 2] No such file: 'C:\\\\Users\\\\chris\\\\AppData\\\\x.json'",
     "[Errno 2] No such file: '~\\\\AppData\\\\x.json'"),
    ("C:\\Users\\chris", "C:\\Users\\Chris\\Documents\\crm isn't a project", "~\\Documents\\crm isn't a project"),
    ("/", "/etc/x", "/etc/x"),
])
def test_the_home_folder_is_written_as_a_tilde(monkeypatch, home, message, shown):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(home)) if "\\" not in home else
                        classmethod(lambda cls: _Win(home)))
    assert server._home(message) == shown


class _Win:
    """Enough of a Windows path for _home on any machine."""

    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text

    def as_posix(self):
        return self.text.replace("\\", "/")


def test_an_rpc_error_carries_no_home_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    svc = Service(root=tmp_path / "app")
    reply = server.handle(svc, json.dumps({"id": 1, "method": "meta_open", "params": {"path": str(tmp_path / "nope")}}))
    assert "error" in reply and str(tmp_path) not in reply["error"] and "~" in reply["error"], reply
