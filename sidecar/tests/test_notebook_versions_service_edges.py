"""Version history service: the refusals and the failures that must never fail a save. Files on this PC only."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import notebook_versions as versions
from fabric_desktop import notebook_versions_service as nvs
from fabric_desktop.meta import release
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(client_id="x", root=tmp_path)
    s.save_notebook("NB", [{"type": "code", "source": "a = 1"}])
    return s


@pytest.fixture
def warnings(monkeypatch):
    logged = []
    monkeypatch.setattr(nvs._log, "warning", lambda *a, **k: logged.append(a[0] % a[1:]))
    return logged


def test_a_notebook_that_isnt_saved_has_no_versions(svc):
    with pytest.raises(ValueError, match="There's no notebook named 'Gone'"):
        svc.notebook_versions("Gone")


def test_keeping_both_sides_logs_each_failure_and_never_raises(svc, monkeypatch, warnings):
    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(versions, "record", broken)
    assert svc._keep_both_sides("NB", [{"type": "code", "source": "b = 2"}], None, None, None, False) is None
    assert len(warnings) == 2
    assert "Couldn't keep the outside version of 'NB'" in warnings[0]
    assert "Couldn't keep the unsaved cells of 'NB'" in warnings[1]


def test_an_unreadable_file_is_kept_as_a_byte_copy_and_its_path_returned(svc, monkeypatch, tmp_path):
    kept = tmp_path / "copy.bin"
    monkeypatch.setattr(versions, "keep_copy", lambda root, name, path: kept)
    got = svc._keep_both_sides("NB", [{"type": "code", "source": "b = 2"}], None, "spark", None, True)
    assert got == str(kept)
    assert [v["kind"] for v in svc.notebook_versions("NB")["versions"]].count("auto") >= 1


def test_compare_with_the_last_commit_reads_the_committed_file(svc, monkeypatch):
    committed = svc._saved_nb("NB")
    monkeypatch.setattr(release, "committed_file", lambda path: json.dumps(committed))
    got = svc.notebook_version_compare("NB", cells=[{"type": "code", "source": "a = 2"}], against="head")
    assert got["against"] == "head" and got["problem"] is None and got["changes"]


def test_compare_against_anything_but_a_version_or_head_is_refused(svc):
    with pytest.raises(ValueError, match='against is "version" or "head"'):
        svc.notebook_version_compare("NB", "v1", against="yesterday")
