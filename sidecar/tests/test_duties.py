"""FD-130: workspace roles before a promote writes to Test or Prod, and a separate promoter for Prod."""

import pytest

from fabric_desktop import duties
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
