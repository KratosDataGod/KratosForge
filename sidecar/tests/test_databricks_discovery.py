"""Databricks workspace discovery against a fake of the documented REST APIs: nothing reaches a
workspace, every request is a GET, and a part that can't be read never hides the others."""

from __future__ import annotations

import functools
import json

import pytest

from fabric_desktop import cli
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_discovery as disc
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import dbconnect

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".eyJ" + "zdWIiOiIxIn0" + "." + "c2lnbmF0dXJl" * 3

WAREHOUSES = [
    {"id": "wh-run", "name": "Shared SQL", "state": "RUNNING", "cluster_size": "Small", "auto_stop_mins": 10,
     "warehouse_type": "PRO", "enable_serverless_compute": True},
    {"id": "wh-stop", "name": "Big SQL", "state": "STOPPED", "cluster_size": "Large", "auto_stop_mins": 0},
]
CLUSTERS = [
    {"cluster_id": "c-ok", "cluster_name": "dev", "state": "TERMINATED", "spark_version": "16.4.x-scala2.12",
     "data_security_mode": "SINGLE_USER", "autotermination_minutes": 30, "cluster_source": "UI"},
    {"cluster_id": "c-old", "cluster_name": "old", "state": "RUNNING", "spark_version": "14.3.x-scala2.12",
     "data_security_mode": "USER_ISOLATION", "autotermination_minutes": 60, "cluster_source": "UI"},
    {"cluster_id": "c-none", "cluster_name": "no uc", "state": "RUNNING", "spark_version": "16.4.x-scala2.12",
     "data_security_mode": "NONE", "autotermination_minutes": 60, "cluster_source": "API"},
    {"cluster_id": "c-job", "cluster_name": "job-1", "state": "RUNNING", "spark_version": "16.4.x-scala2.12",
     "data_security_mode": "SINGLE_USER", "autotermination_minutes": 0, "cluster_source": "JOB"},
]


@pytest.fixture(autouse=True)
def app_python(monkeypatch):
    """The app ships Python 3.12, whatever runs the tests."""
    monkeypatch.setattr(dbconnect, "check", functools.partial(dbconnect.check, python="3.12"))


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class FakeWorkspace:
    """Answers the discovery GETs; ``refuse`` maps a path to an HTTP status; ``pages`` splits clusters."""

    def __init__(self, refuse=None, pages=False):
        self.calls = []
        self.refuse = refuse or {}
        self.pages = pages

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append((method, path, dict(params or {})))
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if path in self.refuse:
            return Resp(self.refuse[path], {"message": f"denied for token {TOKEN}"})
        if path == "/api/2.0/preview/scim/v2/Me":
            return Resp(body={"userName": "dev@contoso.com", "displayName": "Dev"})
        if path == "/api/2.0/sql/warehouses":
            return Resp(body={"warehouses": WAREHOUSES})
        if path == "/api/2.1/clusters/list":
            if self.pages and not (params or {}).get("page_token"):
                return Resp(body={"clusters": CLUSTERS[:2], "next_page_token": "p2"})
            return Resp(body={"clusters": CLUSTERS[2:] if self.pages else CLUSTERS})
        if path == "/api/2.1/unity-catalog/metastore_summary":
            return Resp(body={"metastore_id": "ms1", "name": "westeurope", "region": "westeurope",
                              "default_catalog_name": "main"})
        if path == "/api/2.1/unity-catalog/catalogs":
            return Resp(body={"catalogs": [{"name": "main", "catalog_type": "MANAGED_CATALOG"},
                                           {"name": "samples", "catalog_type": "SYSTEM_CATALOG"}]})
        return Resp(404, {"message": "no such path"})


def tokens():
    return dba.TokenProvider(HOST, lambda: (TOKEN, None), "test")


def discover(ws, **kw):
    return disc.Discovery(dbsql.Client(tokens(), ws), **kw).run()


def test_every_request_is_a_get_on_a_documented_path_so_nothing_starts():
    ws = FakeWorkspace()
    result = discover(ws)
    assert {m for m, _, _ in ws.calls} == {"GET"}
    assert [p for _, p, _ in ws.calls] == ["/api/2.0/preview/scim/v2/Me", "/api/2.0/sql/warehouses",
                                           "/api/2.1/clusters/list", "/api/2.1/unity-catalog/metastore_summary",
                                           "/api/2.1/unity-catalog/catalogs"]
    assert result["schema"] == disc.SCHEMA and result["host"] == HOST and result["signed_in"]
    assert all(p["ok"] for p in result["parts"].values())
    assert result["parts"]["identity"]["value"] == {"user": "dev@contoso.com", "name": "Dev"}
    assert result["parts"]["metastore"]["value"]["default_catalog"] == "main"
    assert [c["name"] for c in result["parts"]["catalogs"]["value"]] == ["main", "samples"]


def test_warehouses_say_when_there_is_no_auto_stop():
    whs = {w["id"]: w for w in discover(FakeWorkspace())["parts"]["warehouses"]["value"]}
    assert whs["wh-run"]["serverless"] and whs["wh-run"]["auto_stop_mins"] == 10 and not whs["wh-run"]["notes"]
    assert whs["wh-stop"]["notes"] == ["No auto-stop: once started, it bills DBUs until someone stops it."]


def test_clusters_say_whether_databricks_connect_can_use_them_and_job_clusters_are_left_out():
    cls = {c["id"]: c for c in discover(FakeWorkspace())["parts"]["clusters"]["value"]}
    assert set(cls) == {"c-ok", "c-old", "c-none"}
    assert cls["c-ok"]["connect"] == {"usable": True, "problems": []} and cls["c-ok"]["access_mode"] == "Dedicated"
    assert not cls["c-old"]["connect"]["usable"]
    assert "Databricks Runtime 16 or later" in cls["c-old"]["connect"]["problems"][0]
    assert any("Dedicated or Standard" in p for p in cls["c-none"]["connect"]["problems"])
    assert not any("isn't installed" in p for c in cls.values() for p in c["connect"]["problems"]), \
        "installing the client is a later step, not something wrong with the cluster"


def test_an_unreadable_runtime_is_a_reason_not_a_crash():
    row = disc.cluster_row({"cluster_id": "x", "spark_version": "custom-image", "data_security_mode": "SINGLE_USER",
                            "autotermination_minutes": 5})
    assert not row["connect"]["usable"] and "isn't a Databricks Runtime version" in row["connect"]["problems"][0]


def test_cluster_pages_are_followed():
    ws = FakeWorkspace(pages=True)
    cls = discover(ws)["parts"]["clusters"]["value"]
    assert [c["id"] for c in cls] == ["c-ok", "c-old", "c-none"]
    assert [q for _, p, q in ws.calls if p == "/api/2.1/clusters/list"] == [{}, {"page_token": "p2"}]


def test_a_part_that_cant_be_read_says_why_and_never_hides_the_rest_or_repeats_a_token():
    result = discover(FakeWorkspace(refuse={"/api/2.1/clusters/list": 403, "/api/2.1/unity-catalog/catalogs": 403}))
    parts = result["parts"]
    assert parts["warehouses"]["ok"] and parts["metastore"]["ok"]
    assert not parts["clusters"]["ok"] and "HTTP 403" in parts["clusters"]["error"]
    assert TOKEN not in json.dumps(result)
    assert result["signed_in"]


def test_a_failed_sign_in_stops_at_the_first_part():
    calls = []

    def fail():
        calls.append(1)
        raise dba.DatabricksAuthError("Sign in with databricks auth login first.")

    ws = FakeWorkspace()
    result = disc.Discovery(dbsql.Client(dba.TokenProvider(HOST, fail, "test"), ws)).run()
    assert not result["signed_in"] and ws.calls == [] and len(calls) == 1
    assert result["parts"]["identity"]["error"] == "Sign in with databricks auth login first."
    assert all(result["parts"][p]["skipped"] for p in disc.PARTS[1:])


def test_the_suggestion_is_running_first_and_only_compute_that_stops_on_its_own():
    result = discover(FakeWorkspace())
    assert result["suggested"] == {"warehouse_id": "wh-run", "cluster_id": "c-ok"}
    assert disc.suggested({}) == {"warehouse_id": "", "cluster_id": ""}


def test_catalogs_can_be_left_out():
    ws = FakeWorkspace()
    result = discover(ws, catalogs=False)
    assert result["parts"]["catalogs"] == {"ok": False, "skipped": True, "error": "Not asked for."}
    assert "/api/2.1/unity-catalog/catalogs" not in [p for _, p, _ in ws.calls]


def test_the_summary_says_nothing_started():
    lines = disc.summary_lines(discover(FakeWorkspace(refuse={"/api/2.1/clusters/list": 403})))
    assert lines[0] == f"Workspace: {HOST}" and lines[-1] == "Nothing was started: discovery only reads."
    assert "Signed in as dev@contoso.com" in lines
    assert any(line.startswith("Clusters: couldn't read: Databricks refused the request (HTTP 403)") for line in lines)
    assert "  Big SQL [wh-stop] STOPPED, Large, no auto-stop" in lines


@pytest.mark.parametrize("argv", [[], ["--profile", "dev", "--host", HOST]])
def test_the_command_needs_exactly_one_workspace(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["databricks-discover", *argv])
    assert stop.value.code == 2 and "--profile" in capsys.readouterr().out


def test_the_command_prints_json_and_exits_1_when_a_part_failed(monkeypatch, capsys):
    ws = FakeWorkspace(refuse={"/api/2.0/sql/warehouses": 403})
    monkeypatch.setattr(dba, "provider_for", lambda conn: tokens())
    real = dbsql.Client
    monkeypatch.setattr(dbsql, "Client", lambda tok: real(tok, ws))
    with pytest.raises(SystemExit) as stop:
        cli.main(["databricks-discover", "--host", HOST, "--json"])
    assert stop.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["signed_in"] and not out["parts"]["warehouses"]["ok"]



def test_a_workspace_without_a_metastore_says_so():
    result = discover(FakeWorkspace(refuse={"/api/2.1/unity-catalog/metastore_summary": 404}))
    assert "no Unity Catalog metastore assigned" in result["parts"]["metastore"]["error"]
    assert "warehouse" not in result["parts"]["metastore"]["error"]
