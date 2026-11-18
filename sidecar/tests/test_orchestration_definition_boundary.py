"""Boundary kill tests for orchestration/definition (mutation testing, batch 166): a definition of
exactly MAX_BYTES is still read; only a larger one is refused."""
from __future__ import annotations

import json

from fabric_desktop.orchestration import definition, load, loads

CONTENT = json.dumps({"properties": {"activities": [{"name": "Wait1", "type": "Wait",
                                                     "typeProperties": {"waitTimeInSeconds": 1}}]}})


def test_text_of_exactly_the_limit_is_read(monkeypatch):
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in loads(CONTENT).pipeline.activities] == ["Wait1"]


def test_a_file_of_exactly_the_limit_is_read(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    f.write_bytes(CONTENT.encode())
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in load(f).pipeline.activities] == ["Wait1"]


def test_a_git_folder_part_of_exactly_the_limit_is_read(tmp_path, monkeypatch):
    d = tmp_path / "Load.DataPipeline"
    d.mkdir()
    (d / definition.CONTENT_PART).write_bytes(CONTENT.encode())
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in load(d).pipeline.activities] == ["Wait1"]
