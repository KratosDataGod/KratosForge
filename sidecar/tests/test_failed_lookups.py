"""A failed read never turns into a guess that a destructive Fabric action then acts on (QA-003, QA-004, QA-007, QA-010)."""

import datetime as dt
import json

import polars as pl
import pytest

from fabric_desktop import cloud_service, lakehouse
from fabric_desktop.service import Service
from fabric_desktop.xray_service import _finding_id
from test_cloud import FolderOneLake, svc  # noqa: F401 - the fixture
from test_fabric_api import Resp, client
from test_xray import vacuum_svc  # noqa: F401 - the fixture


# ---- VACUUM keeps the history it promises, or doesn't start (QA-003)

def test_vacuum_is_refused_when_the_metadata_project_cant_be_read(vacuum_svc, monkeypatch):
    svc, uri, jobs = vacuum_svc
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(ValueError("layers.toml: bad TOML")))
    with pytest.raises(RuntimeError, match="Couldn't read the metadata project.*bad TOML"):
        svc.xray_vacuum("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev", finding_id="vacuum|x")
    with pytest.raises(RuntimeError, match="Couldn't read the metadata project"):
        svc.xray_optimize("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    assert jobs == [] and "xray_submitted" not in svc._settings()


def test_vacuum_is_refused_when_the_tables_retention_cant_be_read(vacuum_svc, monkeypatch, tmp_path):
    svc, uri, jobs = vacuum_svc
    monkeypatch.setattr(Service, "_xray_table", lambda self, ws, lh, schema, table: (str(tmp_path / "gone"), None))
    with pytest.raises(RuntimeError, match="Couldn't read the table's retention setting"):
        svc.xray_vacuum("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev")
    with pytest.raises(RuntimeError, match="retention setting"):
        svc.xray_vacuum_preview("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver")
    assert jobs == []


def test_no_metadata_project_still_keeps_fabrics_default(tmp_path):
    s = Service(root=tmp_path / "app")
    assert s._xray_keep_days("ws", "LH", None) == (7, "Fabric's default")


# ---- the Direct Lake model never overwrites by mistake (QA-004)

def _onelake(svc, tmp_path, monkeypatch):
    project = svc._project()
    store = FolderOneLake(tmp_path / "onelake", "test", [l.lakehouse for l in project.layers] + ["LH_Meta"])
    for e in project.entities.values():
        lh = project.layer(e.layer).lakehouse
        store.write(lh, e.schema, e.table, svc._store(project, "dev").read(lh, e.schema, e.table))
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: store if env == "test" else type(svc)._store(svc, p, env, create))
    return store


def test_model_plan_fails_when_the_models_cant_be_listed(svc, monkeypatch):
    def broken(ws):
        raise ConnectionError("throttled")
    monkeypatch.setattr(svc.fake, "list_semantic_models", broken)
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["test"] = dict(envs.get("test") or {}) | {"target": "onelake", "workspace_id": "ws-test", "workspace_name": "Sales - Test"}
    svc._write_environments(project, envs)
    with pytest.raises(RuntimeError, match="Couldn't list the semantic models in Sales - Test.*SM_Gold exists isn't known"):
        svc.meta_model_plan("test")


def test_model_isnt_written_when_a_table_cant_be_read(svc, tmp_path, monkeypatch):
    _onelake(svc, tmp_path, monkeypatch)
    real = cloud_service.table_schema

    def flaky(store, lh, schema, table):
        if table == "fact_sales":
            raise OSError("403 Forbidden")
        return real(store, lh, schema, table)
    monkeypatch.setattr(cloud_service, "table_schema", flaky)
    with pytest.raises(RuntimeError, match=r"Couldn't read gold.fact_sales \(OSError: 403 Forbidden\), so the model wasn't written"):
        svc.meta_build_model("test", replace=True)
    assert not [c for c in svc.fake.calls if c[0] == "model"]


def test_a_table_not_written_yet_is_left_out_and_said_so(svc, tmp_path, monkeypatch):
    store = _onelake(svc, tmp_path, monkeypatch)
    import shutil
    shutil.rmtree(store.uri("LH_Gold", "dbo", "fact_sales"))
    built = svc.meta_build_model("test")
    assert built["skipped"] == ["gold.fact_sales"] and built["tables"] == ["dim_customer"]


def test_saving_over_an_existing_model_needs_replace():
    c, s = client([Resp(200, {"value": [{"id": "m1", "displayName": "SM_Gold"}]})])
    with pytest.raises(FileExistsError) as err:
        c.save_semantic_model("ws1", "SM_Gold", {"definition.pbism": "{}"}, replace=False)
    assert err.value.args[1] == "m1" and len(s.calls) == 1  # looked up, nothing written


def test_saving_a_model_stops_when_the_list_fails():
    c, s = client([Resp(503, {"error": {"code": "ServiceUnavailable", "message": "busy"}})])
    with pytest.raises(Exception):
        c.save_semantic_model("ws1", "SM_Gold", {"definition.pbism": "{}"})
    assert {m for m, *_ in s.calls} == {"GET"}  # no create over a model that may exist (the list may be retried)


# ---- a lakehouse whose layout can't be read isn't written (QA-007)

LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "Sales"}


def test_table_write_is_refused_when_the_lakehouse_layout_cant_be_read(tmp_path, monkeypatch):
    class Offline:
        def get_lakehouse(self, ws, lh):
            raise ConnectionError("offline")
    monkeypatch.setattr(Service, "fabric", property(lambda self: Offline()))
    published = []
    monkeypatch.setattr(lakehouse, "publish", lambda *a, **k: published.append(a))
    s = Service(root=tmp_path)
    with pytest.raises(RuntimeError, match="whether this lakehouse uses schemas.*offline"):
        s._schemas_enabled("ws1", "lh1")
    s._attach(LH)
    try:
        with pytest.raises(RuntimeError, match="Nothing was read or written"):
            lakehouse.write_table(pl.DataFrame({"a": [1]}), "sales")
        assert published == []
        s._schema_cache["lh1"] = False  # an answer from earlier in the session still stands
        assert s._schemas_enabled("ws1", "lh1") is False
        assert lakehouse.table_ref("sales").flat
    finally:
        lakehouse.attach(None)


# ---- X-ray: submitted is not fixed (QA-010)

FINDING = {"kind": "vacuum", "severity": "medium", "workspace_id": "ws-dev", "lakehouse_id": "lh-silver",
           "lakehouse_name": "LH_Silver", "workspace_name": "Dev", "schema": "dbo", "table": "orders",
           "title": "dbo.orders has 40 versions", "detail": "", "action": "vacuum"}


def _scan(svc, when):
    svc._xray_file.parent.mkdir(parents=True, exist_ok=True)
    svc._xray_file.write_text(json.dumps({"scanned": when.isoformat(timespec="seconds"), "findings": [FINDING]}))


def _job(svc, monkeypatch, status, ended=None, message=""):
    class Jobs:
        def job_instance(self, ws, item, job):
            reason = {"message": message} if message else None
            return {"status": status, "endTimeUtc": ended, "failureReason": reason}
    monkeypatch.setattr(svc, "_quiet_fabric", lambda: Jobs())


def _shown(svc):
    return svc.xray()["result"]["findings"][0]


def test_a_failed_fix_leaves_the_finding_visible(vacuum_svc, monkeypatch):
    svc, _uri, _jobs = vacuum_svc
    _scan(svc, dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1))
    svc.xray_vacuum("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev", finding_id=_finding_id(FINDING))
    _job(svc, monkeypatch, "InProgress")
    f = _shown(svc)
    assert f["fixed"] is False and f["fix"]["state"] == "submitted" and f["fix"]["job_id"] == "job-1"
    _job(svc, monkeypatch, "Failed", dt.datetime.now(dt.timezone.utc).isoformat(), "Capacity is paused")
    f = _shown(svc)
    assert f["fixed"] is False and f["fix"] == {"state": "failed", "job_id": "job-1", "status": "Failed",
                                                "message": "Capacity is paused"}


def test_a_fix_counts_only_once_the_job_completes_and_a_later_scan_can_show_it_again(vacuum_svc, monkeypatch):
    svc, _uri, _jobs = vacuum_svc
    now = dt.datetime.now(dt.timezone.utc)
    _scan(svc, now - dt.timedelta(hours=1))
    svc.xray_vacuum("ws-dev", "lh-silver", "orders", "dbo", "LH_Silver", "Dev", finding_id=_finding_id(FINDING))
    _job(svc, monkeypatch, "Completed", now.isoformat().replace("+00:00", "Z"))
    assert _shown(svc)["fixed"] is True
    _scan(svc, now + dt.timedelta(minutes=5))  # a scan after the fix still finds the problem
    f = _shown(svc)
    assert f["fixed"] is False and f["fix"] is None
