"""The command line's tenant commands (cli.py), the paths past a dry run: smoke-test, live-test,
live-test-databricks and databricks-discover asking before they run and mapping each outcome to its exit
code; pipeline run refusing a definition it can't read and stopping on Ctrl+C. Sign-in, Fabric, OneLake,
Databricks and the tests themselves are stand-ins: nothing here signs in or reaches a tenant."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import cli


@pytest.fixture(autouse=True)
def pc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: tmp_path / "la" / "FabricDesktop")


def _exit(argv):
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    return stop.value.code


def _answer(monkeypatch, reply):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or reply)
    return asked


@pytest.fixture
def signed_in(monkeypatch):
    """Fabric's sign-in, client and OneLake as stand-ins; ``made`` records what each was given."""
    from fabric_desktop import app_settings, auth, fabric_api, onelake
    made = SimpleNamespace(clients=[], stores=[])

    class Credential:
        def __init__(self, client_id=None):
            made.client_id = client_id
            made.credential = self

        def fabric_token(self):
            return "fabric"

        def storage_token(self):
            return "storage"

        def _token(self, scopes):
            return "execute"

        def signed_in_user(self):
            return "chris@contoso.com"
    monkeypatch.setattr(app_settings, "value", lambda name: "client-1" if name == "client_id" else None)
    monkeypatch.setattr(auth, "Authenticator", Credential)
    monkeypatch.setattr(fabric_api, "FabricClient", lambda token: made.clients.append(token()) or "fabric-client")
    monkeypatch.setattr(onelake, "FileStore", lambda token: made.stores.append(token()) or "file-store")
    return made


class FakeTest:
    """A live or smoke test: the steps it plans and the result it runs to."""
    outcome = "passed"

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        FakeTest.last = self

    def plan(self):
        return ["sign in", "write a table"]

    def run(self):
        return {"outcome": FakeTest.outcome, "files": ["results/report.md"]}


# ---- smoke-test


def test_smoke_test_runs_its_live_and_platform_tests_as_the_signed_in_person(signed_in, monkeypatch, capsys):
    from fabric_desktop import livetest, platformtest, smoketest
    from fabric_desktop.central import client as central
    made = {}

    class Smoke(FakeTest):
        def run(self):
            made["live"] = self.kwargs["live_test"]("out/live")
            made["platform"] = self.kwargs["platform_test"]("out/platform")
            return {"outcome": "failed", "files": ["out/smoke.md"]}
    monkeypatch.setattr(smoketest, "SmokeTest", Smoke)
    monkeypatch.setattr(smoketest, "summary", lambda result: f"smoke {result['outcome']}")
    monkeypatch.setattr(livetest, "LiveTest", FakeTest)
    monkeypatch.setattr(livetest, "policy_workspace_ids", lambda: {"prod-ws"})
    monkeypatch.setattr(platformtest, "PlatformTest", FakeTest)
    monkeypatch.setattr(central, "FabricPlatform", lambda execute, ws, fid: ("platform", execute(), ws, fid))
    asked = _answer(monkeypatch, "Y")
    assert _exit(["smoke-test", "--workspace", "ws-test", "--platform"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("The smoke test will:\n  - sign in\n  - write a table\n")
    assert out.endswith("smoke failed\nout/smoke.md\n") and asked == ["Run it? [y/N] "]
    assert signed_in.client_id == "client-1" and signed_in.clients == ["fabric"]
    live = made["live"]
    assert live.args[0] == "ws-test" and live.args[2] == "file-store" and live.kwargs["protected_ids"] == {"prod-ws"}
    platform = made["platform"]
    _ws, _fabric, execute, out_dir = platform.args
    assert execute() == "execute" and out_dir == "out/platform"
    assert platform.kwargs["client"]("ws-test", "f1") == ("platform", "execute", "ws-test", "f1")
    assert platform.kwargs["user"]() == "chris@contoso.com" and platform.kwargs["protected_ids"] == {"prod-ws"}
    pause = platform.kwargs["pause"]
    assert pause("Approve the request in Fabric, then press Enter.") == "Y"  # without --yes it waits for Enter


def test_smoke_test_not_confirmed_runs_nothing(signed_in, monkeypatch, capsys):
    from fabric_desktop import smoketest
    ran = []

    class Smoke(FakeTest):
        def run(self):
            ran.append(1)
    monkeypatch.setattr(smoketest, "SmokeTest", Smoke)
    _answer(monkeypatch, "")
    assert _exit(["smoke-test", "--workspace", "ws-test"]) == 2
    assert capsys.readouterr().out.endswith("Not run.\n") and ran == []
    assert Smoke.last.kwargs["platform_test"] is None


# ---- live-test


def test_live_test_needs_a_workspace(capsys):
    assert _exit(["live-test"]) == 2
    assert "--workspace" in capsys.readouterr().out


@pytest.mark.parametrize("outcome, code", [("passed", 0), ("failed", 1), ("refused", 2)])
def test_live_test_maps_its_outcome_to_the_exit_code(signed_in, monkeypatch, capsys, outcome, code):
    from fabric_desktop import livetest
    monkeypatch.setattr(livetest, "LiveTest", FakeTest)
    monkeypatch.setattr(livetest, "summary", lambda result: f"live {result['outcome']}")
    monkeypatch.setattr(livetest, "policy_workspace_ids", lambda: set())
    monkeypatch.setattr(FakeTest, "outcome", outcome)
    assert _exit(["live-test", "--workspace", "ws-test", "--yes", "--check", "notebook.publish"]) == code
    assert capsys.readouterr().out.endswith(f"live {outcome}\nresults/report.md\n")
    assert FakeTest.last.kwargs["extra"] == {"notebook.publish"} and signed_in.stores == ["storage"]


def test_live_test_not_confirmed_runs_nothing(signed_in, monkeypatch, capsys):
    from fabric_desktop import livetest
    monkeypatch.setattr(livetest, "LiveTest", FakeTest)
    monkeypatch.setattr(livetest, "policy_workspace_ids", lambda: set())
    monkeypatch.setattr(FakeTest, "run", lambda self: pytest.fail("ran without a yes"))
    _answer(monkeypatch, "no")
    assert _exit(["live-test", "--workspace", "ws-test"]) == 2
    assert capsys.readouterr().out.endswith("Not run.\n")


@pytest.mark.parametrize("left, code", [([], 0), (["kf_live_1"], 1)])
def test_live_test_cleanup_signs_in_with_the_agents_config_and_says_whats_left(signed_in, monkeypatch, capsys,
                                                                             tmp_path, left, code):
    from fabric_desktop import agent_config, credentials, livetest
    loaded = []
    monkeypatch.setattr(agent_config, "load", lambda path: loaded.append(path) or SimpleNamespace(auth={"kind": "sp"}))
    monkeypatch.setattr(credentials, "from_config", lambda auth: SimpleNamespace(fabric_token=lambda: f"agent-{auth['kind']}"))
    cleaned = []
    monkeypatch.setattr(livetest, "cleanup_from_ledger",
                        lambda ledger, fabric, credential: cleaned.append((ledger, fabric)) or {"left_behind": left})
    ledger = tmp_path / "ledger.json"
    assert _exit(["live-test", "--cleanup", str(ledger), "--agent-config", "agent.toml"]) == code
    assert loaded == ["agent.toml"] and signed_in.clients == ["agent-sp"]
    assert cleaned == [(ledger, "fabric-client")] and '"left_behind"' in capsys.readouterr().out


# ---- live-test-databricks and databricks-discover


@pytest.fixture
def dbx(monkeypatch):
    from fabric_desktop import dbx_livetest
    from fabric_desktop.engines import databricks_auth, databricks_sql, dbconnect
    made = SimpleNamespace(checks=[(True, "Java 17")], providers=[])

    def provider(connection):
        made.providers.append(connection)
        if getattr(made, "refuse", None):
            raise databricks_auth.DatabricksAuthError(made.refuse)
        return SimpleNamespace(host="https://adb-1.azuredatabricks.net")
    monkeypatch.setattr(dbx_livetest, "pc_checks", lambda connection: made.checks)
    monkeypatch.setattr(databricks_auth, "provider_for", provider)
    monkeypatch.setattr(databricks_sql, "Client", lambda tokens: ("client", tokens.host))
    monkeypatch.setattr(databricks_sql, "DatabricksSqlAdapter", lambda client_for: ("sql", client_for(None)))
    monkeypatch.setattr(dbconnect, "DatabricksConnectAdapter", lambda: "connect")
    monkeypatch.setattr(dbx_livetest, "DatabricksLiveTest", FakeTest)
    monkeypatch.setattr(dbx_livetest, "summary", lambda result: f"dbx {result['outcome']}")
    return made


def test_databricks_live_test_wont_run_on_a_pc_that_isnt_ready(dbx, capsys):
    dbx.checks = [(True, "Python 3.12"), (False, "Java 17 for Databricks Connect")]
    assert _exit(["live-test-databricks", "--warehouse", "wh1", "--profile", "dev"]) == 2
    out = capsys.readouterr().out
    assert "  NO  Java 17 for Databricks Connect" in out and out.endswith("Nothing was signed in to or started.\n")
    assert dbx.providers == []


def test_databricks_live_test_stops_when_sign_in_is_refused(dbx, capsys):
    dbx.refuse = "Sign in with the Databricks CLI first: databricks auth login --profile dev"
    assert _exit(["live-test-databricks", "--warehouse", "wh1", "--profile", "dev"]) == 2
    assert capsys.readouterr().out.endswith("databricks auth login --profile dev\n")


def test_databricks_live_test_runs_on_the_cluster_after_a_yes(dbx, monkeypatch, capsys):
    asked = _answer(monkeypatch, "yes")
    monkeypatch.setattr(FakeTest, "outcome", "failed")
    assert _exit(["live-test-databricks", "--warehouse", "wh1", "--host", "https://adb-1.azuredatabricks.net",
                  "--cluster", "c1"]) == 1
    test = FakeTest.last
    connection, warehouse, _tokens, client, sql = test.args[:5]
    assert connection["credential"] == {"method": "oauth-u2m"} and connection["host"] == "https://adb-1.azuredatabricks.net"
    assert (warehouse, client, sql) == ("wh1", ("client", "https://adb-1.azuredatabricks.net"),
                                        ("sql", ("client", "https://adb-1.azuredatabricks.net")))
    assert test.kwargs["connect_adapter"] == "connect" and test.kwargs["cluster_id"] == "c1"
    assert "bills DBUs" in asked[0] and capsys.readouterr().out.endswith("dbx failed\nresults/report.md\n")


def test_databricks_live_test_not_confirmed_runs_nothing(dbx, monkeypatch, capsys):
    _answer(monkeypatch, "n")
    monkeypatch.setattr(FakeTest, "run", lambda self: pytest.fail("ran without a yes"))
    assert _exit(["live-test-databricks", "--warehouse", "wh1", "--profile", "dev"]) == 2
    assert FakeTest.last.kwargs["connect_adapter"] is None and capsys.readouterr().out.endswith("Not run.\n")


def test_discover_stops_when_sign_in_is_refused_or_didnt_happen(dbx, monkeypatch, capsys):
    from fabric_desktop.engines import databricks_discovery
    dbx.refuse = "Sign in with the Databricks CLI first."
    assert _exit(["databricks-discover", "--profile", "dev"]) == 2
    assert capsys.readouterr().out == "Sign in with the Databricks CLI first.\n"
    dbx.refuse = None

    class Discovery:
        def __init__(self, client, catalogs):
            pass

        def run(self):
            return {"signed_in": False, "parts": {}}
    monkeypatch.setattr(databricks_discovery, "Discovery", Discovery)
    monkeypatch.setattr(databricks_discovery, "summary_lines", lambda result: ["Not signed in."])
    assert _exit(["databricks-discover", "--host", "https://adb-1.azuredatabricks.net"]) == 2
    assert capsys.readouterr().out == "Not signed in.\n"


# ---- pipeline run


def test_a_pipeline_that_cant_be_read_isnt_run(tmp_path, capsys):
    assert _exit(["pipeline", "run", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().out.startswith("error: ")
    good = tmp_path / "pipeline-content.json"
    good.write_text('{"properties": {"activities": []}}', encoding="utf-8")
    bad_mocks = tmp_path / "mocks.json"
    bad_mocks.write_text("{not json", encoding="utf-8")
    assert _exit(["pipeline", "run", str(good), "--mocks", str(bad_mocks)]) == 2
    assert capsys.readouterr().out.startswith("error: ")


def test_ctrl_c_cancels_the_run(tmp_path, monkeypatch, capsys):
    from fabric_desktop.orchestration import runner
    good = tmp_path / "pipeline-content.json"
    good.write_text('{"properties": {"activities": []}}', encoding="utf-8")
    cancelled = []

    def interrupted(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(runner.PipelineRun, "run", interrupted)
    monkeypatch.setattr(runner.PipelineRun, "cancel", lambda self: cancelled.append(True))
    assert _exit(["pipeline", "run", str(good)]) == 1
    assert capsys.readouterr().out.endswith("cancelled\n") and cancelled == [True]


# ---- nb open on Windows


@pytest.mark.skipif(os.name != "nt", reason="Windows opens the link; elsewhere it's printed")
def test_nb_open_asks_windows_to_open_the_link(tmp_path, monkeypatch, capsys):
    nb = tmp_path / "a&b.ipynb"
    nb.write_text("{}", encoding="utf-8")
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    assert _exit(["nb", "open", str(nb)]) == 0
    assert opened == ["kratosforge://notebook?path=" + __import__("urllib.parse").parse.quote(nb.resolve().as_posix(), safe="")]
    assert capsys.readouterr().out == ""
    assert sys.platform == "win32"
