"""The live test harness (FD-101), with Fabric and OneLake faked and Delta on local disk."""

import json

import pytest
import requests

from fabric_desktop import cli, leases, livetest
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


# ---- opt-in checks (KF-DE-005)

import base64  # noqa: E402

from deltalake import write_deltalake  # noqa: E402


def _b64(text):
    return base64.b64encode(text.encode()).decode()


class ItemFabric(Fabric):
    """Fabric with notebook and pipeline items and the job scheduler."""

    def __init__(self, workspace=None, fail_delete_item=False, change_cells=False, bad_pipeline=False,
                 job_status="Completed", existing_items=()):
        super().__init__(workspace)
        self.items = {i["id"]: i for i in existing_items}
        self.fail_delete_item, self.change_cells, self.bad_pipeline = fail_delete_item, change_cells, bad_pipeline
        self.job_status = job_status

    def list_items(self, ws):
        return [{k: v for k, v in i.items() if k != "parts"} for i in self.items.values()]

    def find_item(self, ws, item_type, name):
        return next((i for i in self.list_items(ws) if i["type"] == item_type and i["displayName"] == name), None)

    def list_notebooks(self, ws):
        return [i for i in self.list_items(ws) if i["type"] == "Notebook"]

    def _create(self, item_type, name, parts):
        item = {"id": f"item-{len(self.items)}", "displayName": name, "type": item_type, "parts": parts}
        self.items[item["id"]] = item
        self.calls.append(("create_item", item_type, name))
        return item

    def save_notebook(self, ws, name, ipynb):
        nb = json.loads(ipynb)
        if self.change_cells:
            nb["cells"][1]["source"] = ["print('changed')"]
        self._create("Notebook", name, {"notebook-content.ipynb": json.dumps(nb)})
        return {}  # a create can come back without the item

    def save_item(self, ws, item_type, name, parts=None, description=""):
        if self.bad_pipeline:
            parts = {"pipeline-content.json": '{"properties": {"activities": "not a list"}}'}
        item = self._create(item_type, name, dict(parts or {}))
        return {k: v for k, v in item.items() if k != "parts"} | {"created": True}

    def get_item_definition(self, ws, item_id, fmt=None):
        parts = self.items[item_id]["parts"]
        return {"definition": {"parts": [{"path": p, "payload": _b64(t), "payloadType": "InlineBase64"}
                                         for p, t in parts.items()]}}

    def delete_item(self, ws, item_id):
        self.calls.append(("delete_item", item_id))
        if self.fail_delete_item:
            raise FabricError("DELETE failed with 500", "InternalError")
        self.items.pop(item_id, None)

    def run_item_job(self, ws, item_id, job_type):
        self.calls.append(("run_item_job", item_id, job_type))
        return "job-1"

    def job_instance(self, ws, item_id, job_id):
        return {"status": self.job_status, "failureReason": {"message": "Spark said no"}}

    def cancel_job(self, ws, item_id, job_id):
        self.calls.append(("cancel_job", job_id))


def local_writer(tmp_path, fail_once=None, drop=None):
    """The write path, writing to local disk; can lose one commit race (fail_once) or drop a writer's rows."""
    failed = set()

    def write(ref, data, mode):
        who = data.column("writer")[0].as_py() if "writer" in data.column_names else None
        if fail_once and who == fail_once and who not in failed:
            failed.add(who)
            raise RuntimeError("CommitFailedError: Transaction failed: version already exists")
        if drop and who == drop:
            return
        write_deltalake(str(tmp_path / "delta" / ref.lakehouse_id / ref.table), data, mode=mode)
    return write


def extra_harness(tmp_path, fabric=None, extra=livetest.ALL_CHECKS, files=None, writer=None, **kw):
    return livetest.LiveTest(WS, fabric or ItemFabric(), files or Files(), Credential(), tmp_path / "out",
                             table_access=lambda ref: (str(tmp_path / "delta" / ref.lakehouse_id / ref.table), {}),
                             bad_token_files=BadTokenFiles(), run_id="run1", extra=extra,
                             write_table=writer or local_writer(tmp_path), sleep=lambda s: None,
                             lease_backend=kw.pop("lease_backend", None) or (lambda: leases.LocalBackend(tmp_path / "leases")),
                             **kw)


def by_id(result):
    return {c["id"]: c for c in result["checks"]}


def test_default_run_has_no_opt_in_checks(tmp_path):
    fabric = ItemFabric()
    result = harness(tmp_path, fabric).run()
    assert not {"delta.concurrent", "notebook.publish", "notebook.run", "pipeline.definition"} & set(by_id(result))
    assert not [c for c in fabric.calls if c[0] in ("create_item", "run_item_job")]
    assert result["opt_in"] == {"checks": [], "run_in_fabric": False}


def test_all_opt_in_checks_pass_and_their_items_are_deleted(tmp_path):
    fabric = ItemFabric()
    result = extra_harness(tmp_path, fabric).run()
    checks = by_id(result)
    assert result["outcome"] == "passed", result["checks"]
    assert checks["delta.concurrent"]["status"] == "passed"
    assert "101 rows" in checks["delta.concurrent"]["detail"]
    assert checks["notebook.publish"]["status"] == "passed"
    assert checks["pipeline.definition"]["status"] == "passed"
    assert checks["notebook.run"]["status"] == "skipped"
    assert checks["notebook.run"]["detail"].startswith("skipped: needs --run-in-fabric")
    assert ("create_item", "Notebook", "fdlt_run1_nb") in fabric.calls
    assert ("create_item", "DataPipeline", "fdlt_run1_pl") in fabric.calls
    assert not [c for c in fabric.calls if c[0] == "run_item_job"]  # no capacity spent without the flag
    assert fabric.items == {} and set(fabric.lakehouses) == {"existing"}
    assert checks["cleanup.notebook"]["status"] == checks["cleanup.pipeline"]["status"] == "passed"
    # items go before the lakehouse
    deletes = [c[0] for c in fabric.calls if c[0].startswith("delete_")]
    assert deletes == ["delete_item", "delete_item", "delete_lakehouse"]
    assert result["left_behind"] == []


def test_a_writer_that_lost_the_commit_race_is_reported_as_retried(tmp_path):
    result = extra_harness(tmp_path, extra={"delta.concurrent"}, writer=local_writer(tmp_path, fail_once="B")).run()
    check = by_id(result)["delta.concurrent"]
    assert check["status"] == "passed", check
    assert "writer B retried (2 attempts)" in check["detail"]


def test_lost_rows_fail_the_concurrent_check(tmp_path):
    result = extra_harness(tmp_path, extra={"delta.concurrent"}, writer=local_writer(tmp_path, drop="A")).run()
    check = by_id(result)["delta.concurrent"]
    assert result["outcome"] == "failed"
    assert check["status"] == "failed" and "lost ids" in check["detail"]
    assert result["left_behind"] == []


def test_concurrent_check_is_skipped_without_a_lakehouse(tmp_path):
    class NoLakehouse(ItemFabric):
        def create_lakehouse(self, ws, name, description=""):
            raise FabricError("capacity paused", "CapacityNotActive")
    result = extra_harness(tmp_path, NoLakehouse(), extra={"delta.concurrent"}).run()
    assert by_id(result)["delta.concurrent"]["status"] == "skipped"


def test_changed_notebook_cells_fail_and_the_notebook_is_still_deleted(tmp_path):
    fabric = ItemFabric(change_cells=True)
    result = extra_harness(tmp_path, fabric, extra={"notebook.publish"}).run()
    assert by_id(result)["notebook.publish"]["status"] == "failed"
    assert "differ" in by_id(result)["notebook.publish"]["detail"]
    assert fabric.items == {}
    assert result["left_behind"] == []


def test_a_notebook_whose_cells_came_back_changed_is_never_run_in_fabric(tmp_path):
    fabric = ItemFabric(change_cells=True)
    result = extra_harness(tmp_path, fabric, extra=(), run_in_fabric=True).run()
    assert by_id(result)["notebook.publish"]["status"] == "failed"
    assert by_id(result)["notebook.run"]["status"] == "skipped"
    assert not [c for c in fabric.calls if c[0] == "run_item_job"]  # no capacity spent
    assert fabric.items == {}


def test_run_in_fabric_runs_the_notebook_only_with_the_flag(tmp_path):
    fabric = ItemFabric()
    result = extra_harness(tmp_path, fabric, extra=(), run_in_fabric=True).run()
    checks = by_id(result)
    assert checks["notebook.publish"]["status"] == checks["notebook.run"]["status"] == "passed"
    assert ("run_item_job", "item-0", "RunNotebook") in fabric.calls
    assert fabric.items == {}


def test_a_failed_fabric_run_fails_the_check(tmp_path):
    fabric = ItemFabric(job_status="Failed")
    result = extra_harness(tmp_path, fabric, extra=(), run_in_fabric=True).run()
    run = by_id(result)["notebook.run"]
    assert run["status"] == "failed" and "Spark said no" in run["detail"]


def test_a_fabric_run_that_never_finishes_is_cancelled(tmp_path):
    ticks = iter(range(0, 10**6, 600))
    fabric = ItemFabric(job_status="InProgress")
    result = extra_harness(tmp_path, fabric, extra=(), run_in_fabric=True, clock=lambda: next(ticks)).run()
    assert by_id(result)["notebook.run"]["status"] == "failed"
    assert ("cancel_job", "job-1") in fabric.calls


def test_an_unreadable_pipeline_definition_fails_and_is_still_deleted(tmp_path):
    fabric = ItemFabric(bad_pipeline=True)
    result = extra_harness(tmp_path, fabric, extra={"pipeline.definition"}).run()
    check = by_id(result)["pipeline.definition"]
    assert check["status"] == "failed" and "activities must be a list" in check["detail"]
    assert fabric.items == {}


def test_an_existing_item_with_the_test_name_is_never_overwritten_or_deleted(tmp_path):
    theirs = {"id": "theirs", "displayName": "fdlt_run1_pl", "type": "DataPipeline", "parts": {}}
    fabric = ItemFabric(existing_items=[theirs])
    result = extra_harness(tmp_path, fabric, extra={"pipeline.definition"}).run()
    assert by_id(result)["pipeline.definition"]["status"] == "failed"
    assert "won't overwrite" in by_id(result)["pipeline.definition"]["detail"]
    assert "theirs" in fabric.items and not [c for c in fabric.calls if c[0] == "delete_item"]


def test_opt_in_checks_still_refuse_production(tmp_path):
    fabric = ItemFabric({"id": WS, "displayName": "Sales PROD", "description": "fabric-desktop-live-test"})
    result = extra_harness(tmp_path, fabric, run_in_fabric=True).run()
    assert result["outcome"] == "refused"
    assert not [c for c in fabric.calls if c[0] != "get_workspace"]


def test_failed_item_cleanup_is_left_in_the_ledger_and_finished_later(tmp_path):
    fabric = ItemFabric(fail_delete_item=True)
    result = extra_harness(tmp_path, fabric, extra={"notebook.publish", "pipeline.definition"}).run()
    assert result["outcome"] == "failed"
    assert sorted(r["name"] for r in result["left_behind"]) == ["fdlt_run1_nb", "fdlt_run1_pl"]
    fabric.fail_delete_item = False
    done = livetest.cleanup_from_ledger(tmp_path / "out" / "run1" / "ledger.json", fabric, Credential())
    assert done["left_behind"] == [] and fabric.items == {}


def test_opt_in_results_hold_no_token(tmp_path):
    def leaky(ref, data, mode):
        raise RuntimeError(f"401 Authorization: Bearer {TOKEN}")
    result = extra_harness(tmp_path, writer=leaky, run_in_fabric=True).run()
    assert by_id(result)["delta.concurrent"]["status"] == "failed"
    for name in ("result.json", "summary.md", "ledger.json"):
        assert TOKEN not in (tmp_path / "out" / "run1" / name).read_text()
    saved = json.loads((tmp_path / "out" / "run1" / "result.json").read_text())
    assert saved["opt_in"]["run_in_fabric"] is True
    assert {"id", "status", "seconds", "detail"} <= set(saved["checks"][0])


def test_unknown_opt_in_check_is_refused():
    with pytest.raises(ValueError, match="Unknown checks"):
        livetest.LiveTest(WS, None, None, None, ".", bad_token_files=object(), extra={"spark.everything"})


def test_dry_run_with_all_lists_opt_in_checks_and_their_cost(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        cli.main(["live-test", "--workspace", WS, "--dry-run", "--all"])
    assert exit.value.code == 0
    out = capsys.readouterr().out
    for id in livetest.ALL_CHECKS:
        assert f"Opt-in {id}" in out
    assert "uses capacity" in out
    assert "add --run-in-fabric" in out
    assert list(tmp_path.iterdir()) == []


def test_delete_item_calls_the_documented_endpoint():
    from fabric_desktop.fabric_api import FabricClient

    class Resp:
        status_code, content, headers, text = 200, b"", {}, ""

    class Session:
        def request(self, method, url, **kwargs):
            self.call = (method, url)
            return Resp()
    session = Session()
    FabricClient(lambda: "t", session=session).delete_item("ws1", "nb1")
    assert session.call == ("DELETE", "https://api.fabric.microsoft.com/v1/workspaces/ws1/items/nb1")


def test_cleanup_checks_the_live_item_before_deleting_it(tmp_path):
    # A hand-edited or stale ledger can't make cleanup delete something this run didn't make.
    fabric = ItemFabric(existing_items=[
        {"id": "nb1", "type": "Notebook", "displayName": "Customer churn", "parts": {}},
        {"id": "pl1", "type": "Notebook", "displayName": "fdlt_run1_pl", "parts": {}},
        {"id": "nb2", "type": "Notebook", "displayName": "fdlt_other_nb", "parts": {}},
        {"id": "nb3", "type": "Notebook", "displayName": "fdlt_run1_nb", "parts": {}},
    ])
    fabric.lakehouses["lh-x"] = {"id": "lh-x", "displayName": "Sales"}
    ledger = livetest.Ledger(tmp_path / "out" / "run1" / "ledger.json", "run1", WS, [
        {"kind": "notebook", "workspace_id": WS, "id": "nb1", "name": "fdlt_run1_nb"},  # the id is someone else's
        {"kind": "pipeline", "workspace_id": WS, "id": "pl1", "name": "fdlt_run1_pl"},  # wrong type
        {"kind": "notebook", "workspace_id": WS, "id": "nb2", "name": "fdlt_other_nb"},  # another run's name
        {"kind": "lakehouse", "workspace_id": WS, "id": "lh-x", "name": "fdlt_run1"},  # renamed since
        {"kind": "notebook", "workspace_id": WS, "id": "nb3", "name": "fdlt_run1_nb"},  # ours
        {"kind": "notebook", "workspace_id": WS, "id": "gone", "name": "fdlt_run1_nb"},  # already deleted
    ])
    ledger.save()
    done = livetest.cleanup_from_ledger(tmp_path / "out" / "run1" / "ledger.json", fabric, Credential())
    assert [c for c in fabric.calls if c[0] in ("delete_item", "delete_lakehouse")] == [("delete_item", "nb3")]
    assert {"nb1", "pl1", "nb2"} <= set(fabric.items) and "lh-x" in fabric.lakehouses
    left = {r["id"] for r in done["left_behind"]}
    assert left == {"nb1", "pl1", "nb2", "lh-x"}  # refused ones stay listed for a person to look at


# ---- PB-003: a lease on OneLake refuses a second writer; a MERGE stays in the Fabric-safe profile

def merge_writer(tmp_path):
    def write(ref, data, mode):
        write_deltalake(str(tmp_path / "delta" / ref.lakehouse_id / ref.table), data, mode=mode)
    return write


def test_lease_and_merge_checks_pass(tmp_path):
    result = extra_harness(tmp_path, extra={"lease.onelake", "delta.merge_profile"}, writer=merge_writer(tmp_path)).run()
    checks = by_id(result)
    assert checks["lease.onelake"]["status"] == "passed", checks["lease.onelake"]
    assert "writer B's create was refused" in checks["lease.onelake"]["detail"]
    assert checks["delta.merge_profile"]["status"] == "passed", checks["delta.merge_profile"]
    assert "updated 1 and inserted 1" in checks["delta.merge_profile"]["detail"]
    assert "reader 1, writer 2" in checks["delta.merge_profile"]["detail"]
    assert not list((tmp_path / "leases").glob("*.json")), "every lease was released"


class IgnoresConditions(leases.LocalBackend):
    """A store that ignores If-None-Match and If-Match, as a OneLake that didn't honour them would."""

    def create(self, key, data):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._etag(data)

    def replace(self, key, data, etag):
        return self.create(key, data)


def test_a_store_that_ignores_conditional_requests_fails_the_lease_check(tmp_path):
    result = extra_harness(tmp_path, extra={"lease.onelake"},
                           lease_backend=lambda: IgnoresConditions(tmp_path / "leases")).run()
    check = by_id(result)["lease.onelake"]
    assert check["status"] == "failed"
    # The failure is the wrongly granted lease, not the lost lease A's release would report after it.
    assert "A second writer got the lease" in check["detail"] and "If-None-Match" in check["detail"]
    assert not list((tmp_path / "leases").glob("**/*.json")), "B's wrongly granted lease was released"


class IgnoresIfMatch(leases.LocalBackend):
    """A store that refuses a second create but replaces a file whatever its ETag."""

    def replace(self, key, data, etag):
        path = self._path(key)
        if not path.exists():
            return None
        path.write_bytes(data)
        return self._etag(data)


def test_the_lease_check_fails_on_a_store_that_ignores_if_match(tmp_path):
    result = extra_harness(tmp_path, extra={"lease.onelake"},
                           lease_backend=lambda: IgnoresIfMatch(tmp_path / "leases")).run()
    check = by_id(result)["lease.onelake"]
    assert check["status"] == "failed" and "ignored If-Match" in check["detail"]


def test_a_merge_into_a_table_outside_the_profile_is_refused_before_writing(tmp_path):
    def deletion_vectors(ref, data, mode):
        write_deltalake(str(tmp_path / "delta" / ref.lakehouse_id / ref.table), data, mode=mode,
                        configuration={"delta.enableDeletionVectors": "true"})
    result = extra_harness(tmp_path, extra={"delta.merge_profile"}, writer=deletion_vectors).run()
    check = by_id(result)["delta.merge_profile"]
    assert check["status"] == "failed"
    assert "outside the Fabric-safe Delta profile" in check["detail"]
    from deltalake import DeltaTable
    table = DeltaTable(str(tmp_path / "delta" / "lh-1" / "livetest_merge"))
    assert table.version() == 0, "nothing was merged"


def test_lease_and_merge_checks_need_the_lakehouse(tmp_path):
    class NoLakehouse(ItemFabric):
        def create_lakehouse(self, ws, name, description=""):
            raise FabricError("Capacity is paused", "CapacityNotActive")
    built = []
    result = extra_harness(tmp_path, NoLakehouse(), extra={"lease.onelake", "delta.merge_profile"},
                           lease_backend=lambda: built.append(1)).run()
    assert built == [], "no lease store is touched without the run's own lakehouse"
    assert by_id(result)["lease.onelake"]["status"] == "skipped"
    assert by_id(result)["delta.merge_profile"]["status"] == "skipped"
