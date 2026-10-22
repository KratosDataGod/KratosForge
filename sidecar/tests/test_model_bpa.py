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
    params, install, check = nb["cells"][1:]
    assert params["metadata"]["tags"] == ["parameters"]
    assert "".join(install["source"]) == f"%pip install -q semantic-link-labs=={model_bpa.VERSION}\n"
    code = "".join(check["source"])
    assert "run_model_bpa(dataset=dataset, workspace=workspace, extended=True, return_dataframe=True)" in code
    assert "/lakehouse/default/Files/_fabric_desktop/results" in code
    compile(code, "check", "exec")  # the cell is valid Python
    assert model_bpa.notebook_hash(nb) == model_bpa.notebook_hash(model_bpa.bpa_ipynb("ws1", "meta1"))
    assert model_bpa.run_parameters("m1", "ws1", "r1")["parameters"]["dataset"] == {"value": "m1", "type": "string"}


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
