"""shortcut_service.py at its exact edges (mutation batch 165, a boundary re-score of batch 15,
docs/quality/mutation-testing.md): repointing one shortcut has no note about others, and a shortcut
added at exactly the request's time is the request's own."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import shortcut_service
from fabric_desktop.service import Service


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def _repointed(tmp_path, monkeypatch, n):
    svc = Service(client_id="x", root=tmp_path / "app")
    notes = []

    def repoint(*args):
        notes.append(args[-1])
        return {}

    monkeypatch.setattr(svc, "shortcut_repoint", repoint)
    items = [{"host_workspace_id": "w", "lakehouse_id": "l", "path": "Tables", "name": f"s{i}"} for i in range(n)]
    svc.shortcut_repoint_many(items, "ws2", "lh2", lakehouse_name="Gold")
    return notes


def test_one_shortcut_repointed_has_no_note_and_two_name_the_other(tmp_path, monkeypatch):
    assert _repointed(tmp_path, monkeypatch, 1) == [""]
    assert _repointed(tmp_path, monkeypatch, 2) == ["Repointed with 1 more at Gold"] * 2


def test_a_shortcut_added_at_exactly_the_requests_time_is_its_own():
    at = "2026-11-18T16:01:58+00:00"
    assert shortcut_service._added_since([{"kind": "added", "at": at}], at) is True
    assert shortcut_service._added_since([{"kind": "added", "at": "2026-11-18T16:01:57+00:00"}], at) is False
