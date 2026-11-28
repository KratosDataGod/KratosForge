"""Mutation batch 491: a failed read of the access list is a note even beside a list, the Admins
named are at most three, the recorded Entra id must be text, and the login name is the process's."""
from __future__ import annotations

import json

import pytest

from fabric_desktop import duties

ME = "11111111-1111-1111-1111-111111111111"


def assignment(pid, role):
    return {"principal": {"id": pid, "type": "User", "displayName": f"user {pid[:4]}"}, "role": role}


def test_a_read_error_is_a_note_even_when_a_list_came_back():
    errors, notes = duties.role_problems("Prod", [assignment(ME, "Admin")], {ME}, "403 Forbidden")
    assert errors == [] and notes[0].startswith("Couldn't read who has access to Prod (403 Forbidden)")


def test_no_list_and_no_error_is_a_note_not_a_crash():
    errors, notes = duties.role_problems("Prod", None, {ME})
    assert errors == [] and notes[0].startswith("Couldn't read who has access to Prod (None)")


def test_at_most_three_admins_are_named():
    admins = [assignment(f"{i}" * 8 + "-0000-0000-0000-000000000000", "Admin") for i in range(2, 7)]
    errors, _ = duties.role_problems("Prod", [assignment(ME, "Viewer"), *admins], {ME})
    assert errors[0].endswith("Ask one of its Admins (user 2222, user 3333, user 4444) to make you a Contributor.")


@pytest.mark.parametrize("oid", ["", 5, ["x"]])
def test_a_recorded_entra_id_that_isnt_text_is_no_id(oid):
    assert duties.recorded_object_id(json.dumps({"identity": {"object_id": oid}})) is None


def test_the_login_name_is_the_processs(monkeypatch):
    monkeypatch.setattr(duties.getpass, "getuser", lambda: "ana")
    assert duties.current_user() == "ana"
