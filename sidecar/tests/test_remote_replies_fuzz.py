"""Replies from remote services (Fabric REST, OneLake DFS and table API, Databricks REST and SQL, Snowflake)
that aren't what the service documents: an outage page, a cut-off or empty body, the wrong JSON types,
nulls, bad base64, a status nobody knows. Each one is read or refused by name (FabricError,
OneLakeBadReply, DatabricksError, OddReply, SyncError, SnowflakeError), never a crash, a hang, or junk
taken as success. Seeded stdlib fuzzing over fake HTTP layers; nothing reaches the network."""

from __future__ import annotations

import base64
import io
import json
import random
from typing import Any

import pytest
import requests

import test_databricks_workspace_sync as tsync
from fabric_desktop import fabric_api, notebook_files
from fabric_desktop.engines import databricks_jobs as dj
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import databricks_workspace_sync as sync
from fabric_desktop.engines import snowflake_remote as sr
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.engines.databricks_discovery import OddReply
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop import onelake
from fabric_desktop.onelake import FileStore, TableCatalog
from test_notebook_runs import _fabric, svc  # noqa: F401 - svc is a fixture
from fabric_desktop.service import Service

API = fabric_api.API
SEEDS = range(150)
ODD: list[Any] = [None, True, 0, -1, 1.5, 10 ** 30, "", "x" * 300, [], [1, "a"], {}, {"a": None}, "null",
                  "Succeeded ", "succeeded", "Undefined", "!!!!", "YWJj=", float("inf")]
BODIES = [b"", b"<html><body>502 Bad Gateway</body></html>", b'{"value": [', b"null", b"[]", b'"x"', b"42",
          b"[" * 50_000, b"\xff\xfe"]


def response(status: int, body: Any, headers: dict[str, str] | None = None, url: str = "") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    r.headers.update(headers or {})
    r.raw, r.url = io.BytesIO(b""), url
    return r


def mutate(rnd: random.Random, value: Any) -> Any:
    """``value`` with some fields swapped for odd values, dropped, or (sometimes) the whole body broken."""
    if isinstance(value, dict):
        for key in list(value):
            roll = rnd.random()
            if roll < 0.15:
                value[key] = rnd.choice(ODD)
            elif roll < 0.25:
                del value[key]
            else:
                value[key] = mutate(rnd, value[key])
    elif isinstance(value, list):
        return rnd.choice(ODD) if rnd.random() < 0.15 else [mutate(rnd, v) for v in value]
    return value


def body(rnd: random.Random, good: dict[str, Any]) -> Any:
    roll = rnd.random()
    if roll < 0.1:
        return rnd.choice(BODIES)
    if roll < 0.15:
        return rnd.choice(ODD)
    return mutate(rnd, json.loads(json.dumps(good)))


class Session:
    def __init__(self, answer):
        self.answer, self.sent = answer, 0

    def request(self, method, url, **kw):
        self.sent += 1
        assert self.sent < 300, "a reply kept the client asking for ever"
        return self.answer(method, url, kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def fabric(answer, poll_timeout: float = 30) -> FabricClient:
    clock = [0.0]
    fabric_api.time.monotonic = lambda: clock[0]  # restored by the autouse fixture

    def sleep(seconds):
        clock[0] += max(seconds, 0)
    return FabricClient(lambda: "tok", session=Session(answer), sleep=sleep, poll_timeout=poll_timeout)


@pytest.fixture(autouse=True)
def _real_clock(monkeypatch):
    monkeypatch.setattr(fabric_api.time, "monotonic", fabric_api.time.monotonic)


# ---------------------------------------------------------------- Fabric REST


@pytest.mark.parametrize("seed", SEEDS)
def test_fabric_list_pages_are_records_or_a_fabric_error(seed):
    rnd = random.Random(f"list-{seed}")
    repeat = rnd.random() < 0.3

    def answer(method, url, kw):
        good = {"value": [{"id": "a", "displayName": "x"}], "continuationToken": "T" if repeat else rnd.choice(["T1", None]),
                "continuationUri": rnd.choice([None, f"{API}/workspaces?x=1", "http://evil.example/"])}
        return response(rnd.choice([200] * 8 + [429, 500]), body(rnd, good), {"Retry-After": rnd.choice(["0", "-1", "abc", "9" * 40])})
    try:
        items = fabric(answer).list_workspaces()
    except FabricError:
        return
    assert isinstance(items, list) and all(isinstance(i, dict) for i in items)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_fabric_operation_never_succeeds_on_a_junk_reply(seed):
    rnd = random.Random(f"lro-{seed}")

    def answer(method, url, kw):
        if method == "POST":
            headers = {"Location": rnd.choice([f"{API}/operations/op1", "", "https://evil.example/op"])} if rnd.random() < 0.8 else {}
            return response(rnd.choice([202, 202, 201]), body(rnd, {"id": "i1"}), headers, url)
        status = rnd.choice(["Succeeded", "Failed", "Running", "Undefined", "Done", "succeeded"])
        return response(200, body(rnd, {"status": status, "error": {"errorCode": "X", "message": "m"}}), {}, url)
    try:
        out = fabric(answer).update_item_definition("ws", "it", {"parts": []})
    except FabricError:
        return
    assert isinstance(out, dict)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_definition_is_whole_base64_or_refused(seed):
    rnd = random.Random(f"def-{seed}")
    good_payload = base64.b64encode(b"hello").decode()

    def answer(method, url, kw):
        if "getDefinition" in url:
            payload = rnd.choice([good_payload, "!!!!", "YWJj=", "YWJ", None, 5, "aGVsbG8\n", "/w=="])
            return response(200, body(rnd, {"definition": {"parts": [{"path": "a.json", "payload": payload}]}}), {}, url)
        return response(200, body(rnd, {"value": [{"id": "v1", "displayName": "lib"}]}), {}, url)
    try:
        out = fabric(answer).read_variable_library("ws", "lib")
    except FabricError:
        return
    if out is not None:
        assert all(text in ("hello", "") for text in out[0].values()), out


@pytest.mark.parametrize("raw", [b"", b"<html>Service Unavailable</html>", b'{"value": [', b"[]", b'{"value": "abc"}',
                                 b'{"value": [1, 2]}', b"[" * 50_000])
def test_a_list_page_that_isnt_fabrics_json_is_refused_by_name(raw):
    c = fabric(lambda m, u, kw: response(200, raw, {}, u))
    with pytest.raises(FabricError) as err:
        c.list_workspaces()
    assert err.value.code == fabric_api.BAD_REPLY and "because" in str(err.value) and "Try again" in str(err.value)


def test_an_operation_started_without_a_location_is_refused_not_a_key_error():
    c = fabric(lambda m, u, kw: response(202, b"", {}, u))
    with pytest.raises(FabricError) as err:
        c.update_item_definition("ws", "it", {"parts": []})
    assert err.value.code == fabric_api.BAD_REPLY


def test_an_operation_whose_status_is_junk_times_out_and_is_never_taken_as_succeeded():
    def answer(method, url, kw):
        if method == "POST":
            return response(202, b"", {"Location": f"{API}/operations/op1"}, url)
        return response(200, {"status": "Succeeded "}, {}, url)
    with pytest.raises(FabricError) as err:
        fabric(answer).update_item_definition("ws", "it", {"parts": []})
    assert err.value.code == "OperationTimedOut"


def test_a_continuation_token_sent_for_ever_stops():
    c = fabric(lambda m, u, kw: response(200, {"value": [{"id": "a"}], "continuationToken": "same"}, {}, u))
    with pytest.raises(FabricError) as err:
        c.list_workspaces()
    assert err.value.code == "PagingStopped"


@pytest.mark.parametrize("payload", [None, 7, "!!!!", "aGVsbG8", "YWJj=", "YW Jj"])
def test_a_part_that_isnt_whole_base64_is_never_read_as_empty(payload):
    got = {"definition": {"parts": [{"path": "settings.json", "payload": payload}]}}
    with pytest.raises(FabricError) as err:
        fabric_api.definition_parts(got)
    assert err.value.code == fabric_api.BAD_DEFINITION and "settings.json" in str(err.value)


def test_a_notebook_definition_with_a_missing_payload_is_refused():
    with pytest.raises(ValueError, match="^Fabric didn't return the notebook's cells"):
        notebook_files.ipynb_of({"definition": {"parts": [{"path": "notebook-content.ipynb"}]}})
    with pytest.raises(ValueError, match="in a form that can't be read"):  # junk inside the base64 isn't decoded short
        notebook_files.ipynb_of({"parts": [{"path": "notebook-content.ipynb", "payload": "e30=!!"}]})
    with pytest.raises(ValueError):
        notebook_files.ipynb_of({"parts": [{"path": "notebook-content.ipynb", "payload": base64.b64encode(b"[1]").decode()}]})


def test_an_error_body_with_odd_details_still_reads_as_a_fabric_error():
    c = fabric(lambda m, u, kw: response(400, {"errorCode": "Bad", "message": "nope", "moreDetails": 5}, {}, u))
    with pytest.raises(FabricError) as err:
        c.get_workspace("ws")
    assert err.value.status == 400 and "nope" in str(err.value)


def test_a_job_status_that_isnt_text_keeps_the_last_known_status(svc, monkeypatch):  # noqa: F811
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00")

    class Fabric:
        def job_instance(self, ws, item, job):
            return {"status": {"odd": True}, "endTimeUtc": None}

    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: Fabric())
    assert svc.notebook_runs("Daily sales", refresh=True)[0]["status"] == "NotStarted"


def test_a_model_definition_with_bad_base64_isnt_read_as_no_tables():
    from fabric_desktop.cloud_service import CloudMixin

    class Fake:
        def get_item_definition(self, ws, item, fmt):
            return {"definition": {"parts": [{"path": "definition/tables/Sales.tmdl", "payload": None}]}}

    svc = CloudMixin()
    svc.fabric = Fake()  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="table names aren't known"):
        svc._kept_table_names(None, "gold", "ws", {"id": "m1", "displayName": "SM_Gold"})


# ---------------------------------------------------------------- OneLake


@pytest.mark.parametrize("seed", SEEDS)
def test_onelake_listings_are_entries_or_a_named_refusal(seed):
    rnd = random.Random(f"onelake-{seed}")
    repeat = rnd.random() < 0.3

    def answer(method, url, kw):
        good = {"paths": [{"name": "lh/Files/a.csv", "contentLength": "12", "isDirectory": "false",
                           "lastModified": "Wed, 21 Oct 2026 07:28:00 GMT"}],
                "schemas": [{"name": "dbo"}], "tables": [{"name": "t"}]}
        token = {"x-ms-continuation": "T" if repeat else rnd.choice(["", "T2"])}
        return response(200, body(rnd, good), token if rnd.random() < 0.6 else {}, url)
    files = FileStore(lambda: "t", session=Session(answer), sleep=lambda s: None)
    catalog = TableCatalog(lambda: "t", session=Session(answer), sleep=lambda s: None)
    for read in (lambda: files.list("w", "lh"), lambda: catalog.list_schemas("w", "lh"),
                 lambda: catalog.list_tables("w", "lh", "dbo")):
        try:
            read()
        except requests.RequestException:
            pass
        except RuntimeError as exc:
            assert "stopped" in str(exc)


@pytest.mark.parametrize("entry", [{"contentLength": "1"}, {"name": "noslash"}, {"name": "lh/a", "contentLength": "abc"},
                                   {"name": "lh/a", "contentLength": "9" * 5000}, {"name": 5}])
def test_a_odd_onelake_path_entry_is_refused_not_a_crash(entry):
    files = FileStore(lambda: "t", session=Session(lambda m, u, kw: response(200, {"paths": [entry]}, {}, u)),
                      sleep=lambda s: None)
    with pytest.raises(onelake.OneLakeBadReply, match="because"):
        files.list("w", "lh")


# ---------------------------------------------------------------- Databricks


GOOD_STATEMENT = {"statement_id": "01ef-abc", "status": {"state": "SUCCEEDED", "error": {"message": "boom"}},
                  "manifest": {"schema": {"columns": [{"name": "a", "type_name": "INT"},
                                                      {"name": "b", "type_name": "DECIMAL", "type_precision": 10,
                                                       "type_scale": 2}]},
                               "total_row_count": 2, "truncated": False},
                  "result": {"data_array": [["1", "2.5"], ["3", None]], "row_count": 2, "row_offset": 0}}


class Warehouse:
    host = "https://adb.example"

    def __init__(self, rnd):
        self.rnd, self.stopped = rnd, (lambda: False)

    def _reply(self):
        x = mutate(self.rnd, json.loads(json.dumps(GOOD_STATEMENT)))
        return x if isinstance(x, dict) else {}

    def warehouse(self, warehouse_id):
        return {"name": "wh", "state": "RUNNING"}

    def submit(self, body):
        x = self._reply()
        if self.rnd.random() < 0.4:
            x["status"] = {"state": self.rnd.choice(["PENDING", "RUNNING"])}
        return x

    def status(self, statement_id):
        return self._reply()

    def chunk(self, statement_id, index):
        x = self._reply().get("result")
        return x if isinstance(x, dict) else {}

    def cancel(self, statement_id):
        pass


@pytest.mark.parametrize("seed", SEEDS)
def test_a_databricks_statement_reply_is_a_result_or_a_plain_error(seed):
    rnd = random.Random(f"dbsql-{seed}")
    clock = [0.0]
    session = dbsql.DatabricksSqlSession(SessionConfig("nb"), Warehouse(rnd), "wh1",
                                         sleep=lambda s: clock.__setitem__(0, clock[0] + 30), clock=lambda: clock[0])
    result = session.execute(CellRequest("select 1", "sql", result_rows=100 if rnd.random() < 0.4 else 0))
    assert result.status in ("ok", "error")


@pytest.mark.parametrize("change", [
    lambda r: r.__setitem__("manifest", [1]),
    lambda r: r["manifest"]["schema"].__setitem__("columns", "ab"),
    lambda r: r["manifest"].__setitem__("total_row_count", "PENDING"),
    lambda r: r["result"].__setitem__("data_array", {"a": 1}),
    lambda r: r["result"]["data_array"].__setitem__(0, [1, 2]),
])
def test_an_odd_databricks_result_is_a_plain_error_cell(change):
    class One(Warehouse):
        def submit(self, body):
            reply = json.loads(json.dumps(GOOD_STATEMENT))
            change(reply)
            return reply
    session = dbsql.DatabricksSqlSession(SessionConfig("nb"), One(random.Random(0)), "wh1", sleep=lambda s: None)
    for rows in (0, 100):
        result = session.execute(CellRequest("select 1", "sql", result_rows=rows))
        assert result.status == "error" and result.outputs[0]["ename"] == "DatabricksError"


def test_a_failed_statement_whose_error_is_text_says_it_failed():
    class Failed(Warehouse):
        def submit(self, body):
            return {"statement_id": "01ef-abc", "status": {"state": "FAILED", "error": "boom"}}
    session = dbsql.DatabricksSqlSession(SessionConfig("nb"), Failed(random.Random(0)), "wh1", sleep=lambda s: None)
    result = session.execute(CellRequest("select 1", "sql"))
    assert result.status == "error" and "failed" in result.outputs[0]["evalue"]


@pytest.mark.parametrize("seed", SEEDS)
def test_a_databricks_run_reply_is_a_row_or_odd_reply(seed):
    rnd = random.Random(f"run-{seed}")
    run = {"run_id": 5, "job_id": 1, "state": {"result_state": "FAILED"}, "trigger": "PERIODIC",
           "tasks": [{"task_key": "t", "state": {"result_state": "FAILED", "state_message": "boom"},
                      "notebook_task": {"notebook_path": "/a"}, "sql_task": {"warehouse_id": "w"}}],
           "start_time": 1, "end_time": 5, "job_parameters": [{"name": "a", "value": "b"}]}

    class Client:
        host = "https://adb.example"

        def get(self, path, params=None):
            return mutate(rnd, json.loads(json.dumps(run)))
    try:
        dj.Runs(Client()).run("5")
    except (OddReply, dbsql.DatabricksError):
        pass


@pytest.mark.parametrize("content", [None, 5, "!!!!", "aGVsbG8"])
def test_a_pull_whose_export_has_no_whole_content_leaves_the_file_alone(tsync_folder, content):
    ws = tsync.FakeWorkspace(exists=False)
    p = tsync.pair(tsync_folder)
    p["files"] = sync.push(tsync.client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"]["modified_at"] = 9999
    real = ws.request

    def request(method, url, **kw):
        if url.endswith("/api/2.0/workspace/export"):
            return tsync.Resp(body={} if content is None else {"content": content})
        return real(method, url, **kw)
    ws.request = request
    before = (tsync_folder / "load_orders.py").read_bytes()
    out = sync.pull(tsync.client(ws), p, ["load_orders.py"])
    assert out["done"] == [] and "because Databricks sent" in out["skipped"][0]["why"]
    assert (tsync_folder / "load_orders.py").read_bytes() == before


@pytest.mark.parametrize("when", ["abc", 1.5, [1], float("inf"), True])
def test_a_workspace_listing_with_an_odd_change_time_is_a_sync_error(tsync_folder, when):
    ws = tsync.FakeWorkspace(objects={"n": {"type": "FILE", "modified_at": when}})
    with pytest.raises(sync.SyncError, match="because"):
        sync.compare(tsync.client(ws), tsync.pair(tsync_folder))


@pytest.fixture
def tsync_folder(tmp_path):
    root = tmp_path / "nb"
    root.mkdir()
    (root / "load_orders.py").write_text("# Databricks notebook source\nprint(1)\n")
    return root


# ---------------------------------------------------------------- Snowflake


@pytest.mark.parametrize("seed", SEEDS)
def test_snowflake_rows_are_rows_or_a_snowflake_error(seed):
    rnd = random.Random(f"sf-{seed}")
    answer = mutate(rnd, {"resultSetMetaData": {"rowType": [{"name": "name"}, {"name": "state"}]},
                          "data": [["DB", "started"]]})
    try:
        out = sr.rows(answer if isinstance(answer, dict) else {})
    except sr.SnowflakeError:
        return
    assert isinstance(out, list)


# ---------------------------------------------------------------- Fabric Git status


@pytest.mark.parametrize("field", ["workspaceChange", "remoteChange", "conflictType"])
@pytest.mark.parametrize("odd", [["Modified"], {"a": 1}, 5])
def test_a_git_status_change_that_isnt_a_word_reads_as_unknown(field, odd):
    from fabric_desktop import fabric_git
    change = {"itemMetadata": {"itemIdentifier": {"logicalId": "11111111-2222-3333-4444-555555555555"},
                               "itemType": "Notebook", "displayName": "nb"},
              "workspaceChange": "Modified", "remoteChange": None, "conflictType": "None"} | {field: odd}
    (row,) = fabric_git.status({"changes": [change]})["items"]
    assert row[{"workspaceChange": "workspace_change", "remoteChange": "remote_change",
                "conflictType": "conflict"}[field]] == ""
