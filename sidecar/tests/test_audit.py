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


def call(service, method, **params):
    return server.handle(service, json.dumps({"id": 1, "method": method, "params": params}))


def test_calls_that_change_or_run_something_are_recorded_with_who_and_outcome(tmp_path):
    s = Service(tmp_path)
    call(s, "list_workspaces")
    call(s, "poll_cell", run_id="r1")
    call(s, "sql_start", query="SELECT secret_col FROM customers", lakehouse_ref={"workspace_id": "ws1", "lakehouse_id": "lh1"})
    call(s, "meta_promote", env="test", target="prod", entities=["sales"])
    rows = s.audit.recent()
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
