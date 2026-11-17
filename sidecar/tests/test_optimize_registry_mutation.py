"""Mutation batch 48: optimize/registry.py (docs/quality/mutation-testing.md).

Pins what the setup and service tests left open: a connection needs both a workspace and a
lakehouse, connecting again keeps that lakehouse's own saved fields (and no other's), forgetting
the active lakehouse makes the newest remaining one active, and the file's layout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop.optimize.registry import Registry


def _conn(n: int, **kw) -> dict:
    return {"workspace_id": f"ws{n}", "workspace_name": f"WS {n}", "lakehouse_id": f"lh{n}",
            "lakehouse_name": f"LH {n}"} | kw


@pytest.mark.parametrize("missing", ["workspace_id", "lakehouse_id"])
def test_a_connection_needs_a_workspace_and_a_lakehouse(tmp_path: Path, missing: str):
    reg = Registry(tmp_path)
    with pytest.raises(ValueError, match="needs a workspace and a lakehouse"):
        reg.add(_conn(1) | {missing: ""})
    assert reg.all() == [] and not (tmp_path / "connections.json").exists()


def test_connecting_again_keeps_its_own_check_and_not_anothers(tmp_path: Path):
    reg = Registry(tmp_path)
    reg.add(_conn(1, access="admin", checked_at="2026-01-01T00:00:00Z"))
    reg.add(_conn(2, access="viewer", checked_at="2026-02-02T00:00:00Z", schema_version=3))
    again = reg.add(_conn(1, lakehouse_name="Renamed"))
    assert again["lakehouse_name"] == "Renamed"
    assert again["access"] == "admin" and again["checked_at"] == "2026-01-01T00:00:00Z"
    assert "schema_version" not in again
    assert [c["lakehouse_id"] for c in reg.all()] == ["lh2", "lh1"]


def test_forgetting_the_active_one_activates_the_newest_left(tmp_path: Path):
    reg = Registry(tmp_path)
    for n in (1, 2, 3):
        reg.add(_conn(n), activate=(n == 1))
    assert reg.active()["lakehouse_id"] == "lh1"
    reg.forget("lh1")
    assert reg.active()["lakehouse_id"] == "lh3"
    reg.forget("lh3")
    assert reg.active()["lakehouse_id"] == "lh2"
    reg.forget("lh2")
    assert reg.active() is None


def test_the_file_is_written_with_one_space_indents(tmp_path: Path):
    reg = Registry(tmp_path)
    reg.add(_conn(1))
    text = (tmp_path / "connections.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert text.replace("\r\n", "\n") == json.dumps(data, indent=1)
