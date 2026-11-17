"""SQL templates (sql_templates_service.py): the exact limits, the header parse at its edges, the
places' labels, links inside a place, and the names and messages the other tests don't pin down."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from fabric_desktop import sql_templates_service as st
from tests.test_sql_templates import SQL, svc  # noqa: F401  (svc is a fixture)

_REAL_OPEN = builtins.open  # saved once: the tests below replace the module's open


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _rendered_size(title: str, sql: str) -> int:
    return len(st.render({"title": title, "sql": sql}).encode("utf-8"))


def _sql_of_size(title: str, size: int) -> str:
    """SQL that makes the saved file exactly ``size`` bytes."""
    base = _rendered_size(title, "SELECT 1 -- ")
    return "SELECT 1 -- " + "x" * (size - base)


# ---- the limits, exactly

def test_a_title_of_exactly_100_characters_is_taken_and_101_refused():
    assert st.problem({"title": "t" * 100, "sql": SQL}) == ""
    assert st.problem({"title": "t" * 101, "sql": SQL}) == "Use a title of up to 100 characters on one line."


@pytest.mark.parametrize("key", ["group", "description"])
def test_a_group_or_description_of_exactly_200_characters_is_taken_and_201_refused(key):
    assert st.problem({"title": "A", key: "g" * 200, "sql": SQL}) == ""
    assert st.problem({"title": "A", key: "g" * 201, "sql": SQL}) == f"Use a {key} of up to 200 characters on one line."


def test_a_saved_template_of_exactly_64_kb_is_taken_and_one_byte_more_refused(svc):
    assert st.MAX_BYTES == 64 * 1024
    saved = svc.sql_template_save("mine", "Big", _sql_of_size("Big", 64 * 1024))
    assert Path(saved["path"]).stat().st_size == 64 * 1024
    with pytest.raises(ValueError) as e:
        svc.sql_template_save("mine", "Bigger", _sql_of_size("Bigger", 64 * 1024 + 1))
    assert str(e.value) == "A template can be up to 64 KB."


def test_a_file_of_exactly_64_kb_is_listed_and_one_byte_more_is_listed_as_too_large(svc, tmp_path):
    folder = tmp_path / "app" / "sql-templates"
    folder.mkdir(parents=True)
    (folder / "a.sql").write_bytes(b"SELECT 1 -- " + b"x" * (64 * 1024 - 12))
    (folder / "b.sql").write_bytes(b"SELECT 1 -- " + b"x" * (64 * 1024 - 11))
    got = {t["name"]: t for t in svc.sql_templates_list()["templates"]}
    assert got["a.sql"]["problem"] == "" and got["a.sql"]["sql"].startswith("SELECT 1")
    assert got["b.sql"]["problem"] == "It is larger than 64 KB."
    assert got["b.sql"]["endpoint"] is False and got["b.sql"]["sql"] == "" and got["b.sql"]["title"] == "b"


def test_an_unreadable_file_is_never_listed_as_an_endpoint_template(svc, tmp_path):
    folder = tmp_path / "app" / "sql-templates"
    folder.mkdir(parents=True)
    (folder / "latin.sql").write_bytes("-- endpoint: yes\nSELECT 'caf\xe9'".encode("latin-1"))
    [item] = svc.sql_templates_list()["templates"]
    assert item["problem"] == "It couldn't be read as UTF-8 text." and item["endpoint"] is False


def test_a_place_with_exactly_200_templates_lists_them_all_without_a_note(svc, tmp_path):
    assert st.MAX_PER_PLACE == 200
    folder = tmp_path / "app" / "sql-templates"
    folder.mkdir(parents=True)
    for n in range(200):
        (folder / f"t{n:03}.sql").write_text("SELECT 1", encoding="utf-8")
    listed = svc.sql_templates_list()
    assert len(listed["templates"]) == 200 and listed["notes"] == []
    (folder / "t200.sql").write_text("SELECT 1", encoding="utf-8")
    listed = svc.sql_templates_list()
    assert len(listed["templates"]) == 200
    assert listed["notes"] == ["My templates has 201 templates; the first 200 by name are listed."]


# ---- the header parse at its edges

def test_a_file_that_is_only_header_lines_has_no_sql():
    assert st.parse("-- template: Only a title") == {"title": "Only a title", "group": "", "description": "",
                                                     "endpoint": False, "sql": ""}
    assert st.parse("-- template: A\n-- endpoint: yes")["sql"] == ""
    assert st.parse("-- template: A\n")["sql"] == ""


def test_the_line_after_the_header_is_dropped_when_it_is_only_spaces():
    assert st.parse("-- template: A\n   \nSELECT 1")["sql"] == "SELECT 1"
    assert st.parse("-- template: A\n\t\n  SELECT 1")["sql"] == "  SELECT 1"
    # A file with no header keeps a first line of spaces: it is the SQL's.
    assert st.parse("   \nSELECT 1", "f")["sql"] == "   \nSELECT 1"


# ---- saving, importing, the defaults and what each call returns

def test_a_template_saved_without_endpoint_is_not_an_endpoint_template(svc):
    svc.sql_template_save("mine", "Plain", SQL)
    [item] = svc.sql_templates_list()["templates"]
    assert item["endpoint"] is False
    assert "-- endpoint: no" in (svc.root / "sql-templates" / "Plain.sql").read_text(encoding="utf-8")


def test_editing_a_template_says_which_file_it_wrote(svc):
    first = svc.sql_template_save("mine", "Load", SQL)
    again = svc.sql_template_save("mine", "Load v2", "SELECT 2", name="Load.sql")
    assert again == {"place": "mine", "name": "Load.sql", "path": first["path"]}
    assert st.parse(Path(first["path"]).read_text(encoding="utf-8"))["title"] == "Load v2"


def test_an_import_that_isnt_text_is_refused_by_name(svc):
    for text in (None, 7, b"SELECT 1"):
        with pytest.raises(ValueError) as e:
            svc.sql_template_import("mine", "x.sql", text)
        assert str(e.value) == "Pick a .sql file of up to 64 KB."
    assert svc.sql_templates_list()["templates"] == []


def test_an_import_of_exactly_64_kb_passes_the_file_check_and_one_byte_more_does_not(svc):
    exact = "SELECT 1 -- " + "x" * (64 * 1024 - 12)
    with pytest.raises(ValueError) as e:  # it fits as a file, but not with the header added on save
        svc.sql_template_import("mine", "x.sql", exact)
    assert str(e.value) == "A template can be up to 64 KB."
    with pytest.raises(ValueError) as e:
        svc.sql_template_import("mine", "x.sql", exact + "x")
    assert str(e.value) == "Pick a .sql file of up to 64 KB."
    assert svc.sql_template_import("mine", "x.sql", "SELECT 1")["name"] == "x.sql"


# ---- places

def test_a_repository_place_is_labelled_by_its_name_or_else_its_folder(svc, tmp_path, monkeypatch):
    named, unnamed, gone = tmp_path / "r1", tmp_path / "r2", tmp_path / "r3"
    named.mkdir()
    unnamed.mkdir()
    repos = [{"path": str(named), "exists": True, "name": "Team repo"},
             {"path": str(unnamed), "exists": True, "name": ""},
             {"path": str(gone), "exists": False, "name": "Gone"}]
    monkeypatch.setattr(svc, "git_repos", lambda: {"repos": repos})
    places = svc.sql_templates_list()["places"]
    assert [(p["id"], p["label"]) for p in places] == [("mine", "My templates"), (str(named), "Team repo"),
                                                       (str(unnamed), "r2")]


def test_a_link_inside_the_place_to_another_template_there_is_still_refused(svc, tmp_path):
    saved = svc.sql_template_save("mine", "Real", SQL)
    link = Path(saved["path"]).parent / "Alias.sql"
    try:
        link.symlink_to(saved["path"])
    except OSError:
        pytest.skip("this PC can't make links")
    for call in (lambda: svc.sql_template_delete("mine", "Alias.sql"),
                 lambda: svc.sql_template_export("mine", "Alias.sql"),
                 lambda: svc.sql_template_save("mine", "B", "SELECT 4", name="Alias.sql")):
        with pytest.raises(ValueError) as e:
            call()
        assert str(e.value) == "That template file is a link to somewhere else, so it isn't used."
    assert link.is_symlink() and st.parse(Path(saved["path"]).read_text(encoding="utf-8"))["title"] == "Real"


# ---- new file names

def test_an_export_is_named_after_the_template_then_numbered_from_2(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(st.known_folders, "downloads", lambda: tmp_path / "dl")
    svc.sql_template_save("mine", "Load", SQL)
    names = [Path(svc.sql_template_export("mine", "Load.sql")["path"]).name for _ in range(3)]
    assert names == ["Load.sql", "Load (2).sql", "Load (3).sql"]


def _only(free: str):
    """An open where every new name but ``free`` is taken."""
    def fake(path, mode="r", *args, **kwargs):
        if mode == "x" and not str(path).endswith(free):
            raise FileExistsError(path)
        return _REAL_OPEN(path, mode, *args, **kwargs)
    return fake


def test_a_new_template_tries_names_up_to_999_and_no_further(svc, monkeypatch):
    monkeypatch.setattr(st, "open", _only(" (999).sql"), raising=False)
    assert svc.sql_template_save("mine", "Load", SQL)["name"] == "Load (999).sql"
    monkeypatch.setattr(st, "open", _only(" (1000).sql"), raising=False)
    with pytest.raises(ValueError, match="Couldn't find a free file name for the template"):
        svc.sql_template_save("mine", "Load", SQL)


def test_an_export_tries_names_up_to_999_and_no_further(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(st.known_folders, "downloads", lambda: tmp_path / "dl")
    svc.sql_template_save("mine", "Load", SQL)
    monkeypatch.setattr(st, "open", _only(" (999).sql"), raising=False)
    assert Path(svc.sql_template_export("mine", "Load.sql")["path"]).name == "Load (999).sql"
    monkeypatch.setattr(st, "open", _only(" (1000).sql"), raising=False)
    with pytest.raises(ValueError, match="Couldn't find a free file name in Downloads"):
        svc.sql_template_export("mine", "Load.sql")
