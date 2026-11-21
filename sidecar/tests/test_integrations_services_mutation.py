"""integrations/services.py paths that mutation testing (batch 21, docs/quality/mutation-testing.md)
showed no test checked: the paging limits, that each thin adapter hands back what the engine gave and
passes what the caller sent, what notebook runs and proofs are started with, and which fields reach an
outside tool. The engine is a stand-in, so nothing reaches Fabric or starts a process."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from fabric_desktop.integrations import services as S
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def ctx(workspaces=None) -> CallContext:
    c = CallContext(Caller("tool-1", "test"))
    c.workspaces = workspaces
    return c


class Notebooks:
    def __init__(self, books: dict[str, dict[str, Any]]):
        self.books = books

    def list(self):
        return [{"name": n, "modified": b.get("modified", "m")} for n, b in self.books.items()]

    def load(self, name):
        return self.books[name]


class Mgr:
    """The execution manager, standing in: start records the run and what it would run."""

    def __init__(self):
        self.started: list[tuple[Any, Any, float]] = []
        self.found: Any = None

    def start(self, run, fn, c, wait):
        self.started.append((run, fn, wait))
        return {"started": run.kind}

    def get(self, execution_id, client_id):
        assert client_id == "tool-1"
        return self.found

    def list(self, client_id, limit):
        assert (client_id, limit) == ("tool-1", S.ALL)
        return [{"id": "e1", "target": {}}]

    def cancel(self, execution_id, client_id):
        return {"cancelled": execution_id, "by": client_id}

    def run_sql(self, run, query, ref, max_rows):
        return {"query": query, "max_rows": max_rows}


class Svc:
    """Every engine method an adapter calls, each returning what it was called with."""

    def __init__(self, books=None):
        self.notebooks = Notebooks(books or {})
        self._integration_executions = Mgr()
        self.inputs: dict[str, Any] = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            return {"method": name, "args": list(args), "kwargs": kwargs}
        return call

    def _saved_inputs(self, name):
        return self.inputs.get(name)


# ---- paging


def test_a_page_is_at_most_500_and_at_least_1():
    rows = list(range(1200))
    assert len(S.page(rows, 0, 10_000)["items"]) == 500
    assert S.page(rows, 0, 500)["next_offset"] == 500 and S.page(rows, 0, 501)["next_offset"] == 500
    assert S.page(rows, 0, 0)["items"] == [0]


def test_a_page_defaults_to_the_first_100():
    out = S.page(list(range(150)))
    assert out == {"items": list(range(100)), "total": 150, "offset": 0, "next_offset": 100}


def test_listings_ask_the_engine_for_everything_then_page():
    assert S.ALL == 100_000
    svc = Svc()
    seen = {}
    svc.prove_list = lambda name, limit: seen.setdefault("proofs", (name, limit)) and []
    svc.side_by_side_list = lambda name, limit: seen.setdefault("sbs", (name, limit)) and []
    svc.receipts_list = lambda name, limit: seen.setdefault("receipts", (name, limit)) and []
    S.list_proofs(svc, ctx())
    S.list_side_by_side(svc, ctx(), name="nb")
    S.list_receipts(svc, ctx())
    assert seen == {"proofs": (None, 100_000), "sbs": ("nb", 100_000), "receipts": (None, 100_000)}


def test_listing_defaults_page_20_proofs_and_side_by_sides_and_50_receipts_and_100_notebooks():
    svc = Svc({f"n{i:03}": {"cells": []} for i in range(120)})
    svc.prove_list = lambda name, limit: [{"id": i} for i in range(60)]
    svc.side_by_side_list = lambda name, limit: [{"id": i} for i in range(60)]
    svc.receipts_list = lambda name, limit: [{"id": i} for i in range(60)]
    assert len(S.list_proofs(svc, ctx())["items"]) == 20
    assert len(S.list_side_by_side(svc, ctx())["items"]) == 20
    assert len(S.list_receipts(svc, ctx())["items"]) == 50
    for listing in (S.list_proofs, S.list_side_by_side, S.list_receipts):  # from the first one
        assert listing(svc, ctx())["items"][0]["id"] == 0
    assert len(S.list_notebooks(svc, ctx())["items"]) == 100
    svc._integration_executions.found = type("F", (), {"logs": [str(i) for i in range(300)]})()
    assert len(S.get_execution_logs(svc, ctx(), "e1")["items"]) == 200


def test_workspaces_and_items_page_100_by_default():
    svc = Svc()
    svc.list_workspaces = lambda: [{"id": f"w{i}"} for i in range(150)]
    svc.list_items = lambda ws: [{"id": f"i{i}", "type": "Notebook"} for i in range(150)]
    assert len(S.list_workspaces(svc, ctx())["items"]) == 100
    assert len(S.list_artifacts(svc, ctx(), "w")["items"]) == 100


# ---- adapters pass through


@pytest.mark.parametrize("fn, kwargs, method, args, kw", [
    (S.validate_pipeline, {"definition": "d"}, "pipeline_validate", [], {"text": "d"}),
    (S.inspect_pipeline, {"definition": "d"}, "pipeline_inspect", [], {"text": "d"}),
    (S.export_pipeline, {"definition": "d"}, "pipeline_export", [], {"text": "d", "format": None}),
    (S.export_pipeline, {"definition": "d", "format": "yaml"}, "pipeline_export", [], {"text": "d", "format": "yaml"}),
    (S.list_semantic_models, {}, "meta_model_ossie_models", ["test"], {}),
    (S.export_semantic_model_ossie, {"env": "dev", "name": "m"}, "meta_model_ossie_export", ["dev", "m", True], {}),
    (S.preview_ossie_import, {"env": "dev", "text": "t"}, "meta_model_ossie_preview", ["dev", "t", "", "update", []], {}),
    (S.check_run_risk, {"workload_id": "w", "action": "a"}, "risk_check", ["w", "a", None], {}),
    (S.get_placement_advice, {"name": "nb"}, "placement_advice", ["nb"], {}),
    (S.check_pipeline_compatibility, {"definition": "d"}, "pipeline_compatibility", [], {"text": "d"}),
    (S.get_activity_registry, {}, "pipeline_activity_registry", [None], {}),
    (S.get_activity_registry, {"activity_type": "Copy"}, "pipeline_activity_registry", ["Copy"], {}),
    (S.get_pipeline_run, {"id": "r"}, "pipeline_run_status", ["r"], {"values": False}),
    (S.list_pipeline_runs, {}, "pipeline_runs", [20], {}),
    (S.cancel_pipeline_run, {"id": "r"}, "pipeline_run_cancel", ["r"], {}),
    (S.cancel_proof, {"id": "p"}, "prove_cancel", ["p"], {}),
    (S.verify_proof_table, {"id": "p", "table": "t"}, "prove_verify", ["p", "t", None, 0], {"save": False}),
    (S.verify_proof_table, {"id": "p", "table": "t", "rules": {"k": 1}, "offset": 3}, "prove_verify",
     ["p", "t", {"k": 1}, 3], {"save": False}),
    (S.delete_proof_outputs, {"id": "p"}, "prove_delete_outputs", ["p"], {}),
])
def test_an_adapter_returns_what_the_engine_gave(fn, kwargs, method, args, kw):
    assert fn(Svc(), ctx(), **kwargs) == {"method": method, "args": args, "kwargs": kw}


def test_pipeline_runs_pass_their_settings_and_a_zero_timeout_means_none():
    out = S.start_pipeline_run(Svc(), ctx(), "d")
    assert out["kwargs"] == {"text": "d", "parameters": None, "mocks": None, "children": None, "max_parallel": 4,
                             "wait_scale": 1.0, "timeout_seconds": None}
    out = S.start_pipeline_run(Svc(), ctx(), "d", timeout_seconds=5)
    assert out["kwargs"]["timeout_seconds"] == 5
    assert S.check_pipeline_run(Svc(), ctx(), "d")["kwargs"] == {"text": "d", "parameters": None, "mocks": None,
                                                                   "children": None}


def test_a_step_an_outside_tool_can_only_mock_says_so():
    svc = Svc()
    svc.pipeline_run_check = lambda **k: {"problems": [{"code": "notebook_needs_engine", "activity": "Load", "message": "m"},
                                                       {"code": "other", "activity": "X", "message": "kept"}]}
    out = S.check_pipeline_run(svc, ctx(), "d")
    assert out["problems"][0]["message"] == ("Load runs a notebook, which an external tool's run can't do. Give a mock "
                                             "for it, or run the pipeline in KratosForge.")
    assert out["problems"][1]["message"] == "kept"


def test_notebook_dependencies_and_environment_come_from_the_saved_notebook():
    nb = {"cells": [{"source": "x"}], "lakehouse": {"lakehouse_id": "L"}, "compute": "local", "workspace": {"id": "W"}}
    svc = Svc({"nb": nb})
    assert S.get_notebook_dependencies(svc, ctx(), "nb") == {
        "name": "nb", "dependencies": {"method": "notebook_dependencies", "args": [nb["cells"]], "kwargs": {}}}
    assert S.get_notebook_environment(svc, ctx(), "nb") == {"name": "nb", "environment": {
        "method": "notebook_environment", "args": ["nb", nb["cells"], nb["lakehouse"], "local", nb["workspace"]], "kwargs": {}}}
    out = S.check_proof(svc, ctx(), "nb")
    assert out["name"] == "nb" and out["method"] == "prove_check"


def test_a_proof_side_by_side_and_receipt_come_back_without_data():
    svc = Svc()
    svc.prove_status = lambda i: {"id": i, "pc": {"cells": {"1": {"outputs": ["row"], "ok": True}}}, "_cancel": 1}
    svc.side_by_side_status = lambda i: {"id": i, "local": {"cells": {"1": {"outputs": ["row"]}}}}
    svc.receipt_get = lambda i: {"id": i, "receipt": {"id": i, "pc": {"cells": {"1": {"outputs": ["row"]}}}}}
    assert S.get_proof(svc, ctx(), "p") == {"id": "p", "pc": {"cells": {"1": {"ok": True}}}}
    assert S.get_side_by_side(svc, ctx(), "s") == {"id": "s", "local": {"cells": {"1": {}}}}
    got = S.get_receipt(svc, ctx(), "r")
    assert got["id"] == "r" and got["hidden"] == ["values"] and got["receipt"]["pc"]["cells"]["1"] == {}


def test_metadata_and_notebook_fields_reach_a_tool():
    meta = {"path": "/p", "project": {}, "entities": [{"name": "e", "layer": "bronze", "secret": "x"}], "layers": [],
            "errors": [], "environments": {"dev": {"target": "t", "token": "x"}, "prod": None}}
    svc = Svc({"nb": {"cells": [], "modified": "m1"}})
    svc.meta_project = lambda: meta
    projects = S.list_projects(svc, ctx())["projects"]
    assert projects[1]["name"] == "Metadata project"
    meta["project"] = {"name": "Sales"}
    assert S.list_projects(svc, ctx())["projects"][1]["name"] == "Sales"
    meta["project"] = {}
    got = S.get_project(svc, ctx(), "metadata")
    assert got["entities"] == [{"name": "e", "layer": "bronze"}] and got["environments"] == {"dev": {"target": "t"}, "prod": {}}
    assert S.get_project(svc, ctx(), "notebooks") == {"id": "notebooks", "kind": "notebooks",
                                                     "notebooks": [{"name": "nb", "modified": "m1"}]}


def test_a_notebook_cell_counts_its_lines_and_hides_its_source_when_asked():
    svc = Svc({"nb": {"cells": [{"source": "a\nb\nc"}, {"type": "markdown"}]}})
    cells = S.get_notebook(svc, ctx(), "nb", include_source=False)["cells"]
    assert [(c["lines"], "source" in c) for c in cells] == [(3, False), (0, False)]
    assert [c["source"] for c in S.get_notebook(svc, ctx(), "nb")["cells"]] == ["a\nb\nc", ""]


# ---- executions


def test_the_execution_manager_is_made_once_and_kept(tmp_path):
    from types import SimpleNamespace
    svc = SimpleNamespace(root=str(tmp_path / "app"), _integration_executions=None)
    first = S._executions(svc)
    assert first is S._executions(svc) and type(first).__name__ == "ExecutionManager"


def test_a_sql_run_is_titled_by_its_first_line_cut_to_120():
    svc = Svc()
    S.execute_sql(svc, ctx(), "W", "L", "  " + "x" * 200 + "\nmore")
    S.execute_sql(svc, ctx(), "W", "L", "   ")
    (run, _, wait), (blank, _, _) = svc._integration_executions.started
    assert run.title == "x" * 120 and blank.title == "query" and wait == 30 and run.timeout == 120
    assert run.target == {"workspace_id": "W", "lakehouse_id": "L"} and run.client_id == "tool-1"
    assert svc._integration_executions.started[0][1](run)["max_rows"] == 100


def test_a_python_run_takes_a_whole_lakehouse_or_none():
    svc = Svc()
    S.execute_python(svc, ctx(), "a\nb")
    S.execute_python(svc, ctx(), "a", workspace_id="W", lakehouse_id="L", wait_seconds=2)
    (plain, _, w1), (attached, _, w2) = svc._integration_executions.started
    assert plain.title == "2 lines of Python" and plain.target == {} and plain.timeout == 300 and w1 == 30
    assert attached.target == {"workspace_id": "W", "lakehouse_id": "L"} and w2 == 2
    with pytest.raises(ValueError, match="Give both workspace_id and lakehouse_id"):
        S.execute_python(svc, ctx(), "a", workspace_id="W")


def test_execution_status_waits_only_when_asked_and_drops_the_logs():
    svc = Svc()
    done = threading.Event()
    waited = []

    class Found:
        def __init__(self):
            self.done = type("D", (), {"wait": lambda _self, s: waited.append(s)})()

        def view(self):
            return {"id": "e1", "logs": ["x"]}
    svc._integration_executions.found = Found()
    assert S.get_execution_status(svc, ctx(), "e1") == {"id": "e1"} and waited == []
    S.get_execution_status(svc, ctx(), "e1", wait_seconds=5)
    assert len(waited) == 1 and 0 < waited[0] <= 5
    svc._integration_executions.found = {"id": "e2", "logs": ["a", "b"]}
    assert S.get_execution_status(svc, ctx(), "e2") == {"id": "e2"}
    svc._integration_executions.found = {"id": "e2", "logs": ["a", "b"]}
    assert S.get_execution_logs(svc, ctx(), "e2")["items"] == ["a", "b"]
    del done


def test_executions_list_and_cancel_go_to_the_manager():
    svc = Svc()
    assert S.list_executions(svc, ctx())["items"] == [{"id": "e1", "target": {}}]
    assert S.cancel_execution(svc, ctx(), "e9") == {"cancelled": "e9", "by": "tool-1"}


# ---- runs and proofs are bound to what was approved

NB = {"cells": [{"source": "print(1)"}, {"type": "markdown", "source": "# t"}, {"source": "   "}],
      "lakehouse": {"workspace_id": "W", "lakehouse_id": "L"}}


def test_a_notebook_run_takes_its_code_cells_and_its_lakehouses_workspace():
    svc = Svc({"nb": NB, "bare": {"cells": [{"source": "x"}]}})
    bound = S.bind_notebook(svc, {"name": "nb", "workspace_id": "W"})
    assert bound["cells"] == ["print(1)"] and bound["lakehouse_id"] == "L"
    assert S.bind_notebook(svc, {"name": "bare"})["lakehouse_id"] == ""
    with pytest.raises(ValueError, match=r"^nb reads a lakehouse in workspace W; pass that as workspace_id\.$"):
        S.bind_notebook(svc, {"name": "nb", "workspace_id": "Other"})


def test_a_notebook_with_no_code_says_so_and_what_it_skipped():
    step = {"source": "x", "meta": {"fabric_step": True}}
    svc = Svc({"empty": {"cells": [{"type": "markdown", "source": "t"}]}, "step": {"cells": [step]}})
    with pytest.raises(ValueError, match=r"^empty has no code to run\.$"):
        S.bind_notebook(svc, {"name": "empty"})
    with pytest.raises(ValueError, match=r"^step has no code to run\. Cell 1 is marked Run on Fabric"):
        S.bind_notebook(svc, {"name": "step"})


def test_what_a_notebook_run_is_started_with():
    svc = Svc({"nb": NB})
    bound = S.bind_notebook(svc, {"name": "nb", "workspace_id": "W"})
    ok = dict(cells=bound["cells"], cells_sha256=bound["cells_sha256"], lakehouse_id="L")
    assert S.run_notebook(svc, ctx(), "nb", "W", **ok) == {"started": "notebook"}
    run, _, wait = svc._integration_executions.started[0]
    assert run.title == "nb" and run.target == {"notebook": "nb", "workspace_id": "W"} and run.timeout == 900 and wait == 30
    for bad in (dict(ok, cells=["print(2)"]), dict(ok, cells_sha256="0" * 64), dict(ok, lakehouse_id="Other")):
        with pytest.raises(IntegrationError) as caught:
            S.run_notebook(svc, ctx(), "nb", "W", **bad)
        assert caught.value.code == "approval_required" and caught.value.retryable is True
    svc.notebooks.books["nb"] = {**NB, "cells": [{"source": "print(2)"}]}  # changed after the approval
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        S.run_notebook(svc, ctx(), "nb", "W", **ok)


def test_what_a_proof_is_started_with():
    svc = Svc({"nb": NB})
    svc.inputs["nb"] = {"p": 1}
    svc.prove_start = lambda *a: {"id": "proof", "pc": {"cells": {"1": {"outputs": ["row"]}}}}
    bound = S.bind_proof(svc, {"name": "nb"})
    assert bound["inputs"] == {"p": 1} and len(bound["notebook_sha256"]) == 64
    assert S.start_proof(svc, ctx(), "nb", notebook_sha256=bound["notebook_sha256"], inputs={"p": 1}) == {
        "id": "proof", "pc": {"cells": {"1": {}}}}
    for sha, inputs in ((bound["notebook_sha256"], {"p": 2}), ("0" * 64, {"p": 1})):
        with pytest.raises(IntegrationError) as caught:
            S.start_proof(svc, ctx(), "nb", notebook_sha256=sha, inputs=inputs)
        assert caught.value.code == "approval_required" and caught.value.retryable is True
    svc.inputs["nb"] = {"p": 2}  # the bar changed after the approval
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        S.start_proof(svc, ctx(), "nb", notebook_sha256=bound["notebook_sha256"], inputs={"p": 1})


def test_a_notebook_without_saved_values_proves_with_none():
    svc = Svc({"nb": NB})
    assert S.bind_proof(svc, {"name": "nb"})["inputs"] == {}
    assert S._proof_inputs(NB, {}) == S._proof_inputs(dict(NB), {})
    assert S._proof_inputs(NB, {"a": 1, "b": 2}) == S._proof_inputs(NB, {"b": 2, "a": 1})  # key order doesn't matter
    assert S._proof_inputs({"cells": ["é"]}, {}) == \
        __import__("hashlib").sha256('[["é"], null, null, null, {}]'.encode()).hexdigest()


def test_a_receipt_export_hides_what_the_caller_asked_and_what_policy_requires(monkeypatch):
    from fabric_desktop.integrations import policy
    monkeypatch.setattr(policy, "receipt_redactions", lambda: ["values"])
    svc = Svc()
    svc.receipt_export = lambda i, fmt, hidden: {"id": i, "format": fmt, "got": hidden}
    out = S.export_receipt(svc, ctx(), "r", hide=["paths", "values"])
    assert out == {"id": "r", "format": "markdown", "got": ["paths", "values"], "hidden": ["paths", "values"]}
    assert S.export_receipt(svc, ctx(), "r")["hidden"] == ["values"]
