"""Saved queries: the refusals and unreadable files the main tests don't reach. Every file is in a
temporary app folder; nothing runs SQL or calls Fabric."""

import pytest

from fabric_desktop import sql_queries_service as sq
from fabric_desktop.sql_templates_service import MAX_BYTES
from tests.test_sql_queries import svc  # noqa: F401  (fixture)


@pytest.mark.parametrize("meta, why", [
    ({"title": "", "sql": "SELECT 1"}, "Give the query a name."),
    ({"title": "x" * (sq.MAX_TITLE + 1), "sql": "SELECT 1"}, "up to 100 characters on one line"),
    ({"title": "two\nlines", "sql": "SELECT 1"}, "up to 100 characters on one line"),
    ({"title": "A", "warehouse": "w" * (sq.MAX_LINE + 1), "sql": "SELECT 1"}, "warehouse of up to 200"),
    ({"title": "A", "description": "a\nb", "sql": "SELECT 1"}, "description of up to 200"),
])
def test_a_query_that_cant_be_saved_says_why(meta, why):
    assert why in sq.problem(meta)


def test_save_refuses_a_bad_query_before_writing_anything(svc, tmp_path):
    with pytest.raises(ValueError, match="Give the query a name"):
        svc.sql_query_save("mine", "  ", "SELECT 1")
    with pytest.raises(ValueError, match="can be up to"):
        svc.sql_query_save("mine", "Huge", "SELECT 1 -- " + "x" * MAX_BYTES)
    assert not (tmp_path / "app" / "sql-queries").exists() or not list((tmp_path / "app" / "sql-queries").iterdir())


def test_saving_over_a_file_that_was_removed_asks_for_a_new_one(svc):
    saved = svc.sql_query_save("mine", "Gone soon", "SELECT 1")
    svc.sql_query_delete("mine", saved["name"])
    with pytest.raises(ValueError, match="isn't there any more"):
        svc.sql_query_save("mine", "Gone soon", "SELECT 2", name=saved["name"])


def test_deleting_a_query_that_is_already_gone_is_fine(svc):
    svc.sql_query_save("mine", "Keep", "SELECT 1")
    assert svc.sql_query_delete("mine", "Never there.sql") == {"place": "mine", "name": "Never there.sql",
                                                               "deleted": True}


def test_files_that_cant_be_read_are_listed_with_why(svc, tmp_path):
    svc.sql_query_save("mine", "Fine", "SELECT 1")
    folder = tmp_path / "app" / "sql-queries"
    (folder / "binary.sql").write_bytes(b"\xff\xfe\x00bad")
    (folder / "big.sql").write_bytes(b"-- " + b"x" * (MAX_BYTES + 1))
    (folder / "empty.sql").write_bytes(b"-- query: Nothing here\n")
    listed = {q["name"]: q for q in svc.sql_queries_list()["queries"]}
    assert listed["binary.sql"]["problem"] == "It couldn't be read as UTF-8 text."
    assert listed["binary.sql"]["title"] == "binary" and listed["binary.sql"]["version"] == ""
    assert "larger than" in listed["big.sql"]["problem"]
    assert listed["empty.sql"]["problem"] == "The file has no SQL."
    assert listed["Fine.sql"]["problem"] == ""


def test_a_file_that_fails_to_read_is_listed_as_unreadable(svc, tmp_path, monkeypatch):
    svc.sql_query_save("mine", "Locked", "SELECT 1")
    real = type(tmp_path).read_bytes

    def locked(self):
        if self.name == "Locked.sql":
            raise PermissionError("in use")
        return real(self)

    monkeypatch.setattr(type(tmp_path), "read_bytes", locked)
    q = svc.sql_queries_list()["queries"][0]
    assert (q["title"], q["problem"]) == ("Locked", "It couldn't be read.")
