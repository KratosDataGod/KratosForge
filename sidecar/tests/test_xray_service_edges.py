"""Estate X-ray's service (xray_service.py), the paths the main tests don't reach: a scan run from
start to end on its own thread, a scan that fails, fixes whose job is gone, a vacuum that can't size
its files, dataflow checks that can't be read, Power BI's dataflow list, and adopting tables that
can't be read. Fabric and OneLake are fakes; nothing reaches a tenant."""

from __future__ import annotations

import base64
import json
import logging
from types import SimpleNamespace

import pytest
from deltalake import write_deltalake

import fabric_desktop.xray_service as xs
from fabric_desktop.service import Service
from test_xray import FakeFabric, _files, _lake, vacuum_svc  # noqa: F401 - the fixture is used by name
from test_xray_staging import OLD, WS, Fabric, svc  # noqa: F401 - the fixture is used by name

PAT = "dapi" + "0123456789abcdef" * 2


@pytest.fixture
def scan_svc(tmp_path, monkeypatch):
    """A Service whose scan reads the folders of test_xray's estate, with health checks recorded."""
    _lake(tmp_path / "lake")
    s = Service(root=tmp_path / "app")
    tokens, checked = [], []
    monkeypatch.setattr(xs, "FabricClient", lambda token: tokens.append(token()) or FakeFabric())
    monkeypatch.setattr(xs, "FileStore", lambda token: SimpleNamespace(list=_files(tmp_path / "lake")))
    monkeypatch.setattr(xs.maintenance, "health", lambda uri, options: checked.append((uri, options)) or {})
    s.tokens, s.checked = tokens, checked
    return s


def test_a_scan_runs_on_its_thread_saves_its_result_and_says_how_far_it_got(scan_svc):
    scan_svc._xray_state = {"running": True}
    scan_svc._xray_run("fabric-token", "storage-token")
    assert scan_svc._xray_state == {"running": False}
    saved = json.loads(scan_svc._xray_file.read_text(encoding="utf-8"))
    assert saved["tables"] == 5 and saved["shared"] is None  # not connected to a central lakehouse
    assert saved["counts"] == xs.xray.counts(saved["findings"])
    assert scan_svc.tokens == ["fabric-token"]
    uris = sorted(uri for uri, _ in scan_svc.checked)
    assert any(u.endswith("/lh-raw/Tables/dbo/orders") for u in uris)
    assert any(u.endswith("/lh-flat/Tables/events") for u in uris)  # a lakehouse without schemas: no dbo
    assert all(options for _, options in scan_svc.checked)


def test_progress_is_kept_only_while_a_scan_is_known(scan_svc):
    scan_svc._xray_state = None
    scan_svc._xray_progress({"phase": "Lakehouses", "done": 1, "total": 2})
    assert scan_svc._xray_state is None
    scan_svc._xray_state = {"running": True}
    scan_svc._xray_progress({"phase": "Lakehouses", "done": 1, "total": 2})
    assert scan_svc._xray_state["progress"]["done"] == 1


def test_a_scan_stands_without_cost_findings(scan_svc, monkeypatch, caplog):
    def no_logs(days):
        raise OSError("the run logs couldn't be read")
    monkeypatch.setattr(scan_svc, "cost_report", no_logs)
    with caplog.at_level(logging.INFO, logger=xs._log.name):
        scan_svc._xray_run("f", "s")
    assert scan_svc._xray_state == {"running": False}
    assert "No cost findings for X-ray" in caplog.text
    assert json.loads(scan_svc._xray_file.read_text(encoding="utf-8"))["tables"] == 5


def test_a_scan_that_fails_says_why_and_keeps_the_last_result(scan_svc, monkeypatch):
    scan_svc._xray_file.parent.mkdir(parents=True, exist_ok=True)
    scan_svc._xray_file.write_text(json.dumps({"tables": 1, "findings": []}), encoding="utf-8")

    class Down:
        def list_workspaces(self):
            raise ConnectionError("Fabric didn't answer")
    monkeypatch.setattr(xs, "FabricClient", lambda token: Down())
    monkeypatch.setattr(xs.xray.Scanner, "scan", lambda self: Down().list_workspaces())
    scan_svc._xray_run("f", "s")
    assert scan_svc._xray_state == {"running": False, "error": "ConnectionError: Fabric didn't answer"}
    assert json.loads(scan_svc._xray_file.read_text(encoding="utf-8"))["tables"] == 1


def test_a_table_is_read_with_the_sign_in_storage_token(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(storage_token=lambda: "tok")), raising=False)
    uri, options = s._xray_table("ws", "lh", None, "events")
    assert uri.endswith("/lh/Tables/events") and options == xs.storage_options("tok")
    assert s._xray_table("ws", "lh", "sales", "orders")[0].endswith("/lh/Tables/sales/orders")


def test_a_fix_whose_job_left_the_runs_log_shows_the_finding_as_found(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s._xray_mark_submitted("f1", "job-gone", "ws", "item")
    monkeypatch.setattr(s, "fabric_runs", lambda limit, refresh: [{"job_id": "other", "status": "Completed"}], raising=False)
    assert s._xray_fixes("2026-11-01T00:00:00+00:00") == {}


def test_a_vacuum_preview_counts_files_even_when_their_sizes_cant_be_read(vacuum_svc, monkeypatch, caplog):  # noqa: F811
    svc, _uri, _jobs = vacuum_svc

    def offline(*a, **k):
        raise OSError(f"OneLake didn't answer ({PAT})")
    monkeypatch.setattr(xs, "FileStore", lambda _t: SimpleNamespace(list=offline))
    with caplog.at_level(logging.WARNING, logger=xs._log.name):
        out = svc.xray_vacuum_preview("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")
    assert out["files"] == 2 and out["bytes"] is None and out["keep_days"] == 30
    assert "Couldn't size the files" in caplog.text and PAT not in caplog.text


# ---- staging lakehouses: dataflows that can't be read keep it

def test_dataflows_that_cant_be_listed_keep_the_lakehouse(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc

    def refused(self, ws):
        raise PermissionError("403 Forbidden")
    monkeypatch.setattr(Fabric, "list_items", refused)
    p = s.xray_staging_preview(WS, "a")
    assert not p["ok"] and p["dataflows"] == [] and "dataflows couldn't be listed" in p["why"][-1]
    assert deleted == []


def test_a_dataflow_whose_refreshes_cant_be_read_keeps_it(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc
    real = Fabric.list_job_instances

    def jobs(self, ws, item):
        if item == "df1":
            raise ConnectionError("Fabric didn't answer")
        return real(self, ws, item)
    monkeypatch.setattr(Fabric, "list_job_instances", jobs)
    p = s.xray_staging_preview(WS, "a")
    assert not p["ok"] and "The refreshes of Sales load couldn't be read" in p["why"][-1]
    assert p["dataflows"][0]["last_refresh"] is None and deleted == []


def test_a_garbled_write_time_stops_the_check_and_deletes_nothing(svc, monkeypatch):  # noqa: F811
    s, deleted, _ = svc
    real = xs.FileStore("t").list

    def files(ws, lh, directory, recursive=False):
        return [dict(e, modified="not a time") for e in real(ws, lh, directory, recursive)]
    monkeypatch.setattr(xs, "FileStore", lambda _t: SimpleNamespace(list=files))
    with pytest.raises(ValueError, match="isoformat"):
        s.xray_staging_preview(WS, "a")
    with pytest.raises(ValueError):
        s.xray_remove_staging(WS, "a", OLD)
    assert deleted == []


def test_power_bis_dataflow_list_is_read_with_its_own_token(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    asked, got = [], []
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(
        _token=lambda scopes: asked.append(scopes) or "pbi-token")), raising=False)

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"value": [{"objectId": "gen1", "name": "Quarterly"}]}

    def get(url, headers, timeout):
        got.append((url, headers, timeout))
        return Reply()
    monkeypatch.setattr(xs.http_session, "session", lambda: SimpleNamespace(get=get))
    assert s._powerbi_dataflows(WS) == [{"objectId": "gen1", "name": "Quarterly"}]
    assert asked == [xs.POWERBI_DATAFLOW_SCOPES]
    assert got == [(f"https://api.powerbi.com/v1.0/myorg/groups/{WS}/dataflows",
                    {"Authorization": "Bearer pbi-token"}, 30)]


def test_definitions_decode_every_part_and_no_parts_is_unreadable():
    text = base64.b64encode(b'{"stagingLakehouseId": "a"}').decode()
    out = xs._definition_text({"definition": {"parts": [
        {"path": "a.json", "payload": text},
        {"path": "b.json", "payload": "abc", "payloadType": "InlineBase64"},  # bad padding: kept as it is
        {"path": "c.txt", "payload": "plain", "payloadType": "Text"}]}})
    assert out.split("\n") == ['{"stagingLakehouseId": "a"}', "a.json", "abc", "b.json", "plain", "c.txt"]
    with pytest.raises(ValueError, match="no definition parts"):
        xs._definition_text({"definition": {}})


@pytest.mark.parametrize("iso, expected", [(None, None), ("", None), ("yesterday", None),
                                           ("2026-11-01T10:00:00", "2026-11-01T10:00:00+00:00"),
                                           ("2026-11-01T10:00:00Z", "2026-11-01T10:00:00+00:00")])
def test_times_are_utc_or_none(iso, expected):
    when = xs._when(iso)
    assert (when.isoformat() if when else None) == expected


def test_a_column_called_id_is_the_key_whatever_its_case():
    assert xs.guess_keys(["name", "ID", "customer_id"]) == ["ID"]


# ---- adopting tables into the metadata project

@pytest.fixture
def adopt_svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def test_a_source_table_is_read_with_the_projects_source_options(adopt_svc, tmp_path, monkeypatch):
    import polars as pl
    write_deltalake(str(tmp_path / "t"), pl.DataFrame({"id": [1]}).to_arrow())
    made = []

    def ref(ws, lh, schema, table, flat):
        made.append((ws, lh, schema, table, flat))
        return SimpleNamespace(uri=str(tmp_path / "t"))
    monkeypatch.setattr(xs, "TableRef", ref)
    monkeypatch.setattr(adopt_svc, "_source_options", lambda: {}, raising=False)
    uri, table = adopt_svc._source_table("ws", "lh", None, "events")
    assert uri == str(tmp_path / "t") and table.version() == 0
    assert made == [("ws", "lh", "dbo", "events", True)]


def test_adopt_refuses_a_layer_the_project_doesnt_have(adopt_svc):
    with pytest.raises(ValueError, match="platinum is not a layer"):
        adopt_svc.xray_adopt("ws", "lh", "LH", "Sales", [{"table": "orders"}], layer="platinum")


def test_adopt_names_tables_it_cant_read_and_works_without_a_sign_in(adopt_svc, monkeypatch):
    def broken(self):
        raise RuntimeError("not signed in")
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(signed_in_user=lambda: broken(self))),
                        raising=False)

    def unreadable(ws, lh, schema, table):
        raise OSError("No Delta log here")
    monkeypatch.setattr(adopt_svc, "_source_table", unreadable)
    out = adopt_svc.xray_adopt("ws", "lh", "LH", "Sales", [{"schema": "dbo", "table": "dumped_csv"}])
    assert out == {"adopted": [], "problems": ["dumped_csv: OSError: No Delta log here"], "layer": "bronze"}


def _dataflows(monkeypatch, tmp_path, replies):
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(Service, "auth", property(lambda self: SimpleNamespace(_token=lambda scopes: "pbi-token")),
                        raising=False)
    sent = []

    def get(url, headers, timeout):
        sent.append(url)
        status, body = replies.pop(0)
        return SimpleNamespace(status_code=status, headers={"Retry-After": "0"}, json=lambda: body,
                               text="secret body", close=lambda: None)
    monkeypatch.setattr(xs.http_session, "session", lambda: SimpleNamespace(get=get))
    return s, sent


def test_power_bis_dataflow_list_is_read_again_when_throttled(tmp_path, monkeypatch):
    s, sent = _dataflows(monkeypatch, tmp_path, [(429, {}), (503, {}), (200, {"value": [{"objectId": "g1"}]})])
    assert s._powerbi_dataflows(WS) == [{"objectId": "g1"}] and len(sent) == 3


def test_a_refused_dataflow_list_says_the_status_not_the_body(tmp_path, monkeypatch):
    s, sent = _dataflows(monkeypatch, tmp_path, [(403, {"error": "secret body"})])
    with pytest.raises(RuntimeError) as exc:
        s._powerbi_dataflows(WS)
    assert str(exc.value) == "Power BI answered 403" and len(sent) == 1
