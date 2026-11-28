"""Fuzz the local data projects' files (localdata/projects.py): each project's project.json and the
list of projects, projects.json. Each case is a seeded mutation of a valid file (tests/settings_fuzz.py).

A project.json must load as the project it describes or be refused with a ValueError that names the
file, and one bad project must not take the others off the list. Reading never changes either file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop.localdata.projects import Project, ProjectStore
from fabric_desktop.tmdl import _RESERVED as _DEVICE_NAMES
from tests.settings_fuzz import mutations

CASES = 200


@pytest.fixture
def store(tmp_path, monkeypatch):
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    return ProjectStore(tmp_path / "local-data")


def _make(store: ProjectStore, tmp_path) -> tuple[Project, Project]:
    good = store.create("Kept", settings={"memory_limit": "2GB", "threads": 2})
    fuzzed = store.create("Fuzzed", settings={"memory_limit": "60%", "timeout_seconds": 30, "max_temp_size": "10GB"})
    src = tmp_path / "data"
    src.mkdir()
    from fabric_desktop.localdata.discovery import Source
    fuzzed.sources.append(Source(path=str(src), schema="sales", names={"a/b.csv": "sales.b"}))
    fuzzed.overrides["sales.b"] = {"amount": "DECIMAL(18,2)"}
    fuzzed.catalog["sales.b"] = {"format": "csv", "files": 1}
    store.save(fuzzed)
    return good, fuzzed


@pytest.mark.parametrize("seed", [11, 12])
def test_any_project_file_loads_as_its_project_or_is_refused_by_name(store, tmp_path, seed):
    good, fuzzed = _make(store, tmp_path)
    path = fuzzed.root / "project.json"
    valid = json.loads(path.read_text(encoding="utf-8"))
    assert store.load(fuzzed.id).summary() == fuzzed.summary()
    for label, raw in mutations(valid, seed, CASES):
        path.write_bytes(raw)
        try:
            loaded = store.load(fuzzed.id)
        except ValueError as exc:
            assert "project.json" in str(exc), f"{label}: {exc}"
        else:
            assert isinstance(loaded, Project) and isinstance(loaded.id, str), label
            assert isinstance(loaded.name, str) and isinstance(loaded.sources, list), label
            loaded.summary()  # what the app shows: must not fail later on a value of the wrong type
        assert [p.id for p in store.list() if p.id == good.id] == [good.id], label
        assert path.read_bytes() == raw, f"{label}: reading the project changed its file"


@pytest.mark.parametrize("seed", [13])
def test_any_project_list_file_lists_the_projects_it_can_or_none(store, tmp_path, seed):
    good, fuzzed = _make(store, tmp_path)
    valid = json.loads(store.index_file.read_text(encoding="utf-8"))
    for label, raw in mutations(valid, seed, CASES):
        for old in store.root.glob("projects.json*"):
            old.unlink()
        store.index_file.write_bytes(raw)
        listed = store.list()
        assert all(isinstance(p, Project) for p in listed), label
        if not store.index_file.exists():  # set aside: its bytes are kept
            assert any(p.read_bytes() == raw for p in store.root.glob("projects.json.corrupt-*")), label


def test_a_folder_where_the_project_file_should_be_is_refused_by_name(store, tmp_path):
    good, fuzzed = _make(store, tmp_path)
    (fuzzed.root / "project.json").unlink()
    (fuzzed.root / "project.json").mkdir()
    with pytest.raises(OSError):
        store.load(fuzzed.id)
    assert [p.id for p in store.list()] == [good.id]


def test_saved_projects_load_back_the_same(store, tmp_path):
    import random
    rnd = random.Random(14)
    _, fuzzed = _make(store, tmp_path)
    for i in range(40):
        fuzzed.name = "".join(rnd.choice("abcé xyz-_.") for _ in range(rnd.randint(1, 40))).strip() or "n"
        fuzzed.storage = rnd.choice(["persistent", "memory"])
        fuzzed.settings.threads = rnd.randint(0, 256)
        fuzzed.settings.memory_limit = rnd.choice(["", "4GB", "60%", "512 MiB"])
        fuzzed.overrides = {f"s.t{i}": {"c": rnd.choice(["INTEGER", "VARCHAR", "DECIMAL(10,2)"])}}
        fuzzed.refreshed = {"x": {"fingerprint": str(rnd.random()), "issues": []}}
        store.save(fuzzed)
        assert store.load(fuzzed.id) == fuzzed


def test_a_project_left_off_the_list_and_a_dropped_list_entry_are_logged(store, tmp_path, caplog):
    good, fuzzed = _make(store, tmp_path)
    (fuzzed.root / "project.json").write_text('{"sources": 5}', encoding="utf-8")
    index = json.loads(store.index_file.read_text(encoding="utf-8"))
    index["hand-edit"] = 1.5
    store.index_file.write_text(json.dumps(index), encoding="utf-8")
    with caplog.at_level("WARNING", logger="fabric_desktop.localdata.projects"):
        assert [p.id for p in store.list()] == [good.id]
    text = caplog.text
    assert f"Local data project {fuzzed.id} isn't listed" in text and "project.json" in text
    assert "'hand-edit': 1.5" in text


@pytest.mark.parametrize("name", ["NUL", "con", "Aux", "PRN", "com1", "LPT9", "  nul  ", "Nul!"])
def test_a_project_named_like_a_windows_device_gets_a_folder_windows_can_open(store, name):
    # On Windows a folder called nul or com1 opens the device, so the project file could never be written
    project = store.create(name)
    folder = Path(project.folder).name
    assert folder.split(".")[0].upper() not in _DEVICE_NAMES, folder
    assert folder == f"{name.strip(' !').lower()}-project"
    assert store.load(project.id).name == name.strip()
    assert store.create(f"{name.strip()} 2").id != project.id


def test_a_name_that_only_starts_like_a_device_keeps_its_folder_name(store):
    assert Path(store.create("Nullable").folder).name == "nullable"
    assert Path(store.create("con sales").folder).name == "con-sales"


def test_a_device_named_project_and_a_project_already_called_that_get_their_own_folders(store):
    first = store.create("nul-project")
    second = store.create("NUL")
    assert (Path(first.folder).name, Path(second.folder).name) == ("nul-project", "nul-project-2")


@pytest.mark.parametrize("part", ["nul", "CON", "com1.data", "lpt9 ", "aux.txt"])
def test_a_chosen_folder_named_like_a_windows_device_is_refused_by_name(store, tmp_path, part):
    folder = str(tmp_path / "picked" / part / "sales")
    with pytest.raises(ValueError, match=f"because Windows reads {part!r} as a device, not a folder"):
        store.create("Sales", folder=folder)
    assert store.list() == []
    assert not (tmp_path / "picked").exists()


def test_a_chosen_folder_that_only_starts_like_a_device_is_used(store, tmp_path):
    project = store.create("Sales", folder=str(tmp_path / "nullable" / "console"))
    assert Path(project.folder).name == "console"
