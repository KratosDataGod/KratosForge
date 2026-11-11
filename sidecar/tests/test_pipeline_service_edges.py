"""Pipelines on this PC: the edges the main tests don't reach. Inspect and export of a definition
that can't be read, bad children/notebooks/sql_projects arguments, a definition both as text and
path, a run refused once started, the run limit, status of a damaged record, the values-free
status, Get Metadata's file lookups, and listing pipeline folders in repositories."""

from __future__ import annotations

import json
import socket
import time
from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import pipeline_service as ps
from fabric_desktop.orchestration.runner import RunRefused

from test_pipeline_runs import Svc, act, finish, pipeline, saved, setvar, wait


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


SIMPLE = pipeline(setvar("Set", "v", "x"), variables={"v": {"type": "String"}})


def test_text_and_path_together_are_refused(tmp_path):
    out = Svc(tmp_path).pipeline_import(text=SIMPLE, path=str(tmp_path / "p.json"))
    assert out["ok"] is False and out["diagnostics"][0]["code"] == "bad_request"


def test_inspect_and_export_of_a_definition_that_isnt_json_say_why(tmp_path):
    svc = Svc(tmp_path)
    for out in (svc.pipeline_inspect(text="{not json"), svc.pipeline_export(text="{not json")):
        assert out["ok"] is False and out["counts"]["error"] == 1 and out["schema"] is None


def test_export_to_an_unknown_format_is_refused(tmp_path):
    out = Svc(tmp_path).pipeline_export(text=SIMPLE, format="yaml")
    assert out["ok"] is False and out["diagnostics"]


def test_inspect_lists_descriptions_and_switch_case_values(tmp_path):
    switch = act("Route", "Switch", on="@pipeline().parameters.kind",
                 cases=[{"value": "a", "activities": [wait("Wa")]}, {"value": 3, "activities": []}, "odd"],
                 defaultActivities=[])
    switch["description"] = "Routes by kind"
    out = Svc(tmp_path).pipeline_inspect(text=pipeline(switch, parameters={"kind": {"type": "String"}}))
    [row] = out["activities"]
    assert row["description"] == "Routes by kind"
    assert row["case_values"] == ["a", None, None]
    assert "expressions" in row and "type_properties" in row


@pytest.mark.parametrize("kw, match", [({"children": {"child": {"name": "x"}}}, "must be given as text"),
                                       ({"notebooks": ["NB"]}, "notebooks must be"),
                                       ({"sql_projects": ["proj"]}, "sql_projects must be")])
def test_bad_run_arguments_are_refused_before_anything_runs(tmp_path, kw, match):
    svc = Svc(tmp_path)
    check = svc.pipeline_run_check(text=SIMPLE, **kw)
    assert check["ok"] is False and match in check["problems"][0]["message"]
    start = svc.pipeline_run_start(text=SIMPLE, **kw)
    assert start["ok"] is False and match in start["problems"][0]["message"]
    assert svc.pipeline_runs()["runs"] == []


def test_a_run_refused_once_started_still_saves_its_record(tmp_path, monkeypatch):
    svc = Svc(tmp_path)

    def refuse(self):
        self.state = "failed"
        raise RunRefused([{"code": "x", "message": "refused"}])

    monkeypatch.setattr(ps.PipelineRun, "run", refuse)
    started = svc.pipeline_run_start(text=SIMPLE, wait_scale=0)
    assert started["ok"]
    assert saved(tmp_path, started["run_id"])["state"] == "failed"


def test_more_than_the_limit_of_runs_at_once_is_refused_and_finished_ones_are_let_go(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "MAX_RUNS", 1)
    svc = Svc(tmp_path)
    slow = pipeline(wait("W", 30))
    first = svc.pipeline_run_start(text=slow, wait_scale=1)
    busy = svc.pipeline_run_start(text=SIMPLE)
    assert busy["ok"] is False and busy["problems"][0]["code"] == "busy"
    assert svc.pipeline_run_cancel(first["run_id"])["cancelled"] is True
    finish(svc, first["run_id"])
    second = svc.pipeline_run_start(text=SIMPLE, wait_scale=0)
    assert second["ok"]
    finish(svc, second["run_id"])
    _, live = svc._runs()
    for _ in range(200):  # the run's thread lets the older finished run go once it ends
        if first["run_id"] not in live:
            break
        time.sleep(0.02)
    assert first["run_id"] not in live
    assert svc.pipeline_run_status(first["run_id"])["state"] == "cancelled"  # still read from disk


def test_status_of_a_record_that_cant_be_read_is_not_found(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    store, _ = svc._runs()

    def damaged(run_id):
        raise ValueError("not JSON")

    monkeypatch.setattr(store, "get", damaged)
    with pytest.raises(KeyError, match="no pipeline run abc"):
        svc.pipeline_run_status("abc")


def test_status_without_values_drops_outputs_variables_and_error_details(tmp_path):
    svc = Svc(tmp_path)
    failing = pipeline(setvar("Set", "v", "secret"),
                       act("Boom", "Fail", depends=("Set",), message="bad @{variables('v')}", errorCode="E9"),
                       variables={"v": {"type": "String"}}, parameters={"p": {"type": "String", "defaultValue": "x"}})
    started = svc.pipeline_run_start(text=failing, wait_scale=0)
    finish(svc, started["run_id"])
    saved(tmp_path, started["run_id"])
    _, live = svc._runs()
    live.clear()  # read from disk, as after a restart
    full = svc.pipeline_run_status(started["run_id"])
    bare = svc.pipeline_run_status(started["run_id"], values=False)
    assert "variables" in full and "variables" not in bare and "return_value" not in bare
    assert bare["parameters"] == ["p"]
    boom = next(a for a in bare["activities"] if a["name"] == "Boom")
    assert boom["error"] == {"errorCode": "E9"} and "output" not in boom
    assert "secret" not in json.dumps(bare)


def test_without_values_keeps_parameters_that_arent_a_dict():
    out = ps._without_values({"parameters": None, "activities": [{"name": "a", "error": "text"}]})
    assert out["parameters"] is None and out["activities"] == [{"name": "a", "error": "text"}]


# ---- Get Metadata's lookups under Files

class FilesSvc(Svc):
    def __init__(self, root, entries=None, error=None):
        super().__init__(root)
        self.entries, self.error, self.asked = entries or [], error, []

    def _pipeline_list_files(self, ref, rel):
        self.asked.append(rel)
        if self.error:
            raise self.error
        return self.entries


def http_error(status):
    return requests.HTTPError(response=SimpleNamespace(status_code=status))


def test_file_info_of_files_itself_a_found_file_a_missing_one_and_a_missing_folder(tmp_path):
    ref = {"workspace_id": "w", "lakehouse_id": "l"}
    svc = FilesSvc(tmp_path, entries=[{"name": "a.csv", "is_dir": False, "size": 3, "modified": "m"}])
    assert svc._pipeline_file_info(ref, "Files") == {"is_dir": True, "size": 0, "modified": ""}
    assert svc._pipeline_file_info(ref, "Files/in/a.csv")["size"] == 3
    assert svc._pipeline_file_info(ref, "Files/in/b.csv") is None
    assert svc.asked == ["Files/in", "Files/in"]
    assert FilesSvc(tmp_path, error=http_error(404))._pipeline_file_info(ref, "Files/gone/a.csv") is None
    with pytest.raises(requests.HTTPError):
        FilesSvc(tmp_path, error=http_error(403))._pipeline_file_info(ref, "Files/locked/a.csv")
    with pytest.raises(requests.HTTPError):
        FilesSvc(tmp_path, error=requests.HTTPError())._pipeline_file_info(ref, "Files/x/a.csv")


def test_list_files_reads_the_lakehouse_with_the_storage_sign_in(tmp_path, monkeypatch):
    from fabric_desktop import onelake
    seen = {}

    class Store:
        def __init__(self, token):
            seen["token"] = token()

        def list(self, ws, lh, rel):
            seen["where"] = (ws, lh, rel)
            return [{"name": "x"}]

    monkeypatch.setattr(onelake, "FileStore", Store)
    svc = Svc(tmp_path)
    svc.auth = SimpleNamespace(storage_token=lambda: "tok")
    assert svc._pipeline_list_files({"workspace_id": "w", "lakehouse_id": "l"}, "Files/in") == [{"name": "x"}]
    assert seen == {"token": "tok", "where": ("w", "l", "Files/in")}


# ---- pipelines in repositories

def folder_pipeline(path, name):
    path.mkdir(parents=True)
    (path / "pipeline-content.json").write_text(pipeline(wait("W"), name=name), encoding="utf-8")


def test_list_stops_at_the_limit_and_skips_hidden_and_tool_folders(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    folder_pipeline(repo / "a.DataPipeline", "a")
    folder_pipeline(repo / "b.DataPipeline", "b")
    folder_pipeline(repo / "node_modules" / "c.DataPipeline", "c")
    folder_pipeline(repo / ".hidden" / "d.DataPipeline", "d")
    svc = Svc(tmp_path / "app")
    svc.git_repos = lambda: {"repos": [{"exists": True, "path": str(repo), "name": "R"},
                                       {"exists": False, "path": str(tmp_path / "gone")}]}
    out = svc.pipeline_list()
    assert [r["relative"] for r in out["pipelines"]] == ["a.DataPipeline", "b.DataPipeline"]
    assert out["truncated"] is False
    monkeypatch.setattr(ps, "_LIST_LIMIT", 1)
    out = svc.pipeline_list()
    assert out["truncated"] is True and len(out["pipelines"]) == 1


def test_a_folder_that_cant_be_listed_lists_nothing(tmp_path):
    assert list(ps._pipeline_folders(tmp_path / "missing")) == []


def test_compatibility_of_a_definition_that_cant_be_read_names_the_registry(tmp_path):
    out = Svc(tmp_path).pipeline_compatibility(text="{not json")
    assert out["ok"] is False and out["registry_version"]


def test_registry_lists_every_type_or_one_known_or_unknown_type(tmp_path):
    svc = Svc(tmp_path)
    whole = svc.pipeline_activity_registry()
    assert whole["registry_version"]
    one = svc.pipeline_activity_registry("Wait")
    assert one["known"] is True and one["activity"]
    assert svc.pipeline_activity_registry("NoSuchActivity")["known"] is False
