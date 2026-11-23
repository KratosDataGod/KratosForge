"""DevOps controls (FEA-305): pass, fail and not-evaluated fixtures for every control; a Git read
that fails or is refused leaves them not evaluated, never passed; the engine call makes only the Git
reads the app already makes and never writes; the demo calls nothing; the evidence keeps no
repository, URL, commit or item detail."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.excellence import assess, catalog, catalog_devops as dv, controls as ct
from fabric_desktop.excellence_service import FIXTURE_GIT_CONNECTION, FIXTURE_GIT_STATUS
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service

AT = "2026-11-19T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
WS = {"id": WSID, "displayName": "Sales Prod"}
REPO_URL = "https://ghp_abc123secretvalue@github.com/contoso/sales.git"  # pragma: allowlist secret (made up: must never reach evidence)
DEVOPS = ("FEA-DEVOPS-003", "FEA-DEVOPS-004", "FEA-DEVOPS-005")


def conn(state: str = "ConnectedAndInitialized", provider: str = "GitHub", branch: str = "main") -> dict:
    return {"gitConnectionState": state,
            "gitProviderDetails": {"gitProviderType": provider, "ownerName": "contoso", "repositoryName": "sales",
                                   "branchName": branch, "directoryName": "/fabric", "url": REPO_URL},
            "gitSyncDetails": {"head": "a" * 40, "lastSyncTime": "2026-11-18T00:00:00Z"}}


def change(ws: str | None, remote: str | None, conflict: str = "None", name: str = "nb_secret_plan") -> dict:
    return {"itemMetadata": {"itemIdentifier": {"objectId": "99999999-0000-4000-8000-000000000001"},
                             "itemType": "Notebook", "displayName": name},
            "workspaceChange": ws, "remoteChange": remote, "conflictType": conflict}


def status(*changes: dict) -> dict:
    return {"workspaceHead": "a" * 40, "remoteCommitHash": "b" * 40, "changes": list(changes)}


def run(connection: dict | None, st: dict | None = None) -> dict:
    records = dv.evidence(WS, connection, st, collected_at=AT)
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid]
    assert len(got) == 1, got
    return got[0]


def test_devops_controls_are_registered_and_complete():
    r = catalog.registry()
    ids = [c.id for c in r.controls("devops")]
    assert ids == ["FEA-DEVOPS-001", "FEA-DEVOPS-002", *DEVOPS]
    for c in dv.CONTROLS:
        assert ct.problems(c) == [] and c.scope == "workspace" and c.evidence_kinds == (dv.KIND,)
        assert any(ref["url"].startswith("https://learn.microsoft.com/") for ref in c.references)
        assert "severity" in (c.evaluate.__doc__ or "").lower()
        assert c.accepts_inferred is False
    assert {c.id: c.severity for c in dv.CONTROLS} == {"FEA-DEVOPS-003": "medium", "FEA-DEVOPS-004": "low",
                                                       "FEA-DEVOPS-005": "low"}


# ---- FEA-DEVOPS-003 connected to Git


def test_connected_pass_fail_and_not_evaluated():
    ok = outcome_of(run(conn(), status()), "FEA-DEVOPS-003")
    assert ok["outcome"] == "pass" and ok["basis"] == "verified_configuration" and "GitHub branch main" in ok["text"]
    no = outcome_of(run({"gitConnectionState": "NotConnected"}), "FEA-DEVOPS-003")
    assert no["outcome"] == "fail" and "isn't connected" in no["text"]
    never = outcome_of(run(conn("Connected", "AzureDevOps", "dev")), "FEA-DEVOPS-003")
    assert never["outcome"] == "fail" and "Azure DevOps branch dev" in never["text"] and "first sync" in never["text"]
    odd = outcome_of(run({"gitConnectionState": "Sideways"}), "FEA-DEVOPS-003")
    assert odd["outcome"] == "not_evaluated"
    unread = outcome_of(run(None), "FEA-DEVOPS-003")
    assert unread["outcome"] == "not_evaluated" and unread["needs"] == dv.KIND


# ---- FEA-DEVOPS-004 uncommitted changes


def test_uncommitted_pass_fail_and_not_evaluated():
    clean = outcome_of(run(conn(), status(change(None, "Modified"), change("Modified", "Modified", "SameChanges"))),
                       "FEA-DEVOPS-004")
    assert clean["outcome"] == "pass" and clean["basis"] == "verified_configuration"
    dirty = outcome_of(run(conn(), status(change("Modified", None), change("Added", None),
                                          change("Modified", "Deleted", "Conflict"))), "FEA-DEVOPS-004")
    assert dirty["outcome"] == "fail" and dirty["text"].startswith("3 items have")
    assert "1 of them in conflict" in dirty["text"] and dirty["detail"] == {"uncommitted": 3, "conflicts": 1}
    unread = outcome_of(run(conn(), None), "FEA-DEVOPS-004")
    assert unread["outcome"] == "not_evaluated" and "GitUpdate" in unread["needs"]
    never = outcome_of(run(conn("Connected")), "FEA-DEVOPS-004")
    assert never["outcome"] == "not_evaluated" and "first sync" in never["text"]
    off = outcome_of(run({"gitConnectionState": "NotConnected"}, status(change("Modified", None))), "FEA-DEVOPS-004")
    assert off["outcome"] == "not_applicable" and "FEA-DEVOPS-003" in off["text"]
    assert outcome_of(run(None), "FEA-DEVOPS-004")["outcome"] == "not_evaluated"


# ---- FEA-DEVOPS-005 behind the branch


def test_behind_pass_fail_and_not_evaluated():
    level = outcome_of(run(conn(), status(change("Modified", None))), "FEA-DEVOPS-005")
    assert level["outcome"] == "pass"
    behind = outcome_of(run(conn(), status(change(None, "Modified"))), "FEA-DEVOPS-005")
    assert behind["outcome"] == "fail" and behind["text"].startswith("1 item has") and "conflict" not in behind["text"]
    assert behind["detail"] == {"incoming": 1, "conflicts": 0}
    assert outcome_of(run(conn(), None), "FEA-DEVOPS-005")["outcome"] == "not_evaluated"
    assert outcome_of(run({"gitConnectionState": "NotConnected"}), "FEA-DEVOPS-005")["outcome"] == "not_applicable"
    assert outcome_of(run(None), "FEA-DEVOPS-005")["outcome"] == "not_evaluated"


def test_both_changed_counts_on_both_sides():
    snap = run(conn(), status(change("Modified", "Modified")))
    assert outcome_of(snap, "FEA-DEVOPS-004")["outcome"] == "fail"
    assert outcome_of(snap, "FEA-DEVOPS-005")["outcome"] == "fail"


# ---- the evidence


def test_evidence_keeps_only_provider_branch_state_and_counts():
    st = status(change("Modified", None, name="nb_with_token=ghp_zzz"))  # pragma: allowlist secret (made up)
    recs = dv.evidence(WS, conn(branch="feature/" + "x" * 400), st, collected_at=AT)
    assert len(recs) == 1 and recs[0]["kind"] == dv.KIND and recs[0]["confidence"] == "verified_configuration"
    p = recs[0]["payload"]
    assert set(p) == {"state", "provider", "branch", "status"}
    assert p["status"] == {"uncommitted": 1, "incoming": 0, "conflicts": 0}
    assert len(p["branch"]) == dv.MAX_TEXT and p["provider"] == "GitHub"
    text = json.dumps(recs)
    for leaked in ("ghp_", "contoso", "sales", "/fabric", "a" * 40, "b" * 40, "nb_with_token", "99999999", "url"):
        assert leaked not in text, leaked


def test_a_branch_with_control_characters_is_dropped():
    p = dv.evidence(WS, conn(branch="main\nX-Injected: 1"), None, collected_at=AT)[0]["payload"]
    assert p["branch"] == ""


def test_not_connected_keeps_no_provider_details():
    raw = {"gitConnectionState": "NotConnected", "gitProviderDetails": {"gitProviderType": "GitHub", "branchName": "x"}}
    p = dv.evidence(WS, raw, status(change("Modified", None)), collected_at=AT)[0]["payload"]
    assert p == {"state": "NotConnected", "provider": "", "branch": "", "status": None}


# ---- the engine call


class FakeFabric:
    def __init__(self, connection=None, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.connection = connection if connection is not None else conn()
        self.error = error

    def get_workspace(self, w):
        self.calls.append(("get_workspace", w))
        return {"id": w, "displayName": "Sales Prod", "description": "d"}

    def list_items(self, w):
        self.calls.append(("list_items", w))
        return []

    def list_role_assignments(self, w):
        self.calls.append(("list_role_assignments", w))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []

    def git_connection(self, w):
        self.calls.append(("git_connection", w))
        if self.error:
            raise self.error
        return self.connection

    def git_update_from_git(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("the assessment wrote to Fabric")


class FakeStatusClient:
    def __init__(self, answer=None, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.answer, self.error = answer if answer is not None else status(change("Modified", None)), error

    def git_status(self, w):
        self.calls.append(("git_status", w))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(status_client=None, **kw):
        fake = FakeFabric(**kw)
        asked: list[int] = []

        def client(self):
            asked.append(1)
            return status_client

        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        monkeypatch.setattr(Service, "_git_status_client", client)
        s = Service(root=tmp_path / "app")
        s.fake, s.asked = fake, asked
        return s
    return build


def read_of(r: dict, name: str) -> dict:
    got = [x for x in r["reads"] if x["read"] == name]
    assert len(got) == 1, r["reads"]
    return got[0]


def test_the_call_reads_git_connection_and_status_only(make):
    sc = FakeStatusClient()
    s = make(status_client=sc)
    r = s.excellence_assess(workspace_id=WSID)
    assert {c[0] for c in s.fake.calls} <= {"get_workspace", "list_items", "list_role_assignments", "list_capacities",
                                            "git_connection"}
    assert sc.calls == [("git_status", WSID)]
    assert outcome_of(r, "FEA-DEVOPS-003")["outcome"] == "pass"
    assert outcome_of(r, "FEA-DEVOPS-004")["outcome"] == "fail"
    assert outcome_of(r, "FEA-DEVOPS-005")["outcome"] == "pass"
    assert read_of(r, "Git connection")["ok"] is True and read_of(r, "Git status")["ok"] is True
    assert r["scores"]["dimensions"]["devops"]["status"] != "not_applicable"


def test_a_failed_connection_read_never_passes(make):
    s = make(error=RuntimeError("timeout token=abc.def.ghi"), status_client=FakeStatusClient())
    r = s.excellence_assess(workspace_id=WSID)
    for cid in DEVOPS:
        assert outcome_of(r, cid)["outcome"] == "not_evaluated"
    got = read_of(r, "Git connection")
    assert got["ok"] is False and "abc.def.ghi" not in json.dumps(r)
    assert s.asked == []  # no status client is even built


def test_a_role_that_cant_see_git_is_listed_not_passed(make):
    s = make(error=FabricError("You don't have permission", "InsufficientPrivileges", 403))
    r = s.excellence_assess(workspace_id=WSID)
    for cid in DEVOPS:
        assert outcome_of(r, cid)["outcome"] == "not_evaluated"
    got = read_of(r, "Git connection")
    assert got["ok"] is False and "workspace role" in got["problem"]
    assert not any(x["read"] == "Git status" for x in r["reads"])


def test_without_the_status_permission_the_status_controls_are_not_evaluated(make):
    s = make(status_client=None)
    r = s.excellence_assess(workspace_id=WSID)
    assert outcome_of(r, "FEA-DEVOPS-003")["outcome"] == "pass"
    for cid in ("FEA-DEVOPS-004", "FEA-DEVOPS-005"):
        assert outcome_of(r, cid)["outcome"] == "not_evaluated"
    got = read_of(r, "Git status")
    assert got["ok"] is False and "Workspace.GitUpdate.All" in got["problem"] and "never asks" in got["problem"]


def test_a_failed_or_refused_status_read_is_listed(make):
    for err in (RuntimeError("boom token=abc.def.ghi"), FabricError("Forbidden", "InsufficientScopes", 403)):
        s = make(status_client=FakeStatusClient(error=err))
        r = s.excellence_assess(workspace_id=WSID)
        assert read_of(r, "Git status")["ok"] is False
        assert outcome_of(r, "FEA-DEVOPS-004")["outcome"] == "not_evaluated"
        assert outcome_of(r, "FEA-DEVOPS-005")["outcome"] == "not_evaluated"
        assert "abc.def.ghi" not in json.dumps(r)


def test_a_workspace_not_in_git_fails_and_reads_no_status(make):
    sc = FakeStatusClient()
    s = make(connection={"gitConnectionState": "NotConnected"}, status_client=sc)
    r = s.excellence_assess(workspace_id=WSID)
    assert outcome_of(r, "FEA-DEVOPS-003")["outcome"] == "fail"
    assert outcome_of(r, "FEA-DEVOPS-004")["outcome"] == "not_applicable"
    assert outcome_of(r, "FEA-DEVOPS-005")["outcome"] == "not_applicable"
    assert sc.calls == [] and s.asked == []
    assert "FEA-DEVOPS-003" in {f["rule"].split("@")[0] for f in r["findings"]}


def test_the_demo_shows_devops_without_calling_fabric(make):
    sc = FakeStatusClient()
    s = make(status_client=sc)
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and sc.calls == [] and s.asked == []
    assert r["fixture"] is True and r["fixture_note"]
    assert outcome_of(r, "FEA-DEVOPS-003")["outcome"] == "pass"
    rules = {f["rule"].split("@")[0] for f in r["findings"]}
    assert {"FEA-DEVOPS-004", "FEA-DEVOPS-005"} <= rules and all(f["fixture"] for f in r["findings"])
    text = json.dumps([e for e in r["evidence"] if e["kind"] == dv.KIND])
    assert "demo-repo" not in text and "demo-owner" not in text and "nb_load_orders" not in text
    assert FIXTURE_GIT_CONNECTION["gitConnectionState"] == "ConnectedAndInitialized" and FIXTURE_GIT_STATUS["changes"]
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert again["scores"] == r["scores"]


@pytest.mark.parametrize("answer", [{"changes": "x"}, {}, {"changes": None}, {"changes": [1, "a"]},
                                    {"changes": [{"workspaceChange": "Modified"}, None]}, ["changes"], "ok"])
def test_a_malformed_status_answer_is_listed_and_never_passes(make, answer):
    s = make(status_client=FakeStatusClient(answer=answer))
    r = s.excellence_assess(workspace_id=WSID)
    got = read_of(r, "Git status")
    assert got["ok"] is False and "expected shape" in got["problem"]
    assert outcome_of(r, "FEA-DEVOPS-003")["outcome"] == "pass"
    for cid in ("FEA-DEVOPS-004", "FEA-DEVOPS-005"):
        assert outcome_of(r, cid)["outcome"] == "not_evaluated"


def test_an_empty_change_list_is_a_clean_status():
    assert dv.status_readable({"changes": []})
    assert not dv.status_readable({"changes": "x"}) and not dv.status_readable(None)
