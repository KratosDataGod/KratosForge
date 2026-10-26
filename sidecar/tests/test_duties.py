"""FD-130: workspace roles before a promote writes to Test or Prod, and a separate promoter for Prod."""

import json

import pytest

from fabric_desktop import app_settings, duties
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service

ME = "11111111-1111-1111-1111-111111111111"


def assignment(pid, role, kind="User", upn=""):
    return {"principal": {"id": pid, "type": kind, "displayName": pid if kind == "Group" else f"user {pid[:4]}",
                          "userDetails": {"userPrincipalName": upn}}, "role": role}


ADMIN = assignment("22222222-2222-2222-2222-222222222222", "Admin", upn="ann@contoso.com")


def test_a_write_role_passes():
    for role in ("Admin", "Member", "Contributor"):
        assert duties.role_problems("Prod", [assignment(ME, role)], {ME}) == ([], [])
    assert duties.role_problems("Prod", [assignment("x", "Contributor", upn="Pat@Contoso.com")], {"pat@contoso.com"}) == ([], [])


def test_a_direct_viewer_is_told_what_is_missing_and_whom_to_ask():
    errors, notes = duties.role_problems("Prod workspace Sales", [assignment(ME, "Viewer"), ADMIN], {ME})
    assert errors == ["You're a Viewer in Prod workspace Sales; promoting writes tables there, which needs "
                      "Contributor or higher. Ask one of its Admins (user 2222) to make you a Contributor."]
    assert notes == []


def test_what_this_list_cant_see_is_a_note_not_a_block():
    group = assignment("33333333-3333-3333-3333-333333333333", "Contributor", "Group")
    assert duties.role_problems("Prod", [assignment(ME, "Viewer"), group], {ME})[0] == []  # a group may give more
    assert duties.role_problems("Prod", [ADMIN], {ME})[0] == []  # a guest's id differs in this tenant
    errors, notes = duties.role_problems("Prod", None, {ME}, "403 Forbidden")
    assert errors == [] and "Couldn't read who has access to Prod (403 Forbidden)" in notes[0]


def test_separation_rule():
    assert duties.separation_problem("dev", "test", 3, None, "pat") is None
    assert "You promoted metadata 3 to Test" in duties.separation_problem("test", "prod", 3, "Pat", "pat")
    assert duties.separation_problem("test", "prod", 3, "ann", "pat") is None
    assert "doesn't say who" in duties.separation_problem("test", "prod", 3, None, "pat")



def test_separation_matches_on_the_entra_id_or_the_login():
    other = "22222222-2222-2222-2222-222222222222"
    # One person on two PCs with different logins: the ids catch it.
    assert "You promoted metadata 3" in duties.separation_problem("test", "prod", 3, "pat", "patricia", ME.upper(), ME)
    # Same login signed in with a second account (an admin one): still the same person.
    assert "You promoted metadata 3" in duties.separation_problem("test", "prod", 3, "pat", "pat", ME, other)
    # Different login and different account: someone else.
    assert duties.separation_problem("test", "prod", 3, "pat", "ann", ME, other) is None
    # Not signed in, or an old record: back to login names, and a note says so.
    assert "You promoted metadata 3" in duties.separation_problem("test", "prod", 3, "pat", "pat", ME, None)
    assert "you aren't signed in" in duties.separation_note(ME, None)
    assert "no Microsoft Entra id" in duties.separation_note(None, ME)
    assert duties.separation_note(ME, other) is None
    assert duties.recorded_object_id('{"identity": {"user": "pat", "object_id": "%s"}}' % ME) == ME
    assert duties.recorded_object_id('{"identity": {"user": "pat"}}') is None
    assert duties.recorded_object_id("not json") is None and duties.recorded_object_id(None) is None


def test_the_release_record_names_the_promoters_entra_id(svc, monkeypatch):
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["prod"]["separate_promoter"] = True
    svc._write_environments(project, envs)
    monkeypatch.setattr(duties, "current_user", lambda: "pat")
    monkeypatch.setattr("fabric_desktop.meta.release._user", lambda: "pat")
    svc._auth = _Auth()
    svc.meta_promote("dev", "test")
    row = svc._store(svc._project(), "test").meta("deployments").row(-1, named=True)
    assert duties.recorded_object_id(row["receipt"]) == ME
    assert any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])

    class _Ann(_Auth):
        def signed_in_object_id(self):
            return "22222222-2222-2222-2222-222222222222"

    svc._auth = _Ann()  # pat's login, another account: still pat
    check = svc.meta_promote_check("test", "prod")
    assert any("someone else" in e for e in check["errors"])
    assert not any("compare PC login names" in n for n in check["notes"])
    monkeypatch.setattr(duties, "current_user", lambda: "ann")  # Ann on her own login and account
    assert not any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])
    monkeypatch.setattr(duties, "current_user", lambda: "patricia")  # pat on another PC login: the id catches it
    svc._auth = _Auth()
    assert any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])
    monkeypatch.setattr(duties, "current_user", lambda: "pat")
    svc._auth = None  # signed out: login names, with a note
    check = svc.meta_promote_check("test", "prod")
    assert any("someone else" in e for e in check["errors"])
    assert any("you aren't signed in" in n for n in check["notes"])


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.meta_set_environment("test", target="local")
    s.meta_set_environment("prod", target="local")
    return s


def test_the_person_who_promoted_to_test_cant_promote_to_prod(svc, monkeypatch):
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["prod"]["separate_promoter"] = True
    svc._write_environments(project, envs)
    monkeypatch.setattr(duties, "current_user", lambda: "pat")
    monkeypatch.setattr("fabric_desktop.meta.release._user", lambda: "pat")
    svc.meta_promote("dev", "test")
    check = svc.meta_promote_check("test", "prod")
    assert any("You promoted metadata" in e and "someone else" in e for e in check["errors"])
    with pytest.raises(GateError, match="someone else"):
        svc.meta_promote("test", "prod")
    monkeypatch.setattr(duties, "current_user", lambda: "ann")
    assert not any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])


class _Fabric:
    def __init__(self, assignments):
        self.assignments = assignments

    def list_role_assignments(self, workspace_id):
        if isinstance(self.assignments, Exception):
            raise self.assignments
        return self.assignments


class _Auth:
    def signed_in_object_id(self):
        return ME

    def signed_in_user(self):
        return "pat@contoso.com"


def test_promote_checks_roles_in_every_target_workspace(svc, monkeypatch):
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["prod"] = {"target": "onelake", "workspace_id": "ws-prod", "workspace_name": "Sales - Prod",
                    "workspaces": {"gold": {"id": "ws-gold", "name": "Sales Gold - Prod"}}}
    svc._write_environments(project, envs)
    svc._auth = _Auth()
    fabric = _Fabric([assignment(ME, "Viewer"), ADMIN])
    monkeypatch.setattr(Service, "fabric", property(lambda s: fabric))
    errors, notes = svc._duties_gate(svc._project(), "test", "prod")
    assert [e.split(";")[0] for e in errors] == ["You're a Viewer in Prod workspace Sales - Prod",
                                                 "You're a Viewer in Prod workspace Sales Gold - Prod"]
    fabric.assignments = PermissionError("403")
    errors, notes = svc._duties_gate(svc._project(), "test", "prod")
    assert errors == [] and len(notes) == 2
    assert svc._duties_gate(svc._project(), "dev", "test") == ([], [])  # Test is on this PC


def test_the_organizations_policy_requires_a_separate_promoter_whatever_the_project_says(svc, monkeypatch, tmp_path):
    policy = tmp_path / "policy.json"
    monkeypatch.setattr(app_settings, "policy_path", lambda: policy)
    monkeypatch.setattr(duties, "current_user", lambda: "pat")
    monkeypatch.setattr("fabric_desktop.meta.release._user", lambda: "pat")
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["prod"]["separate_promoter"] = False  # the project turns it off ...
    svc._write_environments(project, envs)
    svc.meta_promote("dev", "test")
    assert not any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])
    policy.write_text(json.dumps({"separate_promoter_envs": ["Prod"]}))  # ... the organization doesn't
    errors = svc.meta_promote_check("test", "prod")["errors"]
    assert any("someone else" in e and "your organization's policy" in e for e in errors)
    with pytest.raises(GateError, match="someone else"):
        svc.meta_promote("test", "prod")
    monkeypatch.setattr(duties, "current_user", lambda: "ann")
    assert not any("someone else" in e for e in svc.meta_promote_check("test", "prod")["errors"])
