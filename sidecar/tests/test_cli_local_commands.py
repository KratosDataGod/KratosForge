"""The command line's local commands (cli.py): what each prints and the exit code it gives, with the
app's service, the agent's loop and the servers stood in. Nothing here signs in, starts a server or
reaches Fabric."""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import agent_config, cli
from fabric_desktop.meta.templates import init_project
from test_agent import config_text


@pytest.fixture(autouse=True)
def pc(tmp_path, monkeypatch):
    """This PC's data folder, empty and in the current data format."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: tmp_path / "la" / "FabricDesktop")
    return tmp_path / "la" / "FabricDesktop"


def _exit(argv):
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    return stop.value.code


# ---- the commands that start something long-running

def test_mcp_integrations_and_serve_start_their_servers(monkeypatch):
    from fabric_desktop.integrations import http_server, mcp_server
    started = []
    monkeypatch.setattr(mcp_server, "run_stdio", lambda client: started.append(("mcp", client)))
    monkeypatch.setattr(http_server, "serve", lambda port, parent: started.append(("http", port, parent)) or 0)
    monkeypatch.setattr(cli, "serve", lambda: started.append(("serve",)))
    cli.main(["mcp", "--client", "vscode"])
    assert _exit(["integrations", "serve", "--port", "47700", "--parent-pid", "12"]) == 0
    cli.main(["serve"])
    assert started == [("mcp", "vscode"), ("http", 47700, 12), ("serve",)]


# ---- jobs, schedules and Home's Run several, with the service stood in

class FakeService:
    made: list[dict] = []
    result: dict = {"status": "ok"}
    audit_fails = False

    def __init__(self, headless=False):
        FakeService.made.append({"headless": headless})
        self.recorded = []
        self._auth = SimpleNamespace(signed_in_user=lambda: "chris@contoso.com")
        svc = self

        class Audit:
            def record(self, *a, **k):
                if FakeService.audit_fails:
                    raise OSError("audit folder is read-only")
                svc.recorded.append((a, k))
        self.audit = Audit()
        FakeService.last = self

    def sign_in(self):
        return {"signed_in": True}

    def preview_job(self, job, limit):
        return {"job": job, "limit": limit}

    def run_job(self, job):
        return {"ran": job}

    def history(self):
        return [{"job": "orders"}]

    def run_schedule(self, schedule_id, trigger):
        return dict(FakeService.result, schedule=schedule_id, trigger=trigger)

    def run_notebook_batch(self, batch_id):
        FakeService.batch = batch_id

    def run_batch_notebook(self, batch_id, index):
        return dict(FakeService.result, batch=batch_id, index=index)


@pytest.fixture
def service(monkeypatch):
    FakeService.made, FakeService.result, FakeService.audit_fails = [], {"status": "ok"}, False
    monkeypatch.setattr(cli, "Service", FakeService)
    return FakeService


@pytest.mark.parametrize("argv, expected", [
    (["sign-in"], {"signed_in": True}),
    (["preview", "jobs/orders.toml"], {"job": "jobs/orders.toml", "limit": 10}),
    (["run", "jobs/orders.toml"], {"ran": "jobs/orders.toml"}),
    (["history"], [{"job": "orders"}]),
])
def test_job_commands_print_the_services_answer_as_json(service, capsys, argv, expected):
    cli.main(argv)
    assert json.loads(capsys.readouterr().out) == expected
    assert service.made == [{"headless": False}]


def test_a_schedule_runs_headless_and_is_audited_with_its_outcome(service, capsys):
    assert _exit(["run-schedule", "0a1b2c3d4e5f", "--manual"]) == 0
    assert service.made == [{"headless": True}]
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["trigger"] == "manual" and out["schedule"] == "0a1b2c3d4e5f"
    [(args, kw)] = service.last.recorded
    assert args[0] == "run_schedule" and args[3] == {"schedule_id": "0a1b2c3d4e5f", "trigger": "manual"}
    assert kw["who"] == "chris@contoso.com" and kw["error"] == ""


def test_a_failed_schedule_exits_1_and_its_audit_line_names_the_error(service, capsys):
    service.result = {"status": "failed", "error": "The lakehouse wasn't found"}
    assert _exit(["run-schedule", "0a1b2c3d4e5f"]) == 1
    [(args, kw)] = service.last.recorded
    assert args[3]["trigger"] == "schedule" and kw["error"] == "The lakehouse wasn't found"


def test_a_schedule_whose_audit_line_cant_be_written_still_reports_its_run(service, capsys):
    service.audit_fails = True
    assert _exit(["run-schedule", "0a1b2c3d4e5f"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "audit: OSError: audit folder is read-only"
    assert json.loads(lines[-1])["status"] == "ok"


def test_with_no_console_a_schedule_logs_to_the_engine_log(service, monkeypatch):
    from fabric_desktop import server
    log = io.StringIO()
    monkeypatch.setattr(sys, "executable", "C:\\Program Files\\KratosForge\\python\\pythonw.exe")
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(server, "_log_file", lambda: log)
    assert _exit(["run-schedule", "0a1b2c3d4e5f"]) == 0
    assert sys.stdout is log and json.loads(log.getvalue().strip())["status"] == "ok"


def test_run_notebooks_runs_the_batch_and_run_notebook_prints_its_row_last(service, capsys, monkeypatch):
    from fabric_desktop import server
    log = io.StringIO()
    monkeypatch.setattr(sys, "executable", "pythonw.exe")
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(server, "_log_file", lambda: log)
    cli.main(["run-notebooks", "b-1"])
    assert service.batch == "b-1" and service.made == [{"headless": True}] and sys.stdout is log
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    service.result = {"status": "failed"}
    assert _exit(["run-notebook", "b-1", "2"]) == 1
    assert json.loads(sys.stdout.getvalue().strip().splitlines()[-1]) == {"status": "failed", "batch": "b-1", "index": 2}


# ---- this PC's data: local copies and backups

def test_clean_local_says_what_it_deleted_or_would_delete(monkeypatch, capsys, pc):
    from fabric_desktop import retention
    calls = []

    def sweep(root, days, dry_run=False):
        calls.append((root, days, dry_run))
        return {"removed": ["lakehouses/a", "lakehouses/b"], "bytes": 2_500_000}
    monkeypatch.setattr(retention, "sweep", sweep)
    cli.main(["clean-local", "--days", "7", "--dry-run"])
    cli.main(["clean-local"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Would delete 2 local copies (2.5 MB) unused for 7 days" and out[1] == "  lakehouses/a"
    assert out[3].startswith("Deleted 2 local copies (2.5 MB) unused for ")
    assert calls == [(pc, 7, True), (pc, 30, False)]  # without --days, the local_retention_days setting (30)
    assert out[3] == "Deleted 2 local copies (2.5 MB) unused for 30 days"


def test_restore_refuses_a_file_that_isnt_a_backup(tmp_path, capsys):
    bad = tmp_path / "not-a-backup.zip"
    bad.write_text("hello")
    assert _exit(["restore", str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("This backup can't be restored:") and "isn't a zip file" in out


# ---- the agent

@pytest.fixture
def agent_toml(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "home"), encoding="utf-8")
    return path


def test_an_agent_config_that_cant_be_read_exits_2(tmp_path, capsys):
    assert _exit(["agent", "--config", str(tmp_path / "missing.toml"), "status"]) == 2
    assert "missing.toml" in capsys.readouterr().out


def test_the_agent_refuses_a_home_saved_by_a_newer_build(agent_toml, monkeypatch, capsys):
    from fabric_desktop import data_format

    def newer(home):
        raise data_format.NewerData("saved by a newer KratosForge")
    monkeypatch.setattr(data_format, "check", newer)
    for command in (["run"], ["enqueue", "dev"], ["requeue", "j1"], ["prune"]):
        assert _exit(["agent", "--config", str(agent_toml), *command]) == 2, command
    assert capsys.readouterr().out.count("saved by a newer KratosForge") == 4


def test_agent_run_runs_until_stopped_and_refuses_a_folder_another_agent_holds(agent_toml, monkeypatch, capsys):
    from fabric_desktop.agent import Agent
    from fabric_desktop.mailbox import ControlFolderInUse
    ran = []
    monkeypatch.setattr(Agent, "run_forever", lambda self: ran.append(self.config.name))
    assert _exit(["agent", "--config", str(agent_toml), "run"]) == 0
    assert ran == ["etl-01"]

    def held(self):
        raise ControlFolderInUse("etl-02 on PC-7 already uses this control folder")
    monkeypatch.setattr(Agent, "run_forever", held)
    assert _exit(["agent", "--config", str(agent_toml), "run"]) == 2
    assert "already uses this control folder" in capsys.readouterr().out


def test_agent_check_prints_the_check_or_why_it_failed(agent_toml, monkeypatch, capsys):
    from fabric_desktop.runtime import Runtime
    monkeypatch.setattr(Runtime, "check", lambda self: {"project": "ok"})
    assert _exit(["agent", "--config", str(agent_toml), "check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"project": "ok"}

    def broken(self):
        raise RuntimeError("the sign-in was refused")
    monkeypatch.setattr(Runtime, "check", broken)
    assert _exit(["agent", "--config", str(agent_toml), "check"]) == 1
    assert capsys.readouterr().out.strip() == "Check failed: RuntimeError: the sign-in was refused"


def test_agent_prune_refuses_negative_days_and_requeue_an_unknown_job(agent_toml, capsys):
    assert _exit(["agent", "--config", str(agent_toml), "prune", "--days", "-1"]) == 2
    assert capsys.readouterr().out.strip() == "--days: a number of at least 0"
    assert _exit(["agent", "--config", str(agent_toml), "requeue", "no-such-job"]) == 1
    assert capsys.readouterr().out.strip()
    assert agent_config.load(agent_toml).name == "etl-01"


# ---- nb open and nb validate (VS Code's tasks run these)

def _notebook(path, cells, compute=None):
    meta = {"kernelspec": {"name": "python3", "display_name": "Python 3"}}
    if compute:
        meta["fabric_desktop"] = {"compute": compute}
    path.write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": meta, "cells": [
        {"cell_type": "code", "source": src, "metadata": {}, "outputs": [], "execution_count": None} for src in cells]}),
        encoding="utf-8")
    return path


def test_nb_open_prints_a_kratosforge_link_for_an_ipynb_file(tmp_path, capsys):
    nb = _notebook(tmp_path / "a&b.ipynb", ["print(1)"])
    assert _exit(["nb", "open", str(nb), "--print"]) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith("kratosforge://notebook?path=") and "a%26b.ipynb" in link and "/" not in link.split("=", 1)[1]


def test_nb_open_refuses_what_isnt_an_ipynb_file_and_a_missing_variable(tmp_path, capsys, monkeypatch):
    (tmp_path / "notes.txt").write_text("hi")
    assert _exit(["nb", "open", str(tmp_path / "notes.txt"), "--print"]) == 2
    assert "isn't an .ipynb file" in capsys.readouterr().err
    monkeypatch.delenv("KF_FILE", raising=False)
    assert _exit(["nb", "open", "--path-from-env", "KF_FILE"]) == 2
    assert capsys.readouterr().err.strip() == "Name an .ipynb file: KF_FILE isn't set."
    assert _exit(["nb", "validate"]) == 2
    assert capsys.readouterr().err.strip() == "Name an .ipynb file."


def test_nb_validate_reads_the_path_from_the_environment_and_says_when_it_isnt_a_notebook(tmp_path, capsys, monkeypatch):
    bad = tmp_path / "bad.ipynb"
    bad.write_text(json.dumps({"cells": "nope"}))
    monkeypatch.setenv("KF_FILE", str(bad))
    assert _exit(["nb", "validate", "--path-from-env", "KF_FILE"]) == 2
    assert capsys.readouterr().out.strip() == (f"{bad.resolve()}:1:1: error: KratosForge can't read this notebook: "
                                               "it isn't a Jupyter notebook (.ipynb) file")


def test_nb_validate_prints_one_line_per_finding_and_counts(tmp_path, capsys):
    nb = _notebook(tmp_path / "load.ipynb", ["%run Helper", "x = 1"])
    _notebook(tmp_path / "Helper.ipynb", ["y = 2"])  # %run finds the notebook beside it
    assert _exit(["nb", "validate", str(nb)]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [
        f"{nb.resolve()}:1:1: warning: Cell 1 runs another notebook (line 1). A clean run runs it too, in the same "
        "fresh kernel, with its table writes in the same sandbox.",
        "0 errors, 1 warnings, 0 notes (Runs on isn't set, so what stops either place is a warning)"]


def test_nb_validate_exits_1_on_an_error_and_prints_json_when_asked(tmp_path, capsys):
    nb = _notebook(tmp_path / "orders.ipynb", ["spark.read.table('orders')"])
    assert _exit(["nb", "validate", str(nb)]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == f"{nb.resolve()}:1:1: error: No lakehouse is attached. The cells read or write lakehouse tables " \
        "or files. Attach a lakehouse with Add data items."
    assert lines[-1].startswith("1 errors, ")
    assert _exit(["nb", "validate", str(nb), "--json", "--runs-on", "pc"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["path"] == str(nb.resolve()) and out["runs_on"] == "pc" and out["counts"]["error"] == 1
