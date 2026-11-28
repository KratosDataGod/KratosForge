"""duties.py: the branches the FD-130 tests leave out (no admins to ask, receipts, no login name)."""

from fabric_desktop import duties

ME = "11111111-1111-1111-1111-111111111111"


def assignment(pid, role, kind="User", upn="", name=None):
    return {"principal": {"id": pid, "type": kind, "displayName": name or f"user {pid[:4]}",
                          "userDetails": {"userPrincipalName": upn}}, "role": role}


def test_a_direct_viewer_with_no_admin_users_gets_no_one_to_ask():
    # An Admin that is an app isn't someone to ask; a Member user isn't an Admin.
    app = assignment("44444444-4444-4444-4444-444444444444", "Admin", "ServicePrincipal")
    member = assignment("55555555-5555-5555-5555-555555555555", "Member")
    errors, notes = duties.role_problems("Prod", [assignment(ME, "Viewer"), app, member], {ME})
    assert errors == ["You're a Viewer in Prod; promoting writes tables there, which needs Contributor or higher."]
    assert notes == []


def test_only_three_admins_are_named_in_alphabetical_order():
    admins = [assignment(f"{i}" * 8, "Admin", name=n) for i, n in enumerate(["dee", "bo", "cy", "al"], 2)]
    errors, _ = duties.role_problems("Prod", [assignment(ME, "Viewer"), *admins], {ME})
    assert errors[0].endswith("Ask one of its Admins (al, bo, cy) to make you a Contributor.")


def test_an_error_with_assignments_is_still_a_note():
    errors, notes = duties.role_problems("Test", [assignment(ME, "Admin")], {ME}, error="timeout")
    assert errors == [] and notes[0].startswith("Couldn't read who has access to Test (timeout)")


def test_matched_by_sign_in_name_when_the_id_is_missing():
    a = {"principal": {"type": "User", "userDetails": {"userPrincipalName": "Pat@Contoso.com"}}, "role": "Viewer"}
    errors, notes = duties.role_problems("Prod", [a], {"pat@contoso.com"})
    assert notes == [] and errors[0].startswith("You're a Viewer in Prod")
    # Someone else with no id: not me, so the person isn't found.
    assert "Couldn't find you" in duties.role_problems("Prod", [a], {"ann@contoso.com"})[1][0]


def test_the_highest_of_several_roles_counts():
    assert duties.role_problems("Prod", [assignment(ME, "Viewer"), assignment(ME, "Member")], {ME}) == ([], [])


def test_separation_with_no_login_name_and_no_ids_is_someone_else():
    assert duties.separation_problem("test", "prod", 7, "pat", None) is None
    assert duties.separation_problem("test", "prod", 7, "pat", "", None, ME) is None


def test_separation_note_is_silent_with_both_ids():
    assert duties.separation_note(ME, ME) is None
    assert "no Microsoft Entra id" in duties.separation_note(None, None)


def test_recorded_object_id_reads_the_receipt():
    assert duties.recorded_object_id('{"identity": {"object_id": "abc"}}') == "abc"
    assert duties.recorded_object_id(None) is None
    assert duties.recorded_object_id("") is None
    assert duties.recorded_object_id("not json") is None
    assert duties.recorded_object_id("[1, 2]") is None  # a list has no .get
    assert duties.recorded_object_id('{"identity": "x"}') is None
    assert duties.recorded_object_id('{"identity": null}') is None
    assert duties.recorded_object_id('{"identity": {"object_id": ""}}') is None
    assert duties.recorded_object_id('{"identity": {"object_id": 5}}') is None


def test_current_user_is_the_login_name(monkeypatch):
    monkeypatch.setattr(duties.getpass, "getuser", lambda: "pat")
    assert duties.current_user() == "pat"


def test_current_user_without_a_login_name_is_none(monkeypatch, caplog):
    def boom():
        raise OSError("No username set in the environment")
    monkeypatch.setattr(duties.getpass, "getuser", boom)
    with caplog.at_level("INFO", logger=duties.__name__):
        assert duties.current_user() is None
    assert "No login name for this process" in caplog.text
