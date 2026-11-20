"""A release's kept files restored (meta/snapshot.py): an environment that never kept them, a kept file that
points outside the project, files that don't add up to the release, a folder left from before, and a project
with no environments.toml. The store is a stand-in holding the rows a promote writes."""

from __future__ import annotations

import base64
import hashlib

import polars as pl
import pytest

from fabric_desktop.meta import snapshot
from fabric_desktop.service import Service


@pytest.fixture
def project(tmp_path):
    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "repo" / "crm"), "3")
    return svc._project()


class Kept:
    """An environment's ``meta.release_files``, as rows."""

    env = "test"

    def __init__(self, rows):
        self.rows = rows

    def meta_where(self, table, cond):
        assert table == "release_files"
        df = pl.DataFrame(self.rows, schema={"version": pl.String, "path": pl.String, "sha256": pl.String,
                                             "content": pl.String})
        return df.filter(cond)


def rows(version, files):
    return [{"version": version, "path": rel, "sha256": hashlib.sha256(data).hexdigest(),
             "content": base64.b64encode(data).decode("ascii")} for rel, data in files.items()]


def leftovers(project):
    folder = project.local_data / "releases" / "test"
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(".")) if folder.exists() else []


def test_an_environment_that_never_kept_the_release_says_so(project):
    with pytest.raises(LookupError, match=r"^Test doesn't keep the files of release abc1234; it was promoted before"):
        snapshot.restore(project, Kept([]), "abc1234")


def test_a_kept_file_that_points_outside_the_project_is_refused_and_nothing_is_left(project):
    files = snapshot.files(project) | {"../escaped.txt": b"x"}
    with pytest.raises(ValueError, match=r"^\.\./escaped\.txt in release .* points outside the project$"):
        snapshot.restore(project, Kept(rows(project.version, files)), project.version)
    assert leftovers(project) == [] and not (project.local_data / "releases" / "test" / "escaped.txt").exists()


def test_files_that_do_not_add_up_to_the_release_are_refused(project):
    files = snapshot.files(project)
    files["layers.toml"] = files["layers.toml"] + b"\n# changed after the release\n"
    with pytest.raises(ValueError, match=f"^The files Test keeps for release {project.version} don't add up to it$"):
        snapshot.restore(project, Kept(rows(project.version, files)), project.version)
    assert leftovers(project) == [] and not (project.local_data / "releases" / "test" / project.version).exists()


def test_a_folder_left_from_before_that_isnt_the_release_is_replaced(project):
    dest = project.local_data / "releases" / "test" / project.version
    dest.mkdir(parents=True)
    (dest / "layers.toml").write_text("not [ toml", encoding="utf-8")  # doesn't load as a project
    restored = snapshot.restore(project, Kept(rows(project.version, snapshot.files(project))), project.version)
    assert restored.root == dest and restored.version == project.version
    assert (dest / "layers.toml").read_bytes() == snapshot.files(project)["layers.toml"]


def test_a_project_with_no_environments_file_restores_without_one(project):
    (project.root / "environments.toml").unlink()
    restored = snapshot.restore(project, Kept(rows(project.version, snapshot.files(project))), project.version)
    assert restored.version == project.version and not (restored.root / "environments.toml").exists()
