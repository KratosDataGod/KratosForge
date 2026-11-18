"""A project or data folder on a mapped drive or a network share is told apart from one on this PC.

Windows names a folder several ways: ``C:\\x``, its long-path form ``\\\\?\\C:\\x``, a share as
``\\\\server\\share\\x`` or ``\\\\?\\UNC\\server\\share\\x``, and a mapped drive ``Z:\\x``. The checks that warn
about a share (Ready to work, the support bundle, VS Code pairing) used to take the long-path form of a
folder on this PC for a share; and ``Path.resolve`` turns a mapped drive into the share's path, so a project
made on ``Z:`` was kept under a path the person never typed. The string logic runs on every OS; the
share itself, ``\\\\localhost\\C$``, only on windows-latest where it can be reached.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fabric_desktop import readiness, support_bundle, win_paths
from fabric_desktop.editor_service import _folder_problem
from fabric_desktop.localdata.projects import ProjectStore


@pytest.mark.parametrize("text, plain, share", [
    ("C:\\Users\\ann\\proj", "C:\\Users\\ann\\proj", False),
    ("\\\\?\\C:\\Users\\ann\\proj", "C:\\Users\\ann\\proj", False),
    ("\\\\.\\C:\\Users\\ann", "C:\\Users\\ann", False),
    ("\\\\server\\share\\proj", "\\\\server\\share\\proj", True),
    ("//server/share/proj", "//server/share/proj", True),
    ("\\\\?\\UNC\\server\\share\\proj", "\\\\server\\share\\proj", True),
    ("\\\\?\\unc\\server\\share", "\\\\server\\share", True),
    ("Z:\\proj", "Z:\\proj", False),   # mapped or not is Windows' to say
    ("/home/ann/proj", "/home/ann/proj", False),
])
def test_every_form_of_a_path_is_read_the_same_way(text, plain, share):
    assert win_paths.plain(text) == plain
    assert win_paths.is_share(text) is share


def test_the_long_path_form_of_a_local_folder_is_not_called_a_network_share():
    local = Path("\\\\?\\C:\\Users\\ann\\AppData\\Local\\KratosForge")
    assert readiness._on_network(local) is False
    row = readiness.data_folder_check(local, free=lambda p: 50 * 2**30)
    assert "network share" not in row["text"]
    assert support_bundle.path_facts(local)["data_folder_network"] is False
    assert _folder_problem("\\\\?\\C:\\Users\\ann\\notebooks") is None


def test_a_share_in_any_form_is_still_called_one():
    for text in ("\\\\server\\share\\kf", "\\\\?\\UNC\\server\\share\\kf"):
        assert readiness._on_network(Path(text)) is True
        row = readiness.data_folder_check(Path(text), network=readiness._on_network)
        assert row["status"] == "warn" and "network share" in row["text"]
        assert support_bundle.path_facts(Path(text))["data_folder_network"] is True
        assert "network folder" in (_folder_problem(text) or "")


@pytest.mark.parametrize("given, resolved, kept", [
    ("Z:\\proj", "\\\\server\\share\\proj", "Z:\\proj"),                    # a mapped drive keeps its letter
    ("Z:\\a\\..\\proj", "\\\\server\\share\\proj", "Z:\\proj"),
    ("\\\\?\\Z:\\proj", "\\\\?\\UNC\\server\\share\\proj", "Z:\\proj"),
    ("C:\\Users\\ann\\proj", "C:\\Users\\ann\\real", "C:\\Users\\ann\\real"),  # a junction on this PC: resolved
    ("\\\\server\\share\\proj", "\\\\server\\share\\proj", "\\\\server\\share\\proj"),
    ("/home/ann/proj", "/home/ann/proj", "/home/ann/proj"),
])
def test_a_project_made_on_a_mapped_drive_keeps_the_drive_letter(given, resolved, kept):
    assert win_paths.keep_drive_letter(given, resolved) == kept


def test_creating_a_project_on_a_mapped_drive_keeps_the_path_as_given(tmp_path, monkeypatch):
    # The store keeps the folder keep_drive_letter gives, not the resolved one. The kept folder is under
    # tmp_path (on Windows a drive-letter path too), so the test never writes to a real mapped Z: drive.
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))
    kept = str(tmp_path / "as-typed")
    store = ProjectStore(tmp_path / "local-data")
    monkeypatch.setattr(win_paths, "keep_drive_letter", lambda given, resolved: kept if
                        given == str(tmp_path / "sales") else resolved)
    project = store.create("Sales", folder=str(tmp_path / "sales"))
    assert project.folder == kept
    assert store._index()[project.id] == kept
    assert set(os.listdir(tmp_path)) - before <= {"local-data", "sales", "as-typed"}, "wrote outside its folders"


def _admin_share() -> Path | None:
    drive = os.environ.get("SystemDrive", "C:")[:1]
    share = Path(f"\\\\localhost\\{drive}$") / os.path.splitdrive(os.environ.get("TEMP", ""))[1].lstrip("\\")
    try:
        return share if share.is_dir() else None
    except OSError:
        return None


@pytest.mark.skipif(sys.platform != "win32", reason="a real network share path")
def test_a_project_on_a_share_opens_saves_and_lists(tmp_path):
    share = _admin_share()
    if share is None:
        pytest.skip("\\\\localhost\\C$ can't be reached on this runner")
    folder = share / f"kf-share-test-{os.getpid()}"
    store = ProjectStore(tmp_path / "local-data")
    try:
        project = store.create("On a share", folder=str(folder))
        assert win_paths.is_share(project.folder)
        project.settings.threads = 2  # a save: project.json rewritten through jsonfile.replace
        store.save(project)
        assert [p.id for p in store.list()] == [project.id]
        assert store.load(project.id).folder == project.folder
        assert readiness._on_network(Path(project.folder)) is True
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
