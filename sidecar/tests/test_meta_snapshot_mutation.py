"""Kill tests for meta/snapshot.py (docs/quality/mutation-testing.md, batch 114).

How many files a release keeps (and none the second time), and a restore that refuses a release the
environment doesn't keep, a path outside the project and files that don't add up to the version,
leaving no half-written folder; a folder that doesn't load isn't the release.
"""

from __future__ import annotations

import base64
import hashlib
import shutil

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


def _store(svc):
    return svc._store(svc._project(), "test")


def _leftovers(svc, version):
    folder = svc._project().local_data / "releases" / "test"
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(f".{version}-")) if folder.is_dir() else []


def test_a_release_keeps_each_file_once_and_says_how_many(svc):
    project, store = svc._project(), _store(svc)
    n = len(snapshot.files(project))
    assert n > 0
    assert snapshot.save(store, project) == n
    assert snapshot.save(store, project) == 0
    assert store.meta("release_files").height == n


def test_a_release_the_environment_does_not_keep_is_a_lookup_error(svc):
    with pytest.raises(LookupError, match="promoted before releases kept them"):
        snapshot.restore(svc._project(), _store(svc), "0000000")


def _rewrite(store, change):
    store.write("LH_Meta", "meta", "release_files", change(store.meta("release_files")), mode="overwrite")


def test_a_path_outside_the_project_is_refused(svc):
    project, store = svc._project(), _store(svc)
    snapshot.save(store, project)
    data = b"x\n"
    row = {"version": project.version, "path": "../outside.txt", "sha256": hashlib.sha256(data).hexdigest(),
           "content": base64.b64encode(data).decode("ascii")}
    _rewrite(store, lambda df: pl.concat([df, pl.DataFrame([row], schema=df.schema)]))
    with pytest.raises(ValueError, match="points outside the project"):
        snapshot.restore(project, store, project.version)
    assert _leftovers(svc, project.version) == []


def test_files_that_do_not_add_up_to_the_version_are_refused(svc):
    project, store = svc._project(), _store(svc)
    snapshot.save(store, project)
    _rewrite(store, lambda df: df.filter(pl.col("path") != "layers.toml"))
    with pytest.raises(ValueError, match="don't add up to it"):
        snapshot.restore(project, store, project.version)
    assert not (project.local_data / "releases" / "test" / project.version).exists()
    assert _leftovers(svc, project.version) == []


def test_a_folder_that_does_not_load_is_not_the_release(svc, tmp_path):
    project = svc._project()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not snapshot._matches(project, empty, project.version)
    shutil.rmtree(empty)
