"""Checks before a promote (duties.py): what mutation testing found unchecked. A role list read with an
error is a note even when some rows came back, only three Admins are named, a recorded object id that
is empty or isn't text counts as none, and the login name is the process's own."""

from __future__ import annotations

import json

from fabric_desktop import duties
from tests.test_duties import ME, assignment


def test_an_access_list_read_with_an_error_is_a_note_whatever_came_back():
    errors, notes = duties.role_problems("Prod", [assignment(ME, "Viewer")], {ME}, "429 Too Many Requests")
    assert errors == [] and notes[0].startswith("Couldn't read who has access to Prod (429 Too Many Requests)")


def test_at_most_three_admins_are_named():
    admins = [assignment(f"{i}" * 8 + "-2222-2222-2222-222222222222", "Admin") for i in range(1, 5)]
    [error], _ = duties.role_problems("Prod", [assignment(ME, "Viewer"), *admins], {ME})
    assert "Ask one of its Admins (user 1111, user 2222, user 3333) to make you a Contributor." in error
    assert "user 4444" not in error


def test_a_recorded_object_id_that_is_empty_or_not_text_is_none():
    for oid in ("", 5, None):
        assert duties.recorded_object_id(json.dumps({"identity": {"object_id": oid}})) is None
    assert duties.recorded_object_id(json.dumps({"identity": {"object_id": ME}})) == ME


def test_the_login_name_is_the_processs_own(monkeypatch):
    monkeypatch.setattr(duties.getpass, "getuser", lambda: "pat")
    assert duties.current_user() == "pat"

    def no_login():
        raise OSError("no login name")
    monkeypatch.setattr(duties.getpass, "getuser", no_login)
    assert duties.current_user() is None
