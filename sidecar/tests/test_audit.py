"""The audit trail (FD-125)."""

import json

from fabric_desktop import audit, cli, server
from fabric_desktop.audit import AuditLog

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


class Auth:
    def signed_in_user(self):
        return "ann@contoso.com"

    def signed_in_tenant(self):
        return "tenant-1"


class Service:
    def __init__(self, folder):
        self.audit = AuditLog(folder)
        self._auth = Auth()

    def list_workspaces(self):
        return []

    def poll_cell(self, run_id):
        return {}

    def sql_start(self, query, lakehouse_ref=None):  # the real RPC's parameter names
        return {"rows": []}

    def meta_promote(self, env, target, entities=None):
        raise RuntimeError(f"409 conflict, Authorization: Bearer {JWT}")

    def meta_run(self, names=None, env="dev"):  # one table ran, one failed: returned, not raised
        return [{"entity": "sales", "status": "ok", "message": ""},
                {"entity": "orders", "status": "failed", "message": f"Bearer {JWT} customers"}]

    def pipeline_run_start(self, text=None):  # refused without raising
        return {"ok": False, "problems": [{"code": "busy", "message": "4 runs are already going: customers"}]}


def call(service, method, **params):
    return server.handle(service, json.dumps({"id": 1, "method": method, "params": params}))


def test_calls_that_change_or_run_something_are_recorded_with_who_and_outcome(tmp_path):
    s = Service(tmp_path)
    call(s, "list_workspaces")
    call(s, "poll_cell", run_id="r1")
    call(s, "sql_start", query="SELECT secret_col FROM customers", lakehouse_ref={"workspace_id": "ws1", "lakehouse_id": "lh1"})
    call(s, "meta_promote", env="test", target="prod", entities=["sales"])
    every = s.audit.recent()
    assert [r["action"] for r in every if r["outcome"] == "started"] == ["meta_promote"]  # a Prod change: line first
    rows = [r for r in every if r["outcome"] != "started"]
    assert [r["action"] for r in rows] == ["meta_promote", "sql_start"]  # newest first; reads not recorded
    promote, sql = rows
    assert promote["outcome"] == "error" and JWT not in promote["error"] and "[removed]" in promote["error"]
    assert promote["target"] == {"env": "test", "target": "prod", "entities": ["sales"]}
    assert sql["outcome"] == "ok" and sql["who"] == "ann@contoso.com" and sql["tenant"] == "tenant-1"
    assert sql["target"]["query"] == "(given)"  # queries and code are never recorded
    assert sql["target"]["lakehouse_ref"] == {"workspace_id": "ws1", "lakehouse_id": "lh1"}
    assert "customers" not in json.dumps(rows)


def test_every_method_is_either_read_only_or_audited_on_purpose():
    unknown = audit.READ_ONLY - server.METHODS
    assert not unknown, f"READ_ONLY names methods that don't exist: {unknown}"
    for writer in ("start_cell", "sql_start", "meta_run", "meta_promote", "publish_notebook", "save_notebook",
                   "notebook_delete", "create_lakehouse", "xray_vacuum", "save_app_settings", "run_notebook_in_fabric"):
        assert audit.audited(writer), writer
    for reader in ("poll_cell", "list_workspaces", "preview_table", "kernel_complete"):
        assert not audit.audited(reader), reader


def test_the_chain_shows_a_changed_or_removed_line(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(4):
        log.record("meta_run", "ok", 1.0, {"env": "dev", "n": i})
    assert log.verify() == {"lines": 4, "ok": True, "problems": []}
    [path] = list(tmp_path.glob("audit-*.jsonl"))
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")  # one removed
    assert not log.verify()["ok"]
    edited = json.loads(lines[1])
    edited["outcome"] = "error"
    path.write_text("\n".join([lines[0], json.dumps(edited), lines[2], lines[3]]) + "\n")
    result = log.verify()
    assert not result["ok"] and "changed" in result["problems"][0]


def test_a_new_process_continues_the_chain(tmp_path):
    AuditLog(tmp_path).record("meta_run", "ok", 1.0)
    AuditLog(tmp_path).record("meta_run", "ok", 1.0)
    assert AuditLog(tmp_path).verify()["ok"]


def test_an_audit_problem_never_fails_the_call(tmp_path, capsys):
    s = Service(tmp_path)

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")
    s.audit = Broken()
    reply = call(s, "sql_start", query="SELECT 1")
    assert reply["result"] == {"rows": []}
    assert "could not record sql_start" in capsys.readouterr().err


def test_cli_shows_and_verifies_the_trail(tmp_path, capsys):
    log = AuditLog(tmp_path)
    log.record("meta_run", "ok", 1.0, {"env": "dev"})
    try:
        cli.main(["audit", "--folder", str(tmp_path), "--verify"])
    except SystemExit as exit:
        assert exit.code == 0
    assert '"ok": true' in capsys.readouterr().out
    try:
        cli.main(["audit", "--folder", str(tmp_path)])
    except SystemExit as exit:
        assert exit.code == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["action"] == "meta_run"


def test_the_real_service_keeps_its_trail_in_its_own_folder(tmp_path):
    from fabric_desktop.service import Service as Real
    service = Real(root=tmp_path)
    service.audit.record("meta_run", "ok", 0.1)
    out = service.audit_log()
    assert out["check"]["ok"] and out["entries"][0]["app"] == "desktop"
    assert out["folder"] == str(tmp_path / "audit")


def test_every_name_that_carries_code_or_queries_is_left_out():
    params = {"code": "x = 1", "sql": "SELECT 1", "query": "SELECT 2", "statement": "DELETE FROM t", "table": "t"}
    assert audit.target(params) == {"code": "(given)", "sql": "(given)", "query": "(given)",
                                    "statement": "(given)", "table": "t"}


def test_a_refusal_or_failure_returned_rather_than_raised_is_recorded_as_an_error(tmp_path):
    s = Service(tmp_path)
    call(s, "meta_run", names=["sales", "orders"])
    call(s, "pipeline_run_start", text="{}")
    pipeline, run = s.audit.recent()
    assert (run["outcome"], run["reason"]) == ("error", "partial")
    assert (pipeline["outcome"], pipeline["reason"]) == ("error", "busy")
    assert "customers" not in json.dumps([run, pipeline]) and JWT not in json.dumps([run, pipeline])  # never the reply
    assert "reason" not in AuditLog(tmp_path).record("meta_run", "ok", 1.0)


def test_the_outcome_of_what_a_call_returned(tmp_path):
    rows = lambda *st: [{"entity": f"e{i}", "status": x} for i, x in enumerate(st)]  # noqa: E731
    cases = [
        ({"rows": []}, ("ok", "")), (None, ("ok", "")), ([], ("ok", "")), (["a"], ("ok", "")),
        (rows("ok", "skipped"), ("ok", "")),
        (rows("ok", "failed"), ("error", "partial")),
        (rows("failed", "needs_decision"), ("error", "failed")),
        ({"ok": True}, ("ok", "")),
        ({"ok": False}, ("error", "refused")),
        ({"ok": False, "preview": True}, ("error", "preview")),
        ({"ok": False, "problems": [{"code": "busy"}]}, ("error", "busy")),
        ({"ok": False, "problems": [{"code": "policy_blocked"}]}, ("error", "policy")),
        ({"ok": False, "problems": [{"code": "bad_json"}]}, ("error", "invalid")),
        ({"status": "failed"}, ("error", "failed")),
        ({"done": ["a"], "failed": [{"id": "b"}]}, ("error", "partial")),
        ({"done": [], "failed": [{"id": "b"}]}, ("error", "failed")),
        ({"done": ["a"], "failed": [], "problems": ["a note"]}, ("ok", "")),
    ]
    for result, expected in cases:
        assert audit.outcome(result) == expected, result
    import pytest
    with pytest.raises(ValueError):
        AuditLog(tmp_path).record("x", "error", 0.0, reason="anything the reply said")  # a fixed code, nothing else
    assert not list(tmp_path.glob("audit-*.jsonl"))


class Guarded(Service):
    def __init__(self, folder):
        super().__init__(folder)
        self.ran = []

    def meta_rollback(self, env, version):  # Prod-reaching, takes no confirmation: on the list
        self.ran.append("meta_rollback")
        return {"env": env}

    def sql_start(self, query, lakehouse_ref=None, confirm_name=""):  # a typed name: found by its parameter
        self.ran.append("sql_start")
        return {"rows": []}

    def save_notebook(self, name):  # an everyday edit
        self.ran.append("save_notebook")
        return {"saved": name}


def test_a_protected_call_writes_its_line_first_and_doesnt_run_without_it(tmp_path, capsys):
    s = Guarded(tmp_path)
    call(s, "meta_rollback", env="prod", version="v1")
    started, done = s.audit.recent()[::-1]
    assert (started["action"], started["outcome"]) == ("meta_rollback", "started") and done["outcome"] == "ok"
    assert started["target"] == {"env": "prod", "version": "v1"} and started["who"] == "ann@contoso.com"

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")
    s.audit = Broken()
    for method, params in (("meta_rollback", {"env": "prod", "version": "v2"}), ("sql_start", {"query": "x", "confirm_name": "Sales"})):
        reply = call(s, method, **params)
        assert "audit log couldn't be written, so this didn't run (OSError: disk full)" in reply["error"], method
    assert s.ran == ["meta_rollback"]  # neither refused call ran
    assert call(s, "save_notebook", name="n")["result"] == {"saved": "n"}  # everyday edits still run
    assert call(s, "sql_start", query="SELECT 1")["result"] == {"rows": []}  # an ordinary query: no typed name
    assert call(s, "sql_start", query="SELECT 1", confirm_name="")["result"] == {"rows": []}  # given empty
    assert s.ran == ["meta_rollback", "save_notebook", "sql_start", "sql_start"]


def test_which_calls_fail_closed():
    assert audit.FAIL_CLOSED <= server.METHODS
    for method in audit.FAIL_CLOSED:
        assert audit.fails_closed(method, {})
    for method, params in (("sql_start", {"query": "x", "confirm_name": "Sales"}), ("xray_optimize", {"risk_id": "r-1"}),
                           ("platform_share_keys", {"approve": True}), ("integrations_decide", {"approval_id": "a1"}),
                           ("databricks_bundle_deploy", {"typed": "prod"}), ("local_drop_delta", {"confirm": True}),
                           ("set_risk_settings", {"require_acknowledgement": True}),
                           ("meta_run", {"env": "prod"}), ("meta_optimize", {"env": " PROD "}),
                           ("meta_publish_varlib", {"env": "prod"}), ("databricks_bundle_deploy", {"target": "prod"}),
                           ("platform_entra_save", {"envs": ["dev", "prod"]}), ("git_commit", {"allow_secrets": True}),
                           ("pipeline_run_start", {"allow_uncontained": True}), ("git_delete_branch", {"force": True}),
                           ("publish_notebook", {"replace_fabric_changes": True}), ("appstudio_publish", {"replace": True}),
                           ("wh_project_write", {"replace": True}), ("sql_file_write", {"overwrite": True}),
                           ("engine_connection_save", {"replace": "c-1"})):
        assert audit.fails_closed(method, params), method
    for method, params in (("sql_start", {"query": "x"}), ("sql_start", {"query": "x", "confirm": False, "confirm_name": ""}),
                           ("meta_run", {"risk_id": ""}), ("save_notebook", {"name": "n"}), ("start_cell", {"code": "x"}),
                           ("meta_run", {"env": "dev"}), ("meta_run", {"env": "production-copy"}),
                           ("sql_start", {"query": "x", "target": "lakehouse"}), ("sql_query_save", {"expect": "v3"}),
                           ("save_notebook", {"name": "prod"}), ("pipeline_run_start", {"unconfirmed": True}),
                           ("publish_notebook", {"replace_fabric_changes": False}), ("engine_connection_save", {"replace": ""}),
                           ("sql_file_write", {"overwrite": False})):
        assert not audit.fails_closed(method, params), (method, params)


def test_every_go_ahead_parameter_has_been_reviewed():
    """A new parameter that looks like a confirmation, approval or override must be put in
    audit.GUARD_PARAMS (fails closed when given) or audit.NOT_GUARD_PARAMS, so none is missed."""
    import inspect
    import re

    from fabric_desktop.service import Service
    looks = re.compile(r"confirm|approv|typed|risk|acknowledg|allow|force|override|expect|accept|sure|danger|"
                       r"replace|overwrite|discard|bypass|anyway")
    unreviewed = sorted({(name, method) for method in server.METHODS if audit.audited(method)
                         for name in inspect.signature(getattr(Service, method)).parameters
                         if looks.search(name) and name not in audit.GUARD_PARAMS | audit.NOT_GUARD_PARAMS})
    assert unreviewed == []
    assert not audit.GUARD_PARAMS & audit.NOT_GUARD_PARAMS


def test_a_delete_in_fabric_or_a_removed_access_doesnt_run_unrecorded(tmp_path, monkeypatch):
    """A delete in Fabric, a revoked access or a go-ahead to replace runs only once its line is
    written: with the audit file unwritable, the call is refused and never reaches its method."""
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    ran = []
    calls = {"delete_shortcut": {"workspace_id": "w", "item_id": "i", "path": "Tables", "name": "s"},
             "access_delete_role": {"workspace_id": "w", "item_id": "i", "name": "Readers"},
             "delete_fabric_schedule": {"workspace_id": "w", "item_id": "i", "job_type": "Pipeline", "schedule_id": "s1"},
             "platform_revoke": {"product_id": "p", "project": "x"},
             "platform_remove_admin": {"user": "ann@contoso.com"},
             "integrations_revoke_client": {"client_id": "c"},
             "delete_notebook_schedule": {"name": "n", "schedule_id": "s1"},
             "xray_remove_staging": {"workspace_id": "w", "lakehouse_id": "l", "lakehouse_name": "staging"},
             "governance_unassign": {"policy": "p", "scope": "domain"},
             "publish_notebook": {"name": "n", "cells": [], "replace_fabric_changes": True}}
    for method in calls:
        monkeypatch.setattr(Service, method, lambda self, *a, _m=method, **k: ran.append(_m) or {})

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")
    svc._audit_log = Broken()
    for i, (method, params) in enumerate(calls.items()):
        reply = server.handle(svc, json.dumps({"id": i, "method": method, "params": params}))
        assert "audit log couldn't be written, so this didn't run" in reply.get("error", ""), method
    assert ran == []


# Every audited call that deletes, removes, revokes or resets something either fails closed (above) or
# says here why it needn't: each acts only on this PC, or on a file of the open project, and asks first
# on its screen. A new one fails until it's reviewed, so a delete in Fabric can't be added unrecorded.
LOCAL_REMOVALS = {
    "academy_reset": "an exercise's own files on this PC",
    "academy_sandbox_remove_grants": "narrows the exercise sandbox's access on this PC; the next exercise grants it again",
    "appstudio_connection_delete": "a saved model connection on this PC",
    "databricks_sync_pair_remove": "a saved sync pair on this PC; neither side is touched",
    "databricks_volume_delete": "files in a volume on this PC, never in Databricks; confirm is a go-ahead (GUARD_PARAMS)",
    "databricks_volume_drop": "a volume's folder on this PC, never in Databricks; confirm is a go-ahead (GUARD_PARAMS)",
    "delete_pc_schedule": "a schedule on this PC",
    "engine_connection_remove": "a saved connection in this PC's profile",
    "environment_delete": "a Python environment on this PC",
    "git_delete_branch": "a local branch; force is a go-ahead (GUARD_PARAMS)",
    "learn_lab_reset": "a lab's own files on this PC",
    "local_drop_delta": "a table on this PC; confirm is a go-ahead (GUARD_PARAMS)",
    "local_remove_source": "views over a folder; the files are never touched",
    "local_writes_clear": "spill and temporary files on this PC",
    "mesh_delete_link": "a file of the open project",
    "mesh_delete_product": "a file of the open project",
    "meta_delete_entity": "a file of the open project",
    "meta_delete_key_table": "a file of the open project; aimed at Prod it fails closed (env)",
    "meta_delete_source_system": "a file of the open project; aimed at Prod it fails closed (env)",
    "notebook_comment_delete": "a comment in a notebook file on this PC",
    "notebook_delete": "moves a notebook to Recently deleted on this PC; its Fabric item isn't touched",
    "notebook_folder_delete": "a folder of notebooks on this PC",
    "notebook_resource_delete": "a notebook's resource file on this PC",
    "notebook_version_delete": "a saved version on this PC",
    "profile_avatar_remove": "this PC's profile picture",
    "profile_delete": "this PC's profile",
    "profile_goal_remove": "a goal in this PC's profile",
    "sql_query_delete": "a saved query on this PC",
    "sql_template_delete": "a saved template on this PC",
    "usage_import_delete": "an imported usage file on this PC",
}


def test_every_delete_either_fails_closed_or_says_why_it_neednt():
    import re
    removes = re.compile(r"(^|_)(delete|deleted|remove|revoke|unassign|drop|purge|wipe|clear|reset)(_|$)")
    found = {m for m in server.METHODS if removes.search(m) and audit.audited(m)}
    assert sorted(found - audit.FAIL_CLOSED - set(LOCAL_REMOVALS)) == [], \
        "a new delete: add it to audit.FAIL_CLOSED, or to LOCAL_REMOVALS with why it acts only on this PC"
    assert sorted(set(LOCAL_REMOVALS) - found) == [] and not set(LOCAL_REMOVALS) & audit.FAIL_CLOSED
