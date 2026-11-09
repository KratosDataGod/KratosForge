"""What external tools' calls return (integrations/services.py): the edges the main tests don't reach.
Status when the sign-in can't say who it is, projects that aren't open or don't exist, an item that
isn't there and a lakehouse's details, a notebook whose dependency view fails (its checks still
stand, and the error is redacted), the pass-through calls, and refusing a half-given lakehouse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import services as sv
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError


def ctx():
    return CallContext(Caller("cl_test", "test"), timeout=5)


def test_status_when_the_sign_in_cant_say_who_it_is(monkeypatch):
    def broken():
        raise RuntimeError("no client ID")

    svc = SimpleNamespace(_auth=object(), auth=SimpleNamespace(signed_in_user=broken, signed_in_tenant=broken))
    out = sv.get_status(svc, ctx())
    assert out["signed_in"] is False and out["user"] is None and out["caller"]["client_id"] == "cl_test"


def test_projects_that_arent_open_or_dont_exist():
    svc = SimpleNamespace(meta_project=lambda: {"path": ""})
    with pytest.raises(IntegrationError, match="No metadata project is open") as err:
        sv.get_project(svc, ctx(), "metadata")
    assert err.value.code == "not_found"
    with pytest.raises(IntegrationError, match="No project 'other'"):
        sv.get_project(svc, ctx(), "other")
    with pytest.raises(IntegrationError, match="No metadata project is open"):
        sv.validate_project(svc, ctx())


def test_an_open_project_says_whether_it_validates():
    svc = SimpleNamespace(meta_project=lambda: {"path": "C:/p", "errors": ["bronze.x: no keys"], "entities": [{}, {}]})
    assert sv.validate_project(svc, ctx()) == {"project": "metadata", "ok": False, "errors": ["bronze.x: no keys"],
                                               "entities": 2}


def test_item_metadata_of_an_item_that_isnt_there_and_of_a_lakehouse():
    items = [{"id": "lh", "displayName": "LH_Gold", "type": "Lakehouse"}]
    fabric = SimpleNamespace(get_lakehouse=lambda ws, lh: {"properties": {"defaultSchema": "dbo",
                                                                          "sqlEndpointProperties": {"id": "sql1"}}})
    svc = SimpleNamespace(list_items=lambda ws: items, fabric=fabric)
    with pytest.raises(IntegrationError, match="No item nope in workspace w"):
        sv.get_artifact_metadata(svc, ctx(), "w", "nope")
    out = sv.get_artifact_metadata(svc, ctx(), "w", "lh")
    assert out["lakehouse"] == {"schemas_enabled": True, "default_schema": "dbo", "sql_endpoint_id": "sql1"}


def test_table_schema_reads_columns_only():
    seen = {}

    def columns(ref, table):
        seen.update(ref=ref, table=table)
        return [{"name": "id", "type": "long"}]

    out = sv.get_table_schema(SimpleNamespace(table_columns=columns), ctx(), "w", "l", "dbo.t")
    assert out["columns"] == [{"name": "id", "type": "long"}]
    assert seen == {"ref": {"workspace_id": "w", "lakehouse_id": "l", "name": "l"}, "table": "dbo.t"}


def test_a_notebook_whose_dependency_view_fails_still_reports_its_checks(monkeypatch):
    from fabric_desktop import proof

    nb = {"cells": [{"type": "code", "source": "x = 1"}], "lakehouse": None}
    monkeypatch.setattr(sv, "compute_check", lambda cells, lh, **kw: {"local": {"ok": True}, "fabric": {"ok": False}})
    monkeypatch.setattr(proof, "check", lambda cells, lh, **kw: {"ok": True, "blockers": [], "warnings": []})

    def broken(cells):
        raise RuntimeError("token=abcdefghijklmnopqrstuvwxyz0123456789 rejected")

    svc = SimpleNamespace(notebooks=SimpleNamespace(load=lambda name: nb), _checks=lambda name: {},
                          notebook_dependencies=broken,
                          notebook_validate_check=lambda *a: {"findings": [], "counts": {}, "verdict": "ok"})
    out = sv.validate_notebook(svc, ctx(), "NB")
    assert out["ok"] is True and out["verdict"] == "ok"
    assert "error" in out["dependencies"] and "abcdefghijklmnopqrstuvwxyz0123456789" not in out["dependencies"]["error"]


def test_pass_through_calls_reach_the_engine_with_their_arguments():
    calls = []

    def rec(name):
        return lambda *a, **kw: calls.append((name, a, kw)) or {"ok": name}

    svc = SimpleNamespace(**{n: rec(n) for n in ("pipeline_inspect", "pipeline_export", "meta_model_ossie_models",
                                                 "pipeline_run_cancel", "prove_cancel", "prove_verify",
                                                 "prove_delete_outputs")})
    c = ctx()
    assert sv.inspect_pipeline(svc, c, "{}") == {"ok": "pipeline_inspect"}
    assert sv.export_pipeline(svc, c, "{}") == {"ok": "pipeline_export"}
    assert sv.list_semantic_models(svc, c) == {"ok": "meta_model_ossie_models"}
    assert sv.cancel_pipeline_run(svc, c, "r1") == {"ok": "pipeline_run_cancel"}
    assert sv.cancel_proof(svc, c, "p1") == {"ok": "prove_cancel"}
    assert sv.verify_proof_table(svc, c, "p1", "dbo.t") == {"ok": "prove_verify"}
    assert sv.delete_proof_outputs(svc, c, "p1") == {"ok": "prove_delete_outputs"}
    assert ("pipeline_export", (), {"text": "{}", "format": None}) in calls  # empty format means "the same one"
    assert ("prove_verify", ("p1", "dbo.t", None, 0), {"save": False}) in calls  # a tool's verify never saves
    assert ("meta_model_ossie_models", ("test",), {}) in calls


def test_side_by_side_runs_come_back_without_cell_outputs():
    record = {"id": "s1", "local": {"cells": {"1": {"status": "ok", "outputs": ["row 1"]}}}, "_cancel": False}
    out = sv.get_side_by_side(SimpleNamespace(side_by_side_status=lambda id: record), ctx(), "s1")
    assert out["local"]["cells"]["1"] == {"status": "ok"} and "_cancel" not in out


def test_python_with_half_a_lakehouse_is_refused():
    with pytest.raises(ValueError, match="Give both workspace_id and lakehouse_id"):
        sv.execute_python(SimpleNamespace(), ctx(), "x = 1", workspace_id="w")
