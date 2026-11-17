"""SQL templates: the refusals and full-folder paths the tests in test_sql_templates don't reach."""

import pytest

from fabric_desktop import sql_templates_service as st
from tests.test_sql_templates import SQL, svc  # noqa: F401  (svc is a fixture)


def _taken(*args, **kwargs):
    raise FileExistsError("taken")


@pytest.mark.parametrize("key", ["group", "description"])
def test_a_group_or_description_on_more_than_one_line_is_refused(key):
    assert st.problem({"title": "A", key: "one\ntwo", "sql": SQL}) == f"Use a {key} of up to {st.MAX_LINE} characters on one line."
    assert "one line" in st.problem({"title": "A", key: "x" * (st.MAX_LINE + 1), "sql": SQL})


def test_a_template_with_a_problem_or_too_large_is_never_written(svc):
    with pytest.raises(ValueError, match="Give the template a title"):
        svc.sql_template_save("mine", "  ", SQL)
    with pytest.raises(ValueError, match="A template can be up to"):
        svc.sql_template_save("mine", "Big", "SELECT 1 -- " + "x" * st.MAX_BYTES)
    assert svc.sql_templates_list()["templates"] == []


def test_a_place_with_every_name_taken_says_so(svc, monkeypatch):
    monkeypatch.setattr(st, "open", _taken, raising=False)
    with pytest.raises(ValueError, match="Couldn't find a free file name for the template"):
        svc.sql_template_save("mine", "Load", SQL)


def test_deleting_a_template_that_is_already_gone_is_not_an_error(svc):
    assert svc.sql_template_delete("mine", "Never saved.sql") == {"place": "mine", "name": "Never saved.sql", "deleted": True}


def test_an_export_with_every_downloads_name_taken_says_so(svc, tmp_path, monkeypatch):
    saved = svc.sql_template_save("mine", "Load", SQL)
    monkeypatch.setattr(st.known_folders, "downloads", lambda: tmp_path / "dl")
    monkeypatch.setattr(st, "open", _taken, raising=False)
    with pytest.raises(ValueError, match="free file name in Downloads"):
        svc.sql_template_export("mine", saved["name"])
