"""What the app reads at start, and when a project or notebook opens, after a crash or a power cut.

A power cut can leave a file empty, cut off part way, or full of NUL bytes (NTFS can keep a file's new
length before its data reaches the disk), and a temporary file beside it that was never swapped in. Each
read here either loads intact, or falls back with a note in the log and keeps the damaged file, set aside
or left where it is, so the next save never silently overwrites what was in it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fabric_desktop import app_settings, audit, audit_chain
from fabric_desktop.localdata.projects import ProjectStore
from fabric_desktop.notebook import NotebookStore

DAMAGE = {"empty": b"", "cut off": b'{"a": [1, 2', "NUL bytes": b"\x00" * 64}


def _aside(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


@pytest.mark.parametrize("form", sorted(DAMAGE))
def test_settings_fall_back_to_defaults_and_the_damaged_file_is_kept(form, tmp_path, caplog):
    path = tmp_path / "app-settings.json"
    path.write_bytes(DAMAGE[form])
    with caplog.at_level(logging.WARNING):
        assert app_settings._read(path) == {}
    assert [p.read_bytes() for p in _aside(path)] == [DAMAGE[form]]
    assert "back to their defaults" in caplog.text


@pytest.mark.parametrize("form", sorted(DAMAGE))
def test_a_damaged_project_list_is_rebuilt_from_the_projects_own_files(form, tmp_path, caplog):
    store = ProjectStore(tmp_path / "local-data")
    store.create("Sales")
    store.create("Stock")
    store.index_file.write_bytes(DAMAGE[form])
    with caplog.at_level(logging.WARNING):
        assert [p.name for p in store.list()] == ["Sales", "Stock"]
    assert [p.read_bytes() for p in _aside(store.index_file)] == [DAMAGE[form]]
    assert sorted(json.loads(store.index_file.read_text())) == ["sales", "stock"]  # saved again
    assert "rebuilt from the projects' own files: 2 found" in caplog.text


def test_a_project_kept_in_another_folder_is_found_again_from_what_the_cut_off_list_still_names(tmp_path):
    store = ProjectStore(tmp_path / "local-data")
    elsewhere = store.create("Finance", folder=str(tmp_path / "D drive" / "finance 'Ünï' 2026"))  # a name JSON escapes; no " on Windows
    store.create("Sales")
    store.create("Later", folder=str(tmp_path / "later"))
    text = store.index_file.read_text()
    store.index_file.write_text(text[:text.index('"later"') + 12])  # cut off inside the last folder
    found = {p.id: p.folder for p in store.list()}
    assert found == {"finance": elsewhere.folder, "sales": str((tmp_path / "local-data" / "sales").resolve())}


def test_a_folder_the_damaged_list_names_counts_only_where_that_project_is(tmp_path):
    store = ProjectStore(tmp_path / "local-data")
    other = tmp_path / "other"
    other.mkdir()
    (other / "project.json").write_text(json.dumps({"id": "someone-else", "name": "X"}))
    store.root.mkdir(parents=True)
    store.index_file.write_text('{"finance": ' + json.dumps(str(other)) + ', "gone": "' + str(tmp_path / "nope"))
    assert store.list() == []
    assert not store.index_file.exists()  # nothing found: no list until a project is made


@pytest.mark.parametrize("form", sorted(DAMAGE))
def test_a_damaged_project_file_leaves_the_others_listed_and_is_never_replaced(form, tmp_path, caplog):
    store = ProjectStore(tmp_path / "local-data")
    sales = store.create("Sales")
    store.create("Stock")
    damaged = Path(sales.folder) / "project.json"
    damaged.write_bytes(DAMAGE[form])
    with caplog.at_level(logging.WARNING):
        assert [p.name for p in store.list()] == ["Stock"]
    assert "isn't listed" in caplog.text and "project.json" in caplog.text
    with pytest.raises(ValueError, match="isn't a local data project file that can be read"):
        store.load("sales")
    with pytest.raises(ValueError, match="already holds a local data project"):
        store.create("Sales again", folder=sales.folder)
    assert damaged.read_bytes() == DAMAGE[form]


@pytest.mark.parametrize("form", sorted(DAMAGE))
def test_a_damaged_notebook_says_so_and_is_left_as_it_is(form, tmp_path):
    store = NotebookStore(tmp_path)
    store.save("Daily", [{"type": "code", "source": "x = 1"}])
    store.path("Daily").write_bytes(DAMAGE[form])
    with pytest.raises(ValueError, match=r"Daily\.ipynb is damaged or isn't a notebook any more.*Fix it in a text editor, or put back an earlier copy\."):
        store.load("Daily")
    assert store.path("Daily").read_bytes() == DAMAGE[form]


def test_a_temporary_file_a_power_cut_left_beside_a_file_is_not_read(tmp_path):
    store = ProjectStore(tmp_path / "local-data")
    store.create("Sales")
    (store.root / ".projects.json.abc123.tmp").write_bytes(b'{"half')  # jsonfile.write_text's, never swapped in
    settings = tmp_path / "app-settings.json"
    settings.write_text(json.dumps({"theme": "dark"} if "theme" in app_settings.DEFAULTS else {}))
    (tmp_path / ".app-settings.json.abc123.tmp").write_bytes(b"\x00" * 16)
    assert [p.name for p in store.list()] == ["Sales"]
    assert not _aside(store.index_file) and not _aside(settings)
    nb = NotebookStore(tmp_path / "nb")
    nb.folder.mkdir()
    nb.save("Daily", [{"type": "code", "source": "x = 1"}])
    (nb.folder / "Daily.ipynb.0123abcd.tmp").write_bytes(b"{")
    assert [n["name"] for n in nb.list()] == ["Daily"] and nb.load("Daily")["cells"][0]["source"] == "x = 1"


def test_missing_files_start_empty_and_nothing_is_set_aside(tmp_path):
    assert app_settings._read(tmp_path / "app-settings.json") == {}
    store = ProjectStore(tmp_path / "local-data")
    assert store.list() == []
    assert not list(tmp_path.rglob("*.corrupt-*"))


@pytest.mark.parametrize("tail", [b'{"at": "2026-11', b"\x00" * 40, b'{"detail": "Caf\xc3'],
                         ids=["cut off", "NUL bytes", "half of a multi-byte character"])
def test_an_audit_line_cut_off_by_a_power_cut_doesnt_stop_the_history_or_damage_the_next_line(tail, tmp_path):
    log = audit.AuditLog(tmp_path)
    log.record("first", "ok", 0.1)
    log.record("second", "ok", 0.1)
    trail = audit_chain.files(tmp_path)[-1]
    trail.write_bytes(trail.read_bytes() + tail)  # the append stopped before its newline
    # The history still shows, with the cut line as a placeholder row that says where it is (#3088).
    assert [e["action"] for e in log.recent()] == [audit.UNREADABLE, "second", "first"]
    assert [e["action"] for e in log.recent(action="second")] == ["second"]
    log.record("third", "ok", 0.1)
    lines = trail.read_bytes().splitlines()  # the trail is written in text mode: CRLF on Windows
    assert lines[2] == tail  # kept as it was: the trail is never rewritten
    assert json.loads(lines[3])["action"] == "third"  # on a line of its own, whole
    assert [e["action"] for e in log.recent()] == ["third", audit.UNREADABLE, "second", "first"]
    problems = audit_chain.verify(tmp_path)["problems"]
    assert any("line 3: not an audit line" in p for p in problems), problems
    assert not any("deleted from the end" in p for p in problems), problems  # the head counts the cut line


@pytest.mark.parametrize("text", ["[]", '"x"', "[" * 100000], ids=["a list", "a string", "nested too deeply"])
def test_a_project_list_that_isnt_one_is_rebuilt_not_emptied(text, tmp_path):
    store = ProjectStore(tmp_path / "local-data")
    store.create("Sales")
    store.create("Stock")
    store.index_file.write_text(text)
    assert sorted(p.name for p in store.list()) == ["Sales", "Stock"]
