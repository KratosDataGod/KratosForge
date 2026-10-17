"""The live test harness (FD-101), with Fabric and OneLake faked and Delta on local disk."""

import json

import pytest
import requests

from fabric_desktop import cli, livetest
from fabric_desktop.fabric_api import FabricError

WS = "11111111-1111-1111-1111-111111111111"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


class Credential:
    name = "test user"

    def fabric_token(self):
        return TOKEN

    def storage_token(self):
        return TOKEN

    def signed_in_tenant(self):
        return "tenant-1"


class Fabric:
    def __init__(self, workspace=None, fail_delete=False):
        self.workspace = workspace or {"id": WS, "displayName": "Live tests", "description": "fabric-desktop-live-test"}
        self.lakehouses = {"existing": {"id": "existing", "displayName": "Sales"}}
        self.fail_delete = fail_delete
        self.calls = []

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return self.workspace

    def create_lakehouse(self, ws, name, description=""):
        self.calls.append(("create_lakehouse", name))
        lh = {"id": f"lh-{len(self.lakehouses)}", "displayName": name,
              "properties": {"oneLakeTablesPath": "https://x/Tables", "oneLakeFilesPath": "https://x/Files"}}
        self.lakehouses[lh["id"]] = lh
        return lh

    def get_lakehouse(self, ws, lh):
        if lh not in self.lakehouses:
            raise FabricError("not found", "ItemNotFound")
        return self.lakehouses[lh]

    def delete_lakehouse(self, ws, lh):
        self.calls.append(("delete_lakehouse", lh))
        if self.fail_delete:
            raise FabricError("DELETE failed with 500", "InternalError")
        self.lakehouses.pop(lh, None)


class Files:
    def __init__(self, fail_upload=False):
        self.data = {}
        self.fail_upload = fail_upload

    def upload(self, ws, lh, path, body):
        if self.fail_upload:
            raise RuntimeError(f"upload failed, Authorization: Bearer {TOKEN}")
        self.data[(lh, path)] = body

    def list(self, ws, lh, folder):
        return [{"name": p.rsplit("/", 1)[-1]} for (l, p) in self.data if l == lh and p.startswith(folder + "/")]

    def read_bytes(self, ws, lh, path):
        return self.data.get((lh, path))

    def delete(self, ws, lh, folder):
        for key in [k for k in self.data if k[0] == lh and k[1].startswith(folder)]:
            del self.data[key]


class BadTokenFiles:
    def list(self, ws, lh, folder):
        resp = requests.Response()
        resp.status_code = 401
        raise requests.HTTPError("401", response=resp)


def harness(tmp_path, fabric=None, files=None, protected=None):
    return livetest.LiveTest(WS, fabric or Fabric(), files or Files(), Credential(), tmp_path / "out",
                             table_access=lambda ref: (str(tmp_path / "delta" / ref.lakehouse_id / ref.table), {}),
                             protected_ids=protected, bad_token_files=BadTokenFiles(), run_id="run1")


def test_every_check_passes_and_everything_created_is_deleted(tmp_path):
    fabric = Fabric()
    result = harness(tmp_path, fabric).run()
    statuses = {c["id"]: c["status"] for c in result["checks"]}
    assert result["outcome"] == "passed", result["checks"]
    assert set(statuses.values()) == {"passed"}
    assert {"auth.fabric", "onelake.files", "delta.merge", "delta.time_travel", "errors.not_found",
            "errors.bad_token", "cleanup.lakehouse"} <= set(statuses)
    assert ("create_lakehouse", "fdlt_run1") in fabric.calls
    assert set(fabric.lakehouses) == {"existing"}  # the harness's lakehouse is gone, the other untouched
    assert result["left_behind"] == []
    saved = (tmp_path / "out" / "run1" / "result.json").read_text()
    assert json.loads(saved)["schema"] == "live-test-result@1"
    assert TOKEN not in saved
    assert "Outcome: **passed**" in (tmp_path / "out" / "run1" / "summary.md").read_text()


@pytest.mark.parametrize("workspace, reason", [
    ({"id": WS, "displayName": "Live tests", "description": "team sandbox"}, "doesn't contain"),
    ({"id": WS, "displayName": "Sales PROD", "description": "fabric-desktop-live-test"}, "looks like production"),
    ({"id": WS, "displayName": "sales-prd", "description": "fabric-desktop-live-test"}, "looks like production"),
    ({"id": WS, "displayName": "Production", "description": "fabric-desktop-live-test"}, "looks like production"),
])
def test_refuses_a_workspace_not_marked_for_tests_or_that_looks_like_production(tmp_path, workspace, reason):
    fabric = Fabric(workspace)
    result = harness(tmp_path, fabric).run()
    assert result["outcome"] == "refused"
    assert any(reason in r for r in result["refused"])
    assert not [c for c in fabric.calls if c[0] != "get_workspace"]  # nothing created or deleted


def test_product_in_a_name_is_not_production():
    ws = {"id": WS, "displayName": "Product analytics", "description": "fabric-desktop-live-test"}
    assert livetest.refusals(ws, set()) == []


def test_refuses_a_platform_workspace_named_by_policy(tmp_path):
    result = harness(tmp_path, protected={WS.upper()}).run()
    assert result["outcome"] == "refused"
    assert "Platform workspace" in result["refused"][0]


def test_policy_platform_workspaces_are_protected(tmp_path, monkeypatch):
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"platform": {"prod": {"workspace_id": "ABC"}, "dev": {"workspace_id": "def"}}}))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    assert livetest.policy_workspace_ids() == {"abc", "def"}


def test_a_failure_midway_still_cleans_up_and_keeps_tokens_out(tmp_path):
    fabric = Fabric()
    result = harness(tmp_path, fabric, Files(fail_upload=True)).run()
    files = next(c for c in result["checks"] if c["id"] == "onelake.files")
    assert result["outcome"] == "failed"
    assert files["status"] == "failed" and TOKEN not in files["detail"] and "[removed]" in files["detail"]
    assert set(fabric.lakehouses) == {"existing"}
    assert result["left_behind"] == []


def test_a_failed_cleanup_is_reported_and_can_be_finished_from_the_ledger(tmp_path):
    fabric = Fabric(fail_delete=True)
    result = harness(tmp_path, fabric).run()
    assert result["outcome"] == "failed"
    assert [r["name"] for r in result["left_behind"]] == ["fdlt_run1"]
    assert "Left behind" in (tmp_path / "out" / "run1" / "summary.md").read_text()

    fabric.fail_delete = False
    done = livetest.cleanup_from_ledger(tmp_path / "out" / "run1" / "ledger.json", fabric, Credential())
    assert done["left_behind"] == []
    assert set(fabric.lakehouses) == {"existing"}


def test_cleanup_only_deletes_what_the_harness_named_in_this_workspace(tmp_path):
    fabric = Fabric()
    ledger = livetest.Ledger(tmp_path / "ledger.json", "run1", WS, [
        {"kind": "lakehouse", "workspace_id": WS, "id": "existing", "name": "Sales"},
        {"kind": "lakehouse", "workspace_id": "other", "id": "existing", "name": "fdlt_x"},
    ])
    ledger.save()
    livetest.cleanup_from_ledger(tmp_path / "ledger.json", fabric, Credential())
    assert not [c for c in fabric.calls if c[0] == "delete_lakehouse"]


def test_no_sign_in_means_no_workspace_calls(tmp_path):
    class NoToken(Credential):
        def fabric_token(self):
            raise RuntimeError("sign-in cancelled")
    fabric = Fabric()
    test = livetest.LiveTest(WS, fabric, Files(), NoToken(), tmp_path, bad_token_files=BadTokenFiles(), run_id="r")
    result = test.run()
    assert result["outcome"] == "failed"
    assert fabric.calls == []


def test_dry_run_lists_the_plan_and_writes_nothing(tmp_path, monkeypatch, capsys):
    def no_sign_in(*a, **k):
        raise AssertionError("a dry run must not sign in")
    monkeypatch.setattr("fabric_desktop.auth.Authenticator", no_sign_in)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        cli.main(["live-test", "--workspace", WS, "--dry-run"])
    assert exit.value.code == 0
    out = capsys.readouterr().out
    assert "fdlt_" in out and "Dry run" in out
    assert list(tmp_path.iterdir()) == []


def test_delete_lakehouse_calls_the_documented_endpoint():
    from fabric_desktop.fabric_api import FabricClient

    class Resp:
        status_code, content, headers, text = 200, b"", {}, ""

    class Session:
        def request(self, method, url, **kwargs):
            self.call = (method, url)
            return Resp()
    session = Session()
    FabricClient(lambda: "t", session=session).delete_lakehouse("ws1", "lh1")
    assert session.call == ("DELETE", "https://api.fabric.microsoft.com/v1/workspaces/ws1/lakehouses/lh1")
