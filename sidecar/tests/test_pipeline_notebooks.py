"""Notebook steps in local pipeline runs (ORCH-004): a Notebook activity runs a notebook saved on
this PC in a fresh kernel, with its parameters, reading pinned tables and writing to the run's own
folder in the lakehouse, which is deleted when the run ends."""

from __future__ import annotations

import json
import shutil
import time

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from fabric_desktop.orchestration.adapters import ActivityFailed, notebook_value
from fabric_desktop.pipeline_notebooks import with_parameters
from fabric_desktop.service import Service

LH = {"name": "LH_Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


class LocalTables:
    def __init__(self, root):
        self.root = root

    def versions(self):
        return {f"{d.parent.name}.{d.name}": DeltaTable(str(d)).version()
                for d in sorted(self.root.glob("*/*")) if (d / "_delta_log").exists()}


def delete_folder(path):
    # OneLake deletes the folder in one call. On Windows a file a stopped kernel just wrote can stay
    # locked for a moment after its process has ended (the virus scanner), so the local stand-in retries.
    for _ in range(50):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.1)
    shutil.rmtree(path)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for name in ("DeltaTable", "FileStore", "publish", "_attached", "write_guard"):
        monkeypatch.setattr(lakehouse, name, getattr(lakehouse, name))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    lh = tmp_path / "lake"
    write_deltalake(str(lh / "Tables" / "dbo" / "orders"),
                    pa.table({"id": [1, 2, 3], "region": ["EMEA", "EMEA", "APAC"], "amount": [10.0, 20.0, 5.0]}))
    s = Service(client_id="x", root=tmp_path / "app")
    signed_in = []
    s._auth = type("Auth", (), {"storage_token": lambda self: signed_in.append(1) or "tok"})()
    s._schema_cache = {"lh1": True}
    monkeypatch.setattr(s, "_proof_location", lambda ref: (str(lh), lambda: {}))
    monkeypatch.setattr(s, "_proof_tables", lambda ref: LocalTables(lh / "Tables"))
    monkeypatch.setattr(s, "_proof_delete_folder", lambda ref, rel: delete_folder(lh / rel))
    s.lh, s.signed_in = lh, signed_in
    return s


def code(source, **meta):
    return {"type": "code", "source": source, "meta": meta}


def act(name, type_, depends=(), **tp):
    a = {"name": name, "type": type_, "typeProperties": tp,
         "dependsOn": [{"activity": d, "dependencyConditions": ["Succeeded"]} for d in depends]}
    return a


def notebook(name, notebook_id="nb-1", depends=(), parameters=None, **extra):
    return act(name, "TridentNotebook", depends, notebookId=notebook_id, workspaceId="ws1",
               parameters=parameters or {}, **extra)


def pipeline(*activities, variables=None, parameters=None):
    return json.dumps({"name": "P", "properties": {"activities": list(activities), "variables": variables or {},
                                                   "parameters": parameters or {}}})


def finish(svc, run_id, limit=120.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        r = svc.pipeline_run_status(run_id)
        if r["state"] not in ("pending", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError("run didn't finish")


def by_name(record):
    return {a["name"]: a for a in record["activities"]}


FILTER = [code("from fabric_desktop import lakehouse\nregion = 'ALL'\nmin_amount = 0.0", tags=["parameters"]),
          code("orders = lakehouse.read_table('dbo.orders')\n"
               "picked = orders.filter((orders['region'] == region) & (orders['amount'] >= min_amount))\n"
               "lakehouse.write_table(picked, 'dbo.picked')"),
          code("notebookutils.notebook.exit(str(picked.height))"),
          code("raise RuntimeError('after exit, nothing runs')")]
COUNT = [code("from fabric_desktop import lakehouse\nn = lakehouse.read_table('dbo.picked').height\n"
              "notebookutils.notebook.exit(f'{n} rows')")]


def test_notebook_steps_run_with_parameters_and_pass_their_exit_values_on(svc):
    svc.notebooks.save("Filter orders", FILTER, LH)
    svc.notebooks.save("Count picked", COUNT, LH)
    before = DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version()
    text = pipeline(
        notebook("Filter", "nb-filter", parameters={"region": {"value": "@pipeline().parameters.region", "type": "string"},
                                                    "min_amount": {"value": 15, "type": "float"}}),
        notebook("Count", "nb-count", depends=["Filter"]),
        act("Keep", "SetVariable", ["Count"], variableName="result",
            value="@concat(activity('Filter').output.result.exitValue, '/', activity('Count').output.result.exitValue)"),
        variables={"result": {"type": "String"}}, parameters={"region": {"type": "string", "defaultValue": "EMEA"}})
    mapping = {"nb-filter": "Filter orders", "Count": "Count picked"}  # by notebookId or by activity name
    assert svc.pipeline_run_check(text=text, notebooks=mapping)["ok"] is True
    started = svc.pipeline_run_start(text=text, notebooks=mapping)
    assert started["ok"] is True, started
    r = finish(svc, started["run_id"])
    acts = by_name(r)
    assert r["state"] == "succeeded", r
    assert r["variables"]["result"] == "1/1 rows"  # EMEA rows of at least 15, read back by the second step
    assert acts["Filter"]["output"]["result"] == {"runStatus": "Succeeded", "exitValue": "1"}
    assert acts["Filter"]["output"]["tables_written"] == ["dbo.picked"]
    assert acts["Filter"]["output"]["cells_run"] == 3  # stopped at exit; the injected cell isn't counted
    assert acts["Count"]["output"]["sandbox"] == f"Files/_fabric_desktop/proofs/pipeline-{r['run_id']}/pc"
    # The lakehouse's own tables are never written, and the run's folder is gone.
    assert not (svc.lh / "Tables" / "dbo" / "picked").exists()
    assert DeltaTable(str(svc.lh / "Tables" / "dbo" / "orders")).version() == before
    assert not (svc.lh / "Files" / "_fabric_desktop" / "proofs" / f"pipeline-{r['run_id']}").exists()
    assert svc.signed_in and any("uses some capacity" in n for n in r["notes"])
    assert not any("no capacity was used" in n for n in r["notes"])


def test_keep_outputs_leaves_the_runs_folder(svc):
    svc.notebooks.save("Filter orders", FILTER, LH)
    text = pipeline(notebook("Filter", parameters={"region": {"value": "APAC", "type": "string"}}))
    r = finish(svc, svc.pipeline_run_start(text=text, notebooks={"Filter": "Filter orders"}, keep_outputs=True)["run_id"])
    assert r["state"] == "succeeded" and by_name(r)["Filter"]["output"]["result"]["exitValue"] == "1"
    folder = svc.lh / "Files" / "_fabric_desktop" / "proofs" / f"pipeline-{r['run_id']}" / "pc" / "tables" / "dbo" / "picked"
    assert DeltaTable(str(folder)).to_pyarrow_table().column("region").to_pylist() == ["APAC"]
    assert any("kept in" in n for n in r["notes"])


def test_notebooks_that_cant_run_here_refuse_the_run_before_anything_starts(svc):
    svc.notebooks.save("Spark", [code("df = spark.read.table('orders')\ndf.show()")], LH)
    svc.notebooks.save("Web", [code("import requests\nrequests.get('https://example.com')")], LH)
    text = pipeline(notebook("A", "a"), notebook("B", "b"), notebook("C", "c"), notebook("D", "d"))
    out = svc.pipeline_run_start(text=text, notebooks={"b": "Spark", "c": "Web", "d": "Not saved"})
    assert out["ok"] is False
    codes = {p["activity"]: p["code"] for p in out["problems"]}
    assert codes == {"A": "notebook_unmapped", "B": "notebook_spark", "C": "notebook_uncontained",
                     "D": "notebook_missing"}
    assert svc.pipeline_runs()["runs"] == []
    # The person can confirm what a clean run can't contain; the API for external tools can't.
    checked = svc.pipeline_run_check(text=pipeline(notebook("C", "c")), notebooks={"c": "Web"}, allow_uncontained=True)
    assert checked["ok"] is True


def test_a_failing_notebook_fails_its_activity_and_the_run(svc):
    svc.notebooks.save("Broken", [code("x = 1"), code("raise ValueError('Bearer abcdefghijklmnopqrstuvwx')")], LH)
    text = pipeline(notebook("Broken", "nb"), act("After", "Wait", ["Broken"], waitTimeInSeconds=0))
    r = finish(svc, svc.pipeline_run_start(text=text, notebooks={"nb": "Broken"})["run_id"])
    acts = by_name(r)
    assert r["state"] == "failed" and acts["Broken"]["state"] == "failed" and acts["After"]["state"] == "skipped"
    assert acts["Broken"]["error"]["errorCode"] == "LocalNotebookFailed"
    assert "failed at cell 2: ValueError" in acts["Broken"]["error"]["message"]
    assert "abcdefghijklmnop" not in json.dumps(r)


def test_cancel_and_policy_timeout_stop_the_notebooks_kernel(svc):
    svc.notebooks.save("Slow", [code("import time\ntime.sleep(60)")], None)
    text = pipeline(notebook("Slow", "nb"))
    rid = svc.pipeline_run_start(text=text, notebooks={"nb": "Slow"})["run_id"]
    time.sleep(1.0)
    assert svc.pipeline_run_cancel(rid)["cancelled"] is True
    t0 = time.monotonic()
    r = finish(svc, rid, 30)
    assert r["state"] == "cancelled" and time.monotonic() - t0 < 20
    timed = pipeline(notebook("Slow", "nb") | {"policy": {"timeout": "0.00:00:02"}})
    r = finish(svc, svc.pipeline_run_start(text=timed, notebooks={"nb": "Slow"})["run_id"], 60)
    assert r["state"] == "failed" and by_name(r)["Slow"]["error"]["errorCode"] == "LocalActivityTimeout"
    # Without a lakehouse, nothing is read from OneLake and nobody is asked to sign in.
    assert not svc.signed_in and any("with no lakehouse" in n for n in r["notes"])


def test_parameters_go_after_the_parameter_cell_or_first():
    cells = [code("a = 1"), code("p = 0", tags=["parameters"]), code("b = p")]
    out = with_parameters(cells, {"p": 5, "s": "x"})
    assert [c["source"] for c in out][1:3] == ["p = 0", "# Parameters from the pipeline\np = 5\ns = 'x'"]
    assert with_parameters([code("b = p")], {"p": 5})[0]["injected"] is True
    assert with_parameters(cells, {}) == cells


@pytest.mark.parametrize("value,typ,expected", [
    ("x", "string", "x"), (3, "string", "3"), ([1], "string", "[1]"), ("true", "bool", True), (False, "bool", False),
    ("7", "int", 7), (7.0, "int", 7), ("2.5", "float", 2.5), (None, None, "None")])
def test_notebook_parameter_types(value, typ, expected):
    assert notebook_value("p", value, typ) == expected


@pytest.mark.parametrize("value,typ", [("yes", "bool"), (7.5, "int"), (True, "int"), ("x", "float"), (1, "date")])
def test_bad_notebook_parameters_fail_the_activity(value, typ):
    with pytest.raises(ActivityFailed) as exc:
        notebook_value("p", value, typ)
    assert exc.value.error["errorCode"] == "LocalExpressionError"


def test_a_parameter_that_isnt_a_python_name_fails_the_activity(svc):
    svc.notebooks.save("Any", [code("x = 1")], None)
    for bad in ("class", "my-param"):
        text = pipeline(notebook("N", "nb", parameters={bad: {"value": "1", "type": "string"}}))
        r = finish(svc, svc.pipeline_run_start(text=text, notebooks={"nb": "Any"})["run_id"])
        assert r["state"] == "failed" and "isn't a Python name" in by_name(r)["N"]["error"]["message"]


def test_without_notebooks_named_notebook_steps_need_a_mock(svc):
    # External AI tools' calls never name notebooks: their runs can't run notebook code the person wasn't shown.
    from fabric_desktop.integrations import services
    from fabric_desktop.integrations.core import CallContext, Caller
    svc.notebooks.save("Filter orders", FILTER, LH)
    text = pipeline(notebook("Filter", "nb-filter"))
    out = services.start_pipeline_run(svc, CallContext(Caller("cl_test", "test"), timeout=5), text)
    assert out["ok"] is False and [p["code"] for p in out["problems"]] == ["notebook_needs_engine"]
    assert "Give a mock for it" in out["problems"][0]["message"] and "--notebook" not in out["problems"][0]["message"]
    assert svc.pipeline_run_check(text=text)["problems"][0]["code"] == "notebook_needs_engine"
    assert svc.pipeline_run_check(text=text, mocks={"Filter": {}})["ok"] is True


def test_cli_runs_notebook_steps(svc, monkeypatch, tmp_path, capsys):
    from fabric_desktop import cli
    monkeypatch.setattr(cli, "Service", lambda: svc)
    svc.notebooks.save("Filter orders", FILTER, LH)
    path = tmp_path / "p.json"
    path.write_text(pipeline(notebook("Filter", "nb-filter", parameters={"region": {"value": "EMEA", "type": "string"}})),
                    encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(path), "--notebook", "nb-filter=Filter orders", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert done.value.code == 0 and record["state"] == "succeeded", record
    assert by_name(record)["Filter"]["output"]["result"]["exitValue"] == "2" and svc.signed_in
    assert not (svc.lh / "Files" / "_fabric_desktop" / "proofs" / f"pipeline-{record['run_id']}").exists()
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(path)])
    assert done.value.code == 2 and "refused: notebook_needs_engine" in capsys.readouterr().out


WRITE_THEN_WAIT = [code("from fabric_desktop import lakehouse\nlakehouse.write_table(lakehouse.read_table('dbo.orders'), "
                        "'dbo.copy')"),
                   code("import time\ntime.sleep(60)")]


def run_folders(svc):
    return sorted(p.name for p in (svc.lh / "Files" / "_fabric_desktop" / "proofs").glob("pipeline-*"))


def test_a_step_stopped_after_writing_leaves_no_folder(svc):
    # Quality review of #319: Cancel and the policy timeout stop the kernel before it can say what it wrote,
    # so the folder is deleted for every lakehouse a step used, whatever was recorded.
    svc.notebooks.save("Slow writer", WRITE_THEN_WAIT, LH)
    rid = svc.pipeline_run_start(text=pipeline(notebook("W", "nb")), notebooks={"nb": "Slow writer"})["run_id"]
    end = time.monotonic() + 60
    while not run_folders(svc) and time.monotonic() < end:
        time.sleep(0.1)
    assert run_folders(svc), "the step never wrote"
    svc.pipeline_run_cancel(rid)
    assert finish(svc, rid)["state"] == "cancelled" and run_folders(svc) == []
    timed = pipeline(notebook("W", "nb") | {"policy": {"timeout": "0.00:00:05"}})
    r = finish(svc, svc.pipeline_run_start(text=timed, notebooks={"nb": "Slow writer"})["run_id"])
    assert by_name(r)["W"]["error"]["errorCode"] == "LocalActivityTimeout" and run_folders(svc) == []


def test_nan_and_infinite_float_parameters_reach_the_notebook(svc):
    svc.notebooks.save("Floats", [code("notebookutils.notebook.exit(f'{a} {b} {c}')")], None)
    text = pipeline(notebook("F", "nb", parameters={"a": {"value": "NaN", "type": "float"},
                                                    "b": {"value": "inf", "type": "float"},
                                                    "c": {"value": "-inf", "type": "float"}}))
    r = finish(svc, svc.pipeline_run_start(text=text, notebooks={"nb": "Floats"})["run_id"])
    assert r["state"] == "succeeded", r
    assert by_name(r)["F"]["output"]["result"]["exitValue"] == "nan inf -inf"


# Quality review of #423: a child pipeline shares the run's notebooks, so its end must not clean up the run.
CHILD = json.dumps({"name": "C", "properties": {"activities": [act("Noop", "SetVariable", variableName="v", value="x")],
                                                "variables": {"v": {"type": "String"}}}})


def test_a_child_pipeline_does_not_clean_up_the_parents_folder_mid_run(svc):
    svc.notebooks.save("Filter orders", FILTER, LH)
    svc.notebooks.save("Count picked", COUNT, LH)
    text = pipeline(
        notebook("Filter", "nb-filter", parameters={"region": {"value": "EMEA", "type": "string"}}),
        act("Child", "ExecutePipeline", ["Filter"], pipeline={"referenceName": "c", "type": "PipelineReference"}),
        notebook("Count", "nb-count", depends=["Child"]))
    started = svc.pipeline_run_start(text=text, notebooks={"nb-filter": "Filter orders", "nb-count": "Count picked"},
                                     children={"c": CHILD})
    assert started["ok"] is True, started
    r = finish(svc, started["run_id"])
    assert r["state"] == "succeeded", (r["state"], by_name(r)["Count"].get("error"))
    assert run_folders(svc) == []


def test_steps_after_a_child_pipeline_still_get_cleaned_up(svc):
    svc.notebooks.save("Filter orders", FILTER, LH)
    text = pipeline(
        act("Child", "ExecutePipeline", pipeline={"referenceName": "c", "type": "PipelineReference"}),
        notebook("Filter", "nb-filter", depends=["Child"], parameters={"region": {"value": "EMEA", "type": "string"}}))
    r = finish(svc, svc.pipeline_run_start(text=text, notebooks={"nb-filter": "Filter orders"}, children={"c": CHILD})["run_id"])
    assert r["state"] == "succeeded", r
    assert run_folders(svc) == [], "the run's folder was left behind"
