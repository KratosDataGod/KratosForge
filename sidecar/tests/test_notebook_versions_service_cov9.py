"""notebook_versions_service: the sync base kept with the screen's cell ids, and the failures that must never
fail an open, a save or a merge preview."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_versions_service as nvs

STAMP = "abcdef1234567890"


class Host(nvs.NotebookVersionsMixin):
    def __init__(self, root, notebooks=None):
        self.root = root
        self.notebooks = notebooks


def test_remember_base_gives_each_cell_its_screen_id(tmp_path):
    host = Host(tmp_path)
    cells = [{"type": "code", "source": "a = 1"}, {"type": "markdown", "source": "# hi"}]
    host._remember_base("nb", STAMP, cells, ids=["c1", "c2"])
    assert host._sync_bases.find("nb", STAMP) == [
        {"cell_id": "c1", "type": "code", "source": "a = 1"}, {"cell_id": "c2", "type": "markdown", "source": "# hi"}]
    assert "cell_id" not in cells[0]  # the caller's cells are left as they were


def test_remember_base_without_a_stamp_keeps_nothing(tmp_path):
    host = Host(tmp_path)
    host._remember_base("nb", None, [{"type": "code", "source": "x"}])
    assert not (tmp_path / "notebook-sync").exists()


def test_remember_base_failure_is_logged_not_raised(tmp_path, caplog):
    host = Host(tmp_path)
    with caplog.at_level(logging.WARNING, logger=nvs.__name__):
        host._remember_base("nb", STAMP, [{"type": "code", "source": "x"}], ids=["c1", "c2"])  # ids don't fit
    assert "Couldn't keep the sync base of 'nb'" in caplog.text
    assert host._sync_bases.find("nb", STAMP) is None


def test_sync_preview_failure_is_logged_and_none(tmp_path, caplog, monkeypatch):
    host = Host(tmp_path)
    host._remember_base("nb", STAMP, [{"type": "code", "source": "x"}], ids=["c1"])
    def missing(name):
        raise ValueError(f"There's no notebook named {name!r}.")
    monkeypatch.setattr(host, "_saved_nb", missing)
    with caplog.at_level(logging.WARNING, logger=nvs.__name__):
        assert host._sync_preview("nb", [{"type": "code", "source": "y"}], STAMP) is None
    assert "Couldn't merge the two versions of 'nb'" in caplog.text


def test_sync_preview_without_a_base_is_none(tmp_path):
    assert Host(tmp_path)._sync_preview("nb", [], STAMP) is None


def test_sync_merge_without_a_base_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no longer has the version both started from"):
        Host(tmp_path, SimpleNamespace()).notebook_sync_merge("nb", [], STAMP)
