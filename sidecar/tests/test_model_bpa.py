"""Best practices check of a semantic model, run in Fabric with semantic-link-labs."""

import json

import pytest

from fabric_desktop import model_bpa, model_bpa_service, risk
from fabric_desktop.meta.store import OneLakeStore
from fabric_desktop.service import Service


def test_notebook_pins_semantic_link_labs_and_saves_to_lh_meta():
    nb = model_bpa.bpa_ipynb("ws1", "meta1")
    deps = nb["metadata"]["dependencies"]["lakehouse"]
    assert (deps["default_lakehouse"], deps["default_lakehouse_workspace_id"]) == ("meta1", "ws1")
    params, check = nb["cells"][1:]  # one check cell, so its error is saved: a failed cell cancels the rest
    assert params["metadata"]["tags"] == ["parameters"]
    assert 'lakehouse = ""' in "".join(params["source"])
    code = "".join(check["source"])
    assert f'"-m", "pip", "install", "-q", "semantic-link-labs=={model_bpa.VERSION}"' in code
    assert "run_model_bpa(dataset=dataset, workspace=workspace, extended=True, return_dataframe=True)" in code
    assert 'pathlib.Path("/lakehouse/default") / name' in code and 'name = f"Files/_fabric_desktop/results/{request_id}.json"' in code
    compile(code, "check", "exec")  # the cell is valid Python
    assert model_bpa.notebook_hash(nb) == model_bpa.notebook_hash(model_bpa.bpa_ipynb("ws1", "meta1"))
    params = model_bpa.run_parameters("m1", "ws1", "r1", "meta1")["parameters"]
    assert params["dataset"] == {"value": "m1", "type": "string"}
    assert params["lakehouse"] == {"value": "meta1", "type": "string"}
    assert params["_inlineInstallationEnabled"] == {"value": True, "type": "bool"}  # else a job run skips %pip


def test_findings_read_icons_and_sort_worst_first():
    rows = [{"Category": "Formatting", "Rule Name": "Mark primary keys", "Severity": "ℹ️",
             "Object Type": "Column", "Object Name": "'dim'[sk]", "Description": "d", "URL": ""},
            {"Category": "Performance", "Rule Name": "Model should have a date table", "Severity": "⚠️",
             "Object Type": "Model", "Object Name": "Model", "Description": "d", "URL": "u"},
            {"Category": "Error Prevention", "Rule Name": "Relationship columns same type", "Severity": "❌",
             "Object Type": "Relationship", "Object Name": "r", "Description": "d", "URL": ""},
            {"Category": "Maintenance", "Rule Name": "Objects with no description", "Severity": "Warning",
             "Object Type": "Table", "Object Name": "t", "Description": "d", "URL": ""}]
    out = model_bpa.findings({"request_id": "r1", "finished_at": "2026-10-22T17:00:00+00:00", "version": "0.17.1",
                              "tables": True, "rows": rows})
    assert [f["severity"] for f in out["findings"]] == ["Error", "Warning", "Warning", "Info"]
    assert out["findings"][1]["category"] == "Maintenance"  # then by category
    assert out["counts"] == {"Error": 1, "Warning": 2, "Info": 1} and out["no_tables"] is False
    assert model_bpa.findings({"tables": False, "rows": []})["no_tables"] is True


class FakeFabric:
    def __init__(self):
        self.calls = []
        self.status = "InProgress"

    def list_semantic_models(self, ws):
        return [{"id": "m1", "displayName": "SM_Gold"}]

    def save_notebook(self, ws, name, ipynb):
        self.calls.append(("notebook", ws, name))
        return {"id": "nb1"}

    def run_item_job(self, ws, item, job_type, data=None):
        self.calls.append(("job", ws, item, job_type, data))
        return f"job{len([c for c in self.calls if c[0] == 'job'])}"

    def job_instance(self, ws, item, job):
        return {"status": self.status, "endTimeUtc": "2026-10-22T17:05:00Z" if self.status != "InProgress" else None}


class BpaService(Service):
    fake = None

    @property
    def fabric(self):
        return self.fake

    @property
    def fabric_jobs(self):
        return self.fake

    def _quiet_fabric(self):
        return self.fake


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = BpaService(root=tmp_path / "app")
    s.fake = FakeFabric()
    s.meta_init(str(tmp_path / "proj"), "3")
    store = OneLakeStore("test", "ws-test", {"LH_Meta": "id-meta", "LH_Gold": "id-gold"}, lambda: "tok", tmp_path / "cache")
    monkeypatch.setattr(s, "_store", lambda p, env, create=False: store if env == "test" else type(s)._store(s, p, env, create))
    monkeypatch.setattr(s, "_bpa_risk", lambda t, refresh=False: {"result": risk.PASS, "id": "r", "summary": "", "findings": []})
    files = {}

    class FakeFiles:
        def __init__(self, token):
            pass

        def read_bytes(self, ws, lh, path):
            return files.get((ws, lh, path))

    monkeypatch.setattr(model_bpa_service, "FileStore", FakeFiles)
    s.files = files
    return s


def test_check_publishes_once_runs_in_fabric_and_reads_the_findings_back(svc):
    started = svc.model_bpa_check("test")
    assert started["model"] == "SM_Gold" and started["job_id"] == "job1"
    job = next(c for c in svc.fake.calls if c[0] == "job")
    assert job[1:4] == ("ws-test", "nb1", "RunNotebook")
    assert job[4]["parameters"]["dataset"]["value"] == "m1"
    run = svc.fabric_runs(5, refresh=False)[0]
    assert run["kind"] == "Best practices check" and run["model_id"] == "m1"
    with pytest.raises(RuntimeError, match="already being checked"):
        svc.model_bpa_check("test")
    assert svc.model_bpa_status("test")["running"]["request_id"] == started["request_id"]

    svc.fake.status = "Completed"
    record = {"request_id": started["request_id"], "finished_at": "2026-10-22T17:05:00+00:00", "tables": True,
              "rows": [{"Category": "Performance", "Rule Name": "Model should have a date table", "Severity": "⚠️",
                        "Object Type": "Model", "Object Name": "Model", "Description": "d", "URL": ""}]}
    svc.files[("ws-test", "id-meta", f"Files/_fabric_desktop/results/{started['request_id']}.json")] = json.dumps(record).encode()
    status = svc.model_bpa_status("test")
    assert status["running"] is None and status["failed"] is None
    assert status["last"]["counts"] == {"Error": 0, "Warning": 1, "Info": 0}
    svc.files.clear()  # kept on this PC: the next status doesn't read OneLake again
    assert svc.model_bpa_status("test")["last"]["request_id"] == started["request_id"]

    svc.model_bpa_check("test")
    assert len([c for c in svc.fake.calls if c[0] == "notebook"]) == 1  # the same notebook isn't published again


def test_a_check_that_fails_in_fabric_says_so_and_keeps_the_last_findings(svc):
    svc.fake.status = "Failed"
    svc.model_bpa_check("test")
    status = svc.model_bpa_status("test")
    assert status["failed"]["status"] == "Failed" and status["last"] is None and status["running"] is None


def test_risk_check_gates_a_check(svc, monkeypatch):
    monkeypatch.setattr(svc, "_bpa_risk", lambda t, refresh=False: {"result": risk.BLOCKED, "id": "b", "summary": "No capacity",
                                                                    "findings": []})
    with pytest.raises(PermissionError, match="No capacity"):
        svc.model_bpa_check("test")
    warn = {"result": risk.WARNING, "id": "w1", "summary": "Busy", "workload": "x",
            "findings": [{"title": "Capacity is busy", "outcome": risk.WARNING}]}
    monkeypatch.setattr(svc, "_bpa_risk", lambda t, refresh=False: warn)
    with pytest.raises(ValueError, match="changed since you looked"):
        svc.model_bpa_check("test", risk_id="old")
    assert not [c for c in svc.fake.calls if c[0] == "job"]
    svc.model_bpa_check("test", risk_id="w1")
    ack = svc.risk_acknowledgements()[0]
    assert ack["risk_id"] == "w1" and ack["warnings"] == ["Capacity is busy"] and ack["workload_id"] == "model_bpa:ws-test:m1"


def test_needs_the_model_in_fabric_and_a_onelake_environment(svc, monkeypatch):
    with pytest.raises(ValueError, match="on this PC"):
        svc.model_bpa_status("dev")
    monkeypatch.setattr(svc.fake, "list_semantic_models", lambda ws: [])
    with pytest.raises(LookupError, match="Create the model first"):
        svc.model_bpa_check("test")


def test_risk_check_is_advice_when_it_cant_be_read(svc, monkeypatch):
    monkeypatch.setattr(svc, "_bpa_risk", type(svc)._bpa_risk.__get__(svc))
    monkeypatch.setattr(svc, "_risk_facts", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    got = svc.model_bpa_risk("test")
    assert got["result"] == risk.UNKNOWN and got["can_proceed"] is True and "offline" in got["summary"]


def test_a_second_check_starting_at_the_same_time_is_refused(svc):
    with model_bpa_service._STARTING:
        with pytest.raises(RuntimeError, match="already starting"):
            svc.model_bpa_check("test")
    assert not [c for c in svc.fake.calls if c[0] == "job"]
    svc.model_bpa_check("test")


def _finish(svc, status, body=None):
    started = svc.model_bpa_check("test")
    svc.fake.status = status
    if body is not None:
        svc.files[("ws-test", "id-meta", f"Files/_fabric_desktop/results/{started['request_id']}.json")] = body
    return started


@pytest.mark.parametrize("body", [b'{"rows": [', b'["not", "a", "record"]', b'{"rows": ["not a row"]}'])
def test_a_result_file_that_cant_be_read_says_so_and_status_still_answers(svc, body):
    _finish(svc, "Completed", body)
    status = svc.model_bpa_status("test")
    assert status["last"] is None and status["failed"]["status"] == "Completed"
    assert status["failed"]["message"].startswith("The check finished, but the findings it saved in LH_Meta couldn't be read")


def _run_cell(tmp_path, monkeypatch, pip_code=0, bpa=None):
    """Run the check cell here, with pip, sempy_labs and the lakehouse mount stood in for."""
    import subprocess
    import sys
    import types
    code = "".join(model_bpa.bpa_ipynb("ws1", "meta1")["cells"][2]["source"]).replace("/lakehouse/default", tmp_path.as_posix())  # a Windows path has backslashes
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=pip_code, stdout="pip says hi\n", stderr=""))
    labs = types.ModuleType("sempy_labs")
    labs.run_model_bpa = bpa or (lambda **k: None)
    monkeypatch.setitem(sys.modules, "sempy_labs", labs)
    scope = {"dataset": "m1", "workspace": "ws1", "lakehouse": "meta1", "request_id": "r1"}
    compiled = compile(code, "check", "exec")  # outside the try: a cell that doesn't compile fails the test
    try:
        exec(compiled, scope)
    except Exception as exc:  # the cell fails the run after saving
        raised = exc
    else:
        raised = None
    return raised, json.loads((tmp_path / "Files/_fabric_desktop/results/r1.json").read_text(encoding="utf-8"))


def test_the_check_cell_saves_its_error_before_failing_the_run(tmp_path, monkeypatch):
    def boom(**k):
        raise ValueError("XMLA endpoint is read-only")
    raised, record = _run_cell(tmp_path, monkeypatch, bpa=boom)
    assert isinstance(raised, ValueError)
    err = model_bpa.error(record)
    assert (err["type"], err["message"], err["pip"]) == ("ValueError", "XMLA endpoint is read-only", "pip says hi\n")
    assert "Traceback" in err["traceback"]


def test_the_check_cell_scrubs_secrets_from_the_error_it_saves(tmp_path, monkeypatch):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    def boom(**k):
        raise ValueError(f"Authorization: Bearer abc.def-123 {jwt} https://x.blob/f?sv=1&sig=SECRETSIG https://bob:hunter2@host/p")
    _, record = _run_cell(tmp_path, monkeypatch, bpa=boom)
    saved = json.dumps(record["error"])
    for secret in ("abc.def-123", jwt, "SECRETSIG", "hunter2"):
        assert secret not in saved


def test_the_check_cell_redacts_with_the_apps_own_patterns(tmp_path, monkeypatch):
    # The notebook can't import the app, so it carries a copy of redact.secrets' patterns: what it saves
    # matches what the app's redactor gives, for shapes the old short list didn't know.
    from fabric_desktop import redact
    message = ("push " + "".join(["gh", "p_", "A" * 36]) + " key " + "".join(["AK", "IA", "ABCDEFGHIJKLMNOP"])
               + " Password=" + "".join(["Sup3r", "Secret"]) + "; https://contoso.webhook.office.com/webhookb2/"
               + "".join(["abc", "def123", "/IncomingWebhook/xyz"]))
    def boom(**k):
        raise ValueError(message)
    _, record = _run_cell(tmp_path, monkeypatch, bpa=boom)
    assert record["error"]["message"] == redact.secrets(message)
    assert record["error"]["message"] != message


def test_a_saved_error_is_redacted_and_capped_again_when_read():
    err = model_bpa.error({"error": {"type": "E", "message": "x" * 5000 + " Bearer abc.def-123",
                                     "traceback": "t" * 20000, "pip": "p" * 9000}})
    assert len(err["message"]) <= 2000 and "abc.def-123" not in err["message"]
    assert (len(err["traceback"]), len(err["pip"])) == (8000, 4000)


def test_the_check_cell_saves_a_failed_install_with_pips_output(tmp_path, monkeypatch):
    raised, record = _run_cell(tmp_path, monkeypatch, pip_code=1)
    assert isinstance(raised, RuntimeError)
    assert model_bpa.error(record)["message"].startswith("pip install semantic-link-labs==")


def test_the_check_cell_saves_findings_when_it_works(tmp_path, monkeypatch):
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", lambda name: model_bpa.VERSION)
    raised, record = _run_cell(tmp_path, monkeypatch)
    assert raised is None and model_bpa.error(record) is None
    assert (record["version"], record["tables"], record["rows"]) == (model_bpa.VERSION, False, [])


def test_a_check_that_saved_its_error_shows_it(svc):
    err = {"type": "ValueError", "message": "XMLA endpoint is read-only", "traceback": "Traceback ...", "pip": "ok"}
    _finish(svc, "Failed", json.dumps({"error": err}).encode())
    failed = svc.model_bpa_status("test")["failed"]
    assert failed["message"] == "The check failed in Fabric: ValueError: XMLA endpoint is read-only"
    assert (failed["traceback"], failed["pip"]) == ("Traceback ...", "ok")
    assert svc.model_bpa_status("test")["last"] is None


def test_a_completed_check_that_saved_nothing_says_so(svc):
    _finish(svc, "Completed")
    assert "saved no findings in LH_Meta" in svc.model_bpa_status("test")["failed"]["message"]


def test_findings_are_this_checks_whatever_the_file_says_its_request_is(svc):
    started = _finish(svc, "Completed", json.dumps({"tables": True, "rows": []}).encode())
    status = svc.model_bpa_status("test")
    assert status["failed"] is None and status["last"]["request_id"] == started["request_id"]


def test_a_check_notebook_deleted_in_fabric_is_published_again(svc):
    from fabric_desktop.fabric_api import FabricError
    svc.fake.status = "Completed"
    svc.model_bpa_check("test")
    real = svc.fake.run_item_job

    def run(ws, item, job_type, data=None):
        if item == "nb1":
            raise FabricError("not found", code="ItemNotFound", status=404)
        return real(ws, item, job_type, data)
    svc.fake.run_item_job = run
    svc.fake.save_notebook = lambda ws, name, ipynb: svc.fake.calls.append(("notebook", ws, name)) or {"id": "nb2"}
    started = svc.model_bpa_check("test")
    assert started["job_id"] and [c[0] for c in svc.fake.calls].count("notebook") == 2
    assert svc.fabric_runs(5, refresh=False)[0]["item_id"] == "nb2"


def test_other_fabric_errors_starting_a_check_arent_retried(svc):
    from fabric_desktop.fabric_api import FabricError

    def run(ws, item, job_type, data=None):
        raise FabricError("capacity paused", code="CapacityNotActive", status=403)
    svc.fake.run_item_job = run
    with pytest.raises(FabricError, match="capacity paused"):
        svc.model_bpa_check("test")
    assert [c[0] for c in svc.fake.calls].count("notebook") == 1
