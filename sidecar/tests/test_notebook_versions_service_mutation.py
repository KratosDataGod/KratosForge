"""Mutation-testing gaps in notebook_versions_service.py: labels read back, the compute kept with
unsaved cells, and what a delete returns. Files on this PC only (tmp_path)."""

from __future__ import annotations

import pytest

from fabric_desktop import notebook_versions as versions
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = Service(client_id="x", root=tmp_path)
    s.save_notebook("NB", [{"type": "code", "source": "a = 1"}])
    return s


def _ids(svc, kind):
    return [v["id"] for v in svc.notebook_versions("NB")["versions"] if v["kind"] == kind]


def test_a_version_reads_back_its_label_or_an_empty_one(svc):
    svc.notebook_checkpoint("NB", "before the change")
    [cp] = _ids(svc, "checkpoint")
    got = svc.notebook_version("NB", cp)
    assert got["label"] == "before the change" and got["kind"] == "checkpoint" and got["id"] == cp
    [auto] = _ids(svc, "auto")
    assert svc.notebook_version("NB", auto)["label"] == ""


def test_a_compare_reads_back_its_label_or_an_empty_one(svc):
    svc.notebook_checkpoint("NB", "named")
    [cp] = _ids(svc, "checkpoint")
    [auto] = _ids(svc, "auto")
    assert svc.notebook_version_compare("NB", cp)["label"] == "named"
    assert svc.notebook_version_compare("NB", auto)["label"] == ""


@pytest.mark.parametrize("compute, kept", [(None, None), ("default", None), ("spark", "spark")])
def test_unsaved_cells_keep_their_compute_and_none_means_the_default(svc, monkeypatch, compute, kept):
    recorded = []
    monkeypatch.setattr(versions, "record", lambda root, name, nb, kind="auto", *a: recorded.append((kind, nb)))
    svc._keep_both_sides("NB", [{"type": "code", "source": "b = 2"}], None, compute, None, False)
    nb = dict(recorded)["not saved"]
    assert nb.get("metadata", {}).get("fabric_desktop", {}).get("compute") == kept
    if kept is None:
        assert "fabric_desktop" not in nb["metadata"]


def test_a_delete_names_the_version_it_deleted(svc):
    svc.notebook_checkpoint("NB", "x")
    [cp] = _ids(svc, "checkpoint")
    assert svc.notebook_version_delete("NB", cp) == {"deleted": cp}
    assert _ids(svc, "checkpoint") == []
