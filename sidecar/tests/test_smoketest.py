"""The ten-flow tenant smoke test (PB-002), with sign-in, Fabric and the live test faked."""

import json
import zipfile

import pytest

from fabric_desktop import cli, smoketest
from fabric_desktop.fabric_api import FabricClient

WS = "11111111-1111-1111-1111-111111111111"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


class Credential:
    def __init__(self, kusto=True):
        self.kusto = kusto

    def fabric_token(self):
        return TOKEN

    def storage_token(self):
        return TOKEN

    def warehouse_token(self):
        return TOKEN

    def kusto_token(self):
        if not self.kusto:
            raise RuntimeError(f"AADSTS65001: consent needed, Bearer {TOKEN}")
        return TOKEN

    def signed_in_user(self):
        return "chris@contoso.com"

    def signed_in_tenant(self):
        return "22222222-2222-2222-2222-222222222222"


class Fabric:
    def __init__(self, workspaces=None):
        self.workspaces = workspaces if workspaces is not None else [{"id": WS.upper()}, {"id": "other"}]
        self.created = []

    def list_workspaces(self):
        return self.workspaces

    def list_items(self, ws):
        return [{"type": "Lakehouse"}, {"type": "Notebook"}, {"type": "Lakehouse"}]

    def create_lakehouse(self, ws, name, description=""):
        return FabricClient.create_lakehouse(self, ws, name, description)  # the real name check

    def _request(self, *args, **kwargs):
        self.created.append(args)
        return {"id": "lh-made"}

    def _wait(self, resp):
        return resp


class Live:
    def __init__(self, outcome="passed", checks=None, left_behind=None, refused=None):
        self.result = {"run_id": "live1", "outcome": outcome, "refused": refused or [], "left_behind": left_behind or [],
                       "workspace": {"id": WS, "name": "Live tests"}, "files": [],
                       "checks": checks if checks is not None else [
                           {"id": "fabric.create_lakehouse", "title": "Create a lakehouse", "status": "passed", "detail": ""},
                           {"id": "delta.merge", "title": "Merge", "status": "passed", "detail": ""},
                           {"id": "notebook.publish", "title": "Publish", "status": "passed", "detail": ""},
                           {"id": "notebook.run", "title": "Run in Fabric", "status": "skipped",
                            "detail": "skipped: needs --run-in-fabric"},
                           {"id": "cleanup.lakehouse", "title": "Delete the lakehouse", "status": "passed", "detail": ""}]}
        self.out = None

    def __call__(self, out):
        self.out = out
        return self

    def run(self):
        return self.result


def clean_bundle(out):
    path = out / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("about.json", json.dumps({"version": "1.0", "user": "[user]"}))
    return path


def smoke(tmp_path, credential=None, fabric=None, live=None, bundle=clean_bundle, client_id=""):
    return smoketest.SmokeTest(WS, credential or Credential(), fabric or Fabric(), tmp_path / "out",
                               live_test=live if live is not None else Live(), support_bundle=bundle,
                               run_id="smoke1", client_id=client_id)


def flows(result):
    return {f["n"]: f["automated"] for f in result["flows"]}


def test_every_automated_step_passes_and_each_flow_says_what_is_left_by_hand(tmp_path):
    live = Live()
    result = smoke(tmp_path, live=live).run()
    assert result["outcome"] == "passed", result["steps"]
    assert flows(result) == {1: "passed", 2: "passed", 3: "passed", 4: "passed", 5: "passed", 6: "passed",
                             7: "none", 8: "none", 9: "passed", 10: "passed"}
    assert all(f["by_hand"] for f in result["flows"])
    assert live.out == tmp_path / "out" / "smoke1" / "live"
    steps = {s["id"]: s for s in result["steps"]}
    assert steps["live.delta.merge"]["flow"] == 5 and steps["live.notebook.publish"]["flow"] == 6
    assert steps["live.cleanup.lakehouse"]["flow"] == 9
    saved = (tmp_path / "out" / "smoke1" / "smoke.json").read_text()
    assert json.loads(saved)["schema"] == "smoke-test-result@1"
    text = (tmp_path / "out" / "smoke1" / "summary.md").read_text()
    assert "Outcome of the automated parts: **passed**" in text
    assert "- [ ] Data mesh > Environments: run the Dev environment on This PC." in text


def test_the_invalid_lakehouse_name_is_refused_without_calling_fabric(tmp_path):
    fabric = Fabric()
    result = smoke(tmp_path, fabric=fabric).run()
    step = next(s for s in result["steps"] if s["id"] == "lakehouse.bad_name")
    assert step["status"] == "passed" and "doesn't accept the lakehouse name" in step["detail"]
    assert fabric.created == []


def test_the_invalid_name_step_sends_no_request(tmp_path, monkeypatch):
    # Flow 4 must not reach Fabric even when the client's own check regresses: nothing it made is in the ledger.
    calls = []
    fabric = Fabric()
    monkeypatch.setattr(fabric, "create_lakehouse", lambda *a, **k: calls.append(a), raising=False)
    step = smoke(tmp_path, fabric=fabric)._bad_name()
    assert calls == [] and step.startswith("Refused:")


def test_a_refused_kusto_token_is_recorded_without_failing_sign_in(tmp_path):
    result = smoke(tmp_path, credential=Credential(kusto=False)).run()
    step = next(s for s in result["steps"] if s["id"] == "auth.kusto")
    assert step["status"] == "failed" and not step["required"] and "AADSTS65001" in step["detail"]
    assert flows(result)[2] == "passed"
    assert TOKEN not in (tmp_path / "out" / "smoke1" / "smoke.json").read_text()
    assert "(recorded, doesn't fail the flow)" in (tmp_path / "out" / "smoke1" / "summary.md").read_text()


def test_an_own_app_registration_fails_the_sign_in_flow(tmp_path):
    result = smoke(tmp_path, client_id="my-app").run()
    assert flows(result)[2] == "failed"
    assert result["outcome"] == "failed"


def test_microsofts_own_client_id_passes_the_sign_in_flow(tmp_path):
    from fabric_desktop.auth import MICROSOFT_CLIENT_ID
    assert flows(smoke(tmp_path, client_id=MICROSOFT_CLIENT_ID).run())[2] == "passed"


def test_a_missing_test_workspace_fails_the_catalog_flow(tmp_path):
    result = smoke(tmp_path, fabric=Fabric(workspaces=[{"id": "other"}])).run()
    assert flows(result)[3] == "failed"


def test_a_failed_live_check_fails_its_own_flow_and_the_live_test_flow(tmp_path):
    live = Live(outcome="failed", checks=[
        {"id": "delta.write", "title": "Write a Delta table", "status": "failed", "detail": "403"},
        {"id": "cleanup.lakehouse", "title": "Delete the lakehouse", "status": "passed", "detail": ""}])
    result = smoke(tmp_path, live=live).run()
    assert flows(result)[5] == "failed" and flows(result)[9] == "failed"
    assert flows(result)[6] == "none"  # nothing ran for it, so nothing is claimed


def test_a_refused_workspace_is_reported_and_left_behind_items_fail_the_run(tmp_path):
    refused = smoke(tmp_path, live=Live(outcome="refused", checks=[], refused=["The workspace name looks like production."])).run()
    step = next(s for s in refused["steps"] if s["id"] == "live.outcome")
    assert step["status"] == "failed" and "looks like production" in step["detail"]
    left = smoke(tmp_path, live=Live(left_behind=[{"kind": "lakehouse", "name": "fdlt_x", "id": "1"}])).run()
    assert flows(left)[9] == "failed"
    assert "Left behind" in (tmp_path / "out" / "smoke1" / "summary.md").read_text()


def test_a_support_bundle_with_a_user_name_or_tenant_fails_flow_10(tmp_path):
    def leaky(out):
        path = out / "bundle.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("sidecar.log", "signed in as Chris@Contoso.com in 22222222-2222-2222-2222-222222222222")
        return path
    result = smoke(tmp_path, bundle=leaky).run()
    step = next(s for s in result["steps"] if s["id"] == "support.bundle")
    assert step["status"] == "failed"
    assert "the signed-in user" in step["detail"] and "the tenant id" in step["detail"]


def test_the_real_support_bundle_passes_the_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    result = smoke(tmp_path, bundle=smoketest._support_bundle).run()
    step = next(s for s in result["steps"] if s["id"] == "support.bundle")
    assert step["status"] == "passed", step


def test_no_fabric_token_means_no_workspace_calls_and_no_live_test(tmp_path):
    class NoToken(Credential):
        def fabric_token(self):
            raise RuntimeError("sign-in cancelled")
    live = Live()
    result = smoke(tmp_path, credential=NoToken(), live=live).run()
    assert live.out is None
    assert flows(result)[2] == "failed" and flows(result)[3] == "none"


def test_every_flow_has_features_and_a_by_hand_step():
    assert [f.n for f in smoketest.FLOWS] == list(range(1, 11))
    assert all(f.features and f.by_hand for f in smoketest.FLOWS)


def test_dry_run_lists_both_plans_and_signs_in_to_nothing(tmp_path, monkeypatch, capsys):
    def no_sign_in(*args, **kwargs):
        raise AssertionError("signed in during a dry run")
    monkeypatch.setattr("fabric_desktop.auth.Authenticator", no_sign_in)
    monkeypatch.setattr("sys.argv", ["fabric-desktop", "smoke-test", "--workspace", WS, "--dry-run",
                                     "--out", str(tmp_path / "out")])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "Flow 10: save a support bundle" in out and "Opt-in delta.concurrent" in out
    assert "Dry run: nothing was signed in to, created or written." in out
    assert not (tmp_path / "out").exists()


def test_the_cli_dry_run_prints_the_plan_and_writes_nothing(tmp_path, capsys):
    with pytest.raises(SystemExit) as done:
        cli.main(["smoke-test", "--workspace", WS, "--dry-run", "--out", str(tmp_path / "res")])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "Flow 4:" in out and "without --run-in-fabric" in out
    assert not (tmp_path / "res").exists()
    with pytest.raises(SystemExit) as refused:
        cli.main(["smoke-test", "--dry-run"])  # --workspace is required
    assert refused.value.code == 2
