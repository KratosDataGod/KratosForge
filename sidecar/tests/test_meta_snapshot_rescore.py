"""Re-score tests for meta/snapshot.py (docs/quality/mutation-testing.md, batch 250).

A restore that fails and then can't delete its temporary folder still reports why the restore
failed, not the failed delete.
"""

from __future__ import annotations

import os

import polars as pl
import pytest

from fabric_desktop.meta import snapshot
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "repo" / "crm"), "3")
    s.meta_set_environment("test", target="local")
    return s


def test_a_failed_restore_whose_cleanup_also_fails_still_says_why_the_restore_failed(svc, monkeypatch):
    project = svc._project()
    store = svc._store(project, "test")
    snapshot.save(store, project)
    kept = store.meta("release_files").filter(pl.col("path") != "layers.toml")
    store.write("LH_Meta", "meta", "release_files", kept, mode="overwrite")
    releases = project.local_data / "releases" / "test"
    real_unlink = os.unlink

    def locked(path, *args, **kwargs):
        raise PermissionError(13, "The file is in use", str(path))

    monkeypatch.setattr(os, "unlink", locked)
    with pytest.raises(ValueError, match="don't add up to it"):
        snapshot.restore(project, store, project.version)
    monkeypatch.setattr(os, "unlink", real_unlink)
    left = [p.name for p in releases.iterdir() if p.name.startswith(f".{project.version}-")]
    assert left, "the delete was made to fail, so the temporary folder is still there"
    assert not (releases / project.version).exists()
