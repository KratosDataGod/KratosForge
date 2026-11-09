"""The live test harness (livetest.py), the paths the main tests don't reach: Fabric or OneLake
answering wrongly at each step. Each one must fail its own check with why, never pass, and the
lakehouse must still be cleaned up. Fabric and OneLake are faked and Delta is on local disk."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
import requests
from deltalake import write_deltalake

from fabric_desktop import livetest
from fabric_desktop.fabric_api import FabricError
from tests.test_livetest import (TOKEN, WS, BadTokenFiles, Credential, Fabric, Files, ItemFabric, by_id,
                                 extra_harness)


def run(tmp_path, fabric=None, files=None, credential=None, bad_token_files=None, table_access=None):
    fabric = fabric or Fabric()
    test = livetest.LiveTest(WS, fabric, files or Files(), credential or Credential(), tmp_path / "out",
                             table_access=table_access or (lambda ref: (str(tmp_path / "delta" / ref.lakehouse_id / ref.table), {})),
                             bad_token_files=bad_token_files or BadTokenFiles(), run_id="run1")
    return test.run(), fabric


def failed(result, check_id):
    check = by_id(result)[check_id]
    assert check["status"] == "failed", check
    return check["detail"]


# ---- signing in and the workspace


def test_an_empty_onelake_token_fails_its_check_and_the_run_goes_on(tmp_path):
    class NoStorage(Credential):
        def storage_token(self):
            return ""
    result, fabric = run(tmp_path, credential=NoStorage())
    assert "empty token" in failed(result, "auth.onelake")
    assert result["outcome"] == "failed" and by_id(result)["delta.write"]["status"] == "passed"
    assert set(fabric.lakehouses) == {"existing"}


def test_an_empty_fabric_token_stops_before_any_workspace_call(tmp_path):
    class NoFabric(Credential):
        def fabric_token(self):
            return ""
    result, fabric = run(tmp_path, credential=NoFabric())
    assert "empty token" in failed(result, "auth.fabric") and fabric.calls == []


def test_a_workspace_that_cant_be_read_stops_the_run_before_any_write(tmp_path):
    class Hidden(Fabric):
        def get_workspace(self, ws):
            raise FabricError("not found", "WorkspaceNotFound")
    result, fabric = run(tmp_path, Hidden())
    assert failed(result, "fabric.workspace").startswith("FabricError")
    assert result["refused"] == [] and fabric.calls == [] and "fabric.create_lakehouse" not in by_id(result)


# ---- Fabric answering wrongly


def test_a_lakehouse_created_without_an_id_skips_everything_that_needs_it(tmp_path):
    class NoId(Fabric):
        def create_lakehouse(self, ws, name, description=""):
            self.calls.append(("create_lakehouse", name))
            return {"displayName": name}
    result, fabric = run(tmp_path, NoId())
    assert "didn't return the new lakehouse's id" in failed(result, "fabric.create_lakehouse")
    skipped = [c for c in result["checks"] if c["status"] == "skipped"]
    assert {c["id"] for c in skipped} == {i for i, _ in livetest._AFTER_LAKEHOUSE}
    assert result["left_behind"] == []  # nothing went in the ledger, so nothing is deleted
    assert not [c for c in fabric.calls if c[0] == "delete_lakehouse"]


def test_a_lakehouse_without_a_tables_path_fails_its_check(tmp_path):
    class NoPaths(Fabric):
        def get_lakehouse(self, ws, lh):
            return {k: v for k, v in super().get_lakehouse(ws, lh).items() if k != "properties"}
    result, fabric = run(tmp_path, NoPaths())
    assert "no oneLakeTablesPath" in failed(result, "fabric.get_lakehouse")
    assert set(fabric.lakehouses) == {"existing"}


def test_a_missing_lakehouse_that_reads_fine_fails_and_so_does_a_delete_that_didnt_take(tmp_path):
    class ReadsAnything(Fabric):
        def get_lakehouse(self, ws, lh):
            return {"id": lh, "displayName": "fdlt_run1", "properties": {"oneLakeTablesPath": "https://x/Tables"}}
    result, _ = run(tmp_path, ReadsAnything())
    assert "didn't fail" in failed(result, "errors.not_found")
    assert "can still be read after deleting it" in failed(result, "cleanup.lakehouse")
    assert [r["name"] for r in result["left_behind"]] == ["fdlt_run1"]  # kept for --cleanup


# ---- OneLake answering wrongly


class Unlisted(Files):
    def list(self, ws, lh, folder):
        return []


class Changed(Files):
    def read_bytes(self, ws, lh, path):
        return b"something else" if (lh, path) in self.data else None


class Undeletable(Files):
    def delete(self, ws, lh, folder):
        pass


@pytest.mark.parametrize("files, says", [
    (Unlisted(), "isn't listed"),
    (Changed(), "differs from what was written"),
    (Undeletable(), "still there after deleting it"),
])
def test_a_file_that_doesnt_round_trip_fails_the_files_check(tmp_path, files, says):
    result, fabric = run(tmp_path, files=files)
    assert says in failed(result, "onelake.files")
    assert set(fabric.lakehouses) == {"existing"}


def _bad_token_answer(status):
    class Answer:
        def list(self, ws, lh, folder):
            if status is None:
                return []
            resp = requests.Response()
            resp.status_code = status
            raise requests.HTTPError(str(status), response=resp)
    return Answer()


@pytest.mark.parametrize("status, says", [(500, "Refused with 500, expected 401 or 403"),
                                          (None, "OneLake accepted a bad token")])
def test_a_bad_token_onelake_doesnt_refuse_properly_fails_its_check(tmp_path, status, says):
    result, _ = run(tmp_path, bad_token_files=_bad_token_answer(status))
    assert says in failed(result, "errors.bad_token")


# ---- Delta answering wrongly


def test_a_table_that_cant_be_written_skips_the_checks_that_read_it(tmp_path):
    def unreachable(ref):
        raise OSError(f"OneLake refused the write, Authorization: Bearer {TOKEN}")
    result, fabric = run(tmp_path, table_access=unreachable)
    assert TOKEN not in failed(result, "delta.write")
    assert {by_id(result)[i]["status"] for i, _ in livetest._AFTER_WRITE} == {"skipped"}
    assert set(fabric.lakehouses) == {"existing"}


def test_a_table_that_reads_back_wrong_fails_each_delta_check(tmp_path):
    """The write goes to one table; every read, append and merge after it goes to another that already
    holds different rows, as a store that lost the write would look."""
    elsewhere = str(tmp_path / "elsewhere")
    write_deltalake(elsewhere, pa.table({"id": [1, 2], "name": ["z", "y"]}))
    calls = []

    def access(ref):
        calls.append(ref)
        return (str(tmp_path / "written") if len(calls) == 1 else elsewhere), {}
    result, _ = run(tmp_path, table_access=access)
    assert "Read back" in failed(result, "delta.read")
    assert "Expected version 1 with 4 rows" in failed(result, "delta.append")
    assert "After the merge the table holds" in failed(result, "delta.merge")
    assert "doesn't have the original 3 rows" in failed(result, "delta.time_travel")


# ---- opt-in items answering wrongly


def test_a_notebook_created_without_an_id_fails_and_nothing_is_run(tmp_path):
    class NoNotebook(ItemFabric):
        def save_notebook(self, ws, name, ipynb):
            return {}
    fabric = NoNotebook()
    result = extra_harness(tmp_path, fabric, extra={"notebook.publish"}).run()
    assert "didn't return the new notebook's id" in failed(result, "notebook.publish")
    assert not [c for c in fabric.calls if c[0] == "run_item_job"]


def test_a_notebook_definition_without_its_ipynb_fails_and_the_notebook_is_deleted(tmp_path):
    class NoIpynb(ItemFabric):
        def get_item_definition(self, ws, item_id, fmt=None):
            return {"definition": {"parts": [{"path": "notebook-content.py", "payload": ""}]}}
    fabric = NoIpynb()
    result = extra_harness(tmp_path, fabric, extra={"notebook.publish"}).run()
    assert "no .ipynb part" in failed(result, "notebook.publish")
    assert by_id(result)["cleanup.notebook"]["status"] == "passed" and fabric.items == {}


class Pipelines(ItemFabric):
    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def save_item(self, ws, item_type, name, parts=None, description=""):
        made = super().save_item(ws, item_type, name, parts, description)
        return self.answer(self, made)


def _two_activities(fabric, made):
    content = '{"properties": {"activities": [{"name": "Wait1", "type": "Wait", "dependsOn": []}, ' \
              '{"name": "Wait2", "type": "Wait", "dependsOn": []}]}}'
    fabric.items[made["id"]]["parts"] = {"pipeline-content.json": content}
    return made


@pytest.mark.parametrize("answer, says, deleted", [
    (lambda fabric, made: {}, "didn't return the new pipeline's id", False),
    (lambda fabric, made: made | {"created": False}, "already existed, so the live test didn't record it", False),
    (_two_activities, "Expected the one Wait1 activity, read back ['Wait1', 'Wait2']", True),
])
def test_a_pipeline_that_comes_back_wrong_fails_and_only_ours_is_deleted(tmp_path, answer, says, deleted):
    fabric = Pipelines(answer)
    result = extra_harness(tmp_path, fabric, extra={"pipeline.definition"}).run()
    assert says in failed(result, "pipeline.definition")
    assert (("delete_item", "item-0") in fabric.calls) is deleted  # never one the ledger didn't record


# ---- cleanup from a ledger


def _ledger(tmp_path, *resources):
    ledger = livetest.Ledger(tmp_path / "out" / "run1" / "ledger.json", "run1", WS, list(resources))
    ledger.save()
    return ledger.path


def test_a_ledger_lakehouse_already_gone_is_cleaned_up(tmp_path):
    path = _ledger(tmp_path, {"kind": "lakehouse", "workspace_id": WS, "id": "lh-9", "name": "fdlt_run1"})
    done = livetest.cleanup_from_ledger(path, Fabric(), Credential())
    assert done["checks"][0]["detail"] == "Already gone." and done["left_behind"] == []


def test_a_ledger_lakehouse_fabric_cant_say_about_is_kept(tmp_path):
    class Throttled(Fabric):
        def get_lakehouse(self, ws, lh):
            raise FabricError("Too many requests", "RequestBlocked", 429)
    path = _ledger(tmp_path, {"kind": "lakehouse", "workspace_id": WS, "id": "lh-9", "name": "fdlt_run1"})
    done = livetest.cleanup_from_ledger(path, Throttled(), Credential())
    assert done["checks"][0]["status"] == "failed" and [r["id"] for r in done["left_behind"]] == ["lh-9"]


def test_a_ledger_item_still_listed_after_its_delete_is_kept(tmp_path):
    class Sticky(ItemFabric):
        def delete_item(self, ws, item_id):
            self.calls.append(("delete_item", item_id))
    fabric = Sticky(existing_items=[{"id": "nb-1", "displayName": "fdlt_run1_nb", "type": "Notebook", "parts": {}}])
    path = _ledger(tmp_path, {"kind": "notebook", "workspace_id": WS, "id": "nb-1", "name": "fdlt_run1_nb"})
    done = livetest.cleanup_from_ledger(path, fabric, Credential())
    assert "still listed after deleting it" in done["checks"][0]["detail"]
    assert [r["id"] for r in done["left_behind"]] == ["nb-1"]


# ---- small helpers


def test_no_policy_file_means_no_protected_platform_workspaces(monkeypatch):
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "load", lambda: {"platform": {"source": "user", "value": {"dev": {"workspace_id": "W"}}}})
    assert livetest.policy_workspace_ids() == set()


def test_a_mailbox_request_no_agent_answered_says_so():
    with pytest.raises(RuntimeError, match="No agent wrote a status"):
        livetest._status(SimpleNamespace(read=lambda path: None), "r1")


def test_a_detail_that_cant_be_read_is_left_out():
    assert livetest._safe(lambda: 1 / 0) == ""
    assert livetest._safe(lambda: None) == "" and livetest._safe(lambda: 7) == "7"
