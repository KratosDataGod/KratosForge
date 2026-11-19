"""The command line (cli.py): the smoke test's dry run with the platform and warehouse flows, its warehouse
connection, `nb open` on Windows, the templates, governance, pipeline and audit commands' remaining
answers. Every service, client and credential is a stand-in: nothing signs in or reaches a tenant."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import cli


@pytest.fixture(autouse=True)
def pc(tmp_path, monkeypatch):
    """This PC's data folder in tmp_path; no network connection may be opened."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setattr(
        "fabric_desktop.service.data_dir", lambda: tmp_path / "la" / "FabricDesktop"
    )

    def refuse(*args, **kwargs):
        raise AssertionError("the command line tests must not open network connections")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return tmp_path / "la" / "FabricDesktop"


def _exit(argv):
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    return stop.value.code


# --------------------------------------------------------------------------- run-schedule


def test_run_schedule_passes_the_trigger(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cli, "_run_schedule", lambda sid, trigger: seen.append((sid, trigger))
    )
    cli.main(["run-schedule", "sch1", "--manual"])
    cli.main(["run-schedule", "sch2"])
    assert seen == [("sch1", "manual"), ("sch2", "schedule")]


# --------------------------------------------------------------------------- smoke test


def test_smoke_test_dry_run_lists_the_platform_and_warehouse_flows(tmp_path, capsys):
    assert (
        _exit(
            [
                "smoke-test",
                "--workspace",
                "ws1",
                "--dry-run",
                "--platform",
                "--warehouse",
                "wh1",
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "The platform flows will:" in out
    assert "The warehouse check will:" in out
    assert out.rstrip().endswith(
        "Dry run: nothing was signed in to, created or written."
    )
    assert not (tmp_path / "out").exists()


def test_smoke_test_warehouse_flow_connects_with_the_signed_in_token(
    tmp_path, monkeypatch, capsys
):
    from fabric_desktop import (
        app_settings,
        auth,
        fabric_api,
        livetest,
        onelake,
        smoketest,
    )
    from fabric_desktop import warehouse as wh

    class Cred:
        def __init__(self, client_id):
            self.client_id = client_id

        def fabric_token(self):
            return "fabric-token"  # pragma: allowlist secret (a fake token)

        def storage_token(self):
            return "storage-token"  # pragma: allowlist secret (a fake token)

        def warehouse_token(self):
            return "wh-token"  # pragma: allowlist secret (a fake token)

    connected = []
    monkeypatch.setattr(app_settings, "value", lambda key: "client-1")
    monkeypatch.setattr(auth, "Authenticator", Cred)
    monkeypatch.setattr(
        fabric_api, "FabricClient", lambda token: SimpleNamespace(token=token)
    )
    monkeypatch.setattr(
        onelake, "FileStore", lambda token: SimpleNamespace(token=token)
    )
    monkeypatch.setattr(livetest, "policy_workspace_ids", lambda: {"PROTECTED"})
    monkeypatch.setattr(
        wh,
        "resolve",
        lambda fabric, ref: (
            connected.append(("resolve", ref))
            or SimpleNamespace(server="srv.fabric", database="db1")
        ),
    )
    monkeypatch.setattr(
        wh,
        "open_connection",
        lambda server, database, token: (
            connected.append(("open", server, database, token())) or "connection"
        ),
    )

    class Smoke:
        def __init__(self, workspace, credential, fabric, out, **kw):
            self.kw, self.out = kw, out

        def plan(self):
            return ["a step"]

        def run(self):
            check = self.kw["warehouse_check"](self.out / "warehouse")
            assert check.protected_ids == {"protected"}
            assert check.connect() == "connection"
            return {"outcome": "passed", "files": ["summary.md"]}

    monkeypatch.setattr(smoketest, "SmokeTest", Smoke)
    monkeypatch.setattr(
        smoketest, "summary", lambda result: f"summary: {result['outcome']}"
    )
    assert (
        _exit(
            [
                "smoke-test",
                "--workspace",
                "ws1",
                "--warehouse",
                "wh1",
                "--yes",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert connected == [
        ("resolve", {"workspace_id": "ws1", "item_id": "wh1", "kind": "warehouse"}),
        ("open", "srv.fabric", "db1", "wh-token"),
    ]
    out = capsys.readouterr().out
    assert "  - a step" in out and "summary: passed" in out and "summary.md" in out


# --------------------------------------------------------------------------- nb open


def test_nb_open_on_windows_asks_windows_to_open_the_link(
    tmp_path, monkeypatch, capsys
):
    nb = tmp_path / "a b.ipynb"
    nb.write_text("{}", encoding="utf-8")
    opened = []
    # the function imports os itself: a Windows os only for it (pathlib keeps the real one, so paths stay POSIX)
    monkeypatch.setitem(
        sys.modules,
        "os",
        SimpleNamespace(name="nt", startfile=opened.append, environ=os.environ),
    )
    code = cli._nb_open(
        argparse.Namespace(path=str(nb), path_from_env=None, print=False)
    )
    monkeypatch.setitem(sys.modules, "os", os)
    assert code == 0
    assert opened == [
        "kratosforge://notebook?path="
        + __import__("urllib.parse").parse.quote(nb.resolve().as_posix(), safe="")
    ]
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- templates


def test_templates_list_prints_each_template(capsys):
    from fabric_desktop import notebook_templates as nt

    assert _exit(["templates", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(nt.catalog())
    first = nt.catalog()[0]
    assert lines[0].endswith(
        f"{first['title']}: {first['description']} (v{first['version']})"
    )


def test_templates_new_refuses_a_name_that_isnt_ipynb(tmp_path, capsys):
    assert _exit(["templates", "new", "fabric", "silver", str(tmp_path / "x.txt")]) == 2
    assert capsys.readouterr().out == "The new notebook's name must end in .ipynb\n"


@pytest.mark.parametrize("text", ["not json", "[1]", '{"cells": 1}'])
def test_templates_check_refuses_a_file_that_isnt_a_notebook(tmp_path, capsys, text):
    path = tmp_path / "n.ipynb"
    path.write_text(text, encoding="utf-8")
    assert _exit(["templates", "check", str(path)]) == 2
    assert capsys.readouterr().out.startswith(f"Can't read {path}: ")


def test_templates_check_json_not_a_template_and_warning_and_upgrade(
    tmp_path, monkeypatch, capsys
):
    from fabric_desktop import notebook_templates as nt

    path = tmp_path / "n.ipynb"
    path.write_text('{"cells": []}', encoding="utf-8")
    monkeypatch.setattr(nt, "check", lambda nb: {"status": "not-a-template"})
    assert _exit(["templates", "check", str(path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "not-a-template"}
    assert _exit(["templates", "check", str(path)]) == 1
    assert (
        capsys.readouterr().out
        == f"{path}: not made from a template, so it is domain-owned\n"
    )
    monkeypatch.setattr(
        nt,
        "check",
        lambda nb: {
            "status": "conforms",
            "template": "silver",
            "version": "1.0",
            "platform": "fabric",
            "changes": ["one"],
            "warning": "the markers were edited",
            "upgrade_available": True,
            "latest_version": "1.1",
        },
    )
    assert _exit(["templates", "check", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{path}: silver 1.0 for fabric, conforms: the enterprise can support it",
        "  - one",
        "  ! the markers were edited",
        "  Version 1.1 of the template is out; copy it and carry your parameters over.",
    ]


def test_templates_accept_without_a_readable_record(tmp_path, capsys):
    path = tmp_path / "n.ipynb"
    path.write_text('{"cells": []}', encoding="utf-8")
    assert _exit(["templates", "accept", str(path), "--by", "it"]) == 2
    assert capsys.readouterr().out.startswith(
        f"Can't read the handover record {tmp_path / 'n.handover.json'}: "
    )


# --------------------------------------------------------------------------- governance check


def test_governance_check_prints_a_policys_note(monkeypatch, capsys):
    result = {
        "package_note": "Checked offline.",
        "project": "crm",
        "problems": ["one problem"],
        "counts": {
            "to_look_at": 0,
            "unknown": 0,
            "excepted": 0,
            "passing": 0,
            "policies": 1,
        },
        "policies": [
            {
                "status": "not_checked",
                "name": "Gone",
                "id": "g",
                "version": 2,
                "enforcement": "warn",
                "passed": 0,
                "checked": 0,
                "findings": [],
                "unknown": [],
                "note": "No checker on this PC has the rule x.",
            }
        ],
    }
    monkeypatch.setattr(
        cli,
        "Service",
        lambda headless=False: SimpleNamespace(
            governance_check_from_package=lambda **kw: dict(result)
        ),
    )
    monkeypatch.delenv("KRATOSFORGE_POLICY_PACKAGE_SHA256", raising=False)
    assert _exit(["governance", "check", "--expect-sha256", "abc"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:4] == [
        "Checked offline.",
        "not checked: Gone (g v2, warn)",
        "  No checker on this PC has the rule x.",
        "note: one problem",
    ]


# --------------------------------------------------------------------------- pipeline


def test_pipeline_inspect_prints_json_and_exits_on_ok(monkeypatch, capsys):
    from fabric_desktop.pipeline_service import PipelineMixin

    monkeypatch.setattr(
        PipelineMixin,
        "pipeline_inspect",
        lambda self, path: {"ok": False, "path": path},
    )
    assert _exit(["pipeline", "inspect", "p.json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "path": "p.json"}


def test_pipeline_compat_where_everything_runs_on_this_pc(monkeypatch, capsys):
    from fabric_desktop.pipeline_service import PipelineMixin

    report = {
        "ok": True,
        "activities": [],
        "diagnostics": [],
        "counts": {"error": 0, "warning": 0},
        "summary": {
            "overall": "local",
            "runs_on_this_pc": True,
            "needs_fabric_capacity": [],
            "fabric_only": [],
            "activities": 0,
        },
    }
    monkeypatch.setattr(
        PipelineMixin, "pipeline_compatibility", lambda self, path: report
    )
    assert _exit(["pipeline", "compat", "p.json"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "overall: local; runs on this PC: yes; needs Fabric capacity: 0 of 0 activities",
        "0 errors, 0 warnings, 0 notes",
    ]


def test_pipeline_schedule_without_task_scheduler_says_it_wont_fire(
    tmp_path, monkeypatch, capsys
):
    added = []

    class Svc:
        def add_pc_schedule(self, target, schedule):
            added.append((target, schedule))
            return {
                "schedule": {"id": "s1", "target": {"name": "orders"}},
                "windows": False,
            }

    monkeypatch.setattr(cli, "Service", Svc)
    assert (
        _exit(
            [
                "pipeline",
                "schedule",
                str(tmp_path / "orders.DataPipeline"),
                "--every-minutes",
                "15",
            ]
        )
        == 0
    )
    assert added[0][1] == {"kind": "cron", "every_minutes": 15}
    assert capsys.readouterr().out.splitlines() == [
        "Scheduled orders on this PC (s1).",
        "Windows Task Scheduler isn't available here, so it won't fire until the app runs on Windows.",
    ]


def _wait_pipeline(path, name):
    path.write_text(
        json.dumps(
            {
                "name": name,
                "properties": {
                    "activities": [
                        {
                            "name": "Pause",
                            "type": "Wait",
                            "dependsOn": [],
                            "typeProperties": {"waitTimeInSeconds": 1},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pipeline_run_reads_each_child_pipeline(tmp_path, capsys):
    parent = _wait_pipeline(tmp_path / "parent.json", "parent")
    child = _wait_pipeline(tmp_path / "child.json", "child")
    assert (
        _exit(
            [
                "pipeline",
                "run",
                str(parent),
                "--child",
                f"child={child}",
                "--wait-scale",
                "0",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"
    assert (
        _exit(
            [
                "pipeline",
                "run",
                str(parent),
                "--child",
                f"child={tmp_path / 'missing.json'}",
            ]
        )
        == 2
    )
    assert capsys.readouterr().out.startswith("error: ")


def test_pipeline_run_stopped_with_ctrl_c_stops_its_notebooks(
    tmp_path, monkeypatch, capsys
):
    from fabric_desktop import pipeline_notebooks
    from fabric_desktop.orchestration import runner

    calls = []

    class Notebooks:
        def __init__(self, svc, run_id, names, allow, keep):
            calls.append(("made", names))

        def lakehouses(self, acts):
            return False

        def stop(self):
            calls.append("stop")

        def finish(self):
            calls.append("finish")

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "Service", lambda: object())
    monkeypatch.setattr(pipeline_notebooks, "LocalNotebooks", Notebooks)
    monkeypatch.setattr(runner.PipelineRun, "check", lambda self: [])
    monkeypatch.setattr(runner.PipelineRun, "run", interrupted)
    path = _wait_pipeline(tmp_path / "p.json", "p")
    assert (
        _exit(["pipeline", "run", str(path), "--notebook", "nb=Filter", "--json"]) == 1
    )
    assert calls == [("made", {"nb": "Filter"}), "stop", "finish"]
    assert capsys.readouterr().out == "cancelled\n"


# --------------------------------------------------------------------------- audit


def test_audit_refuses_folder_and_agent_home_together(tmp_path, capsys):
    assert (
        _exit(["audit", "--folder", str(tmp_path), "--agent-home", str(tmp_path)]) == 2
    )
    assert capsys.readouterr().err == "Give --folder or --agent-home, not both\n"


def test_audit_export_of_this_pcs_trail_records_it_and_verifies(pc, tmp_path, capsys):
    from fabric_desktop.audit import AuditLog

    AuditLog(pc / "audit").record("meta_run", "ok", 0.1, {"env": "dev"})
    out = tmp_path / "export.jsonl"
    assert _exit(["audit", "--export", str(out)]) == 0
    made = json.loads(capsys.readouterr().out)
    assert made["lines"] == 1
    assert [e["action"] for e in AuditLog(pc / "audit").recent(5)][0] == "audit_export"
    assert _exit(["audit", "--verify-export", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (
        _exit(["audit", "--export", str(out)]) == 2
    )  # already there, and --replace wasn't given
    assert capsys.readouterr().err.strip()


def test_audit_export_still_made_when_the_export_line_cant_be_recorded(
    pc, tmp_path, monkeypatch, capsys
):
    from fabric_desktop.audit import AuditLog

    AuditLog(pc / "audit").record("meta_run", "ok", 0.1, {"env": "dev"})

    def broken(self, *a, **kw):
        raise PermissionError("locked")

    monkeypatch.setattr(AuditLog, "record", broken)
    out = tmp_path / "export.jsonl"
    assert _exit(["audit", "--export", str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.err == "audit: could not record the export: PermissionError\n"
    assert json.loads(captured.out)["lines"] == 1 and Path(out).exists()
