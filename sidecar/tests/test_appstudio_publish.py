"""AS-13 publish: check (reads only) -> the person confirms that check, typing the workspace's name -> one Fabric
write, recorded in the project's history. Fabric is a fake here; a live run needs the owner's typed approval."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import audit
from fabric_desktop.appstudio import discovery, generate, project
from fabric_desktop.appstudio import publish as publishing
from fabric_desktop.appstudio_service import AppStudioMixin

from test_appstudio_generate import BIM, ITEM, PLAN, WS

TARGET_WS = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REPORT = "12345678-1234-1234-1234-123456789abc"


class _Fabric:
    def __init__(self):
        self.items: list[dict] = []
        self.writes: list[tuple] = []
        self.fail: Exception | None = None
        self.leave_behind = False
        self.no_id = False

    def get_workspace(self, ws):
        return {"id": ws, "displayName": "Sales dev"}

    def list_items(self, ws):
        return list(self.items)

    def find_item(self, ws, item_type, name):
        return next((i for i in self.items if i["type"] == item_type and i["displayName"] == name), None)

    def create_item(self, ws, item_type, name, definition=None, description=""):
        self.writes.append(("create", ws, item_type, name, definition))
        if self.fail:
            if self.leave_behind:
                self.items.append({"id": REPORT, "type": item_type, "displayName": name})
            raise self.fail
        made = {"id": REPORT, "type": item_type, "displayName": name}
        self.items.append(made)
        return {} if self.no_id else made

    def update_item_definition(self, ws, item_id, definition):
        self.writes.append(("update", ws, item_id, definition))
        if self.fail:
            raise self.fail
        return {}


class _Svc(AppStudioMixin):
    def __init__(self, root):
        self.root = root
        self.fabric = _Fabric()

    def _model_bim(self, ws, model):
        return BIM


@pytest.fixture
def svc(tmp_path):
    s = _Svc(tmp_path / "data")
    meta = discovery.from_bim(BIM)
    checked = generate.check_plan(PLAN, meta, "powerbi-report")
    spec = generate.to_spec(checked["plan"], meta, "powerbi-report",
                            {"kind": "semantic-model", "name": "Sales model", "workspace_id": WS, "item_id": ITEM})
    s.project = project.create(s._appstudio_root(), "Sales overview", spec).name
    return s


def publish(svc, check, **kw):
    args = {"fingerprint": check["fingerprint"], "confirm_name": "Sales dev", "workspace_id": check["workspace_id"]}
    return svc.appstudio_publish(svc.project, **{**args, **kw})


def test_check_reads_only_and_says_what_would_happen(svc):
    got = svc.appstudio_publish_check(svc.project)
    assert got["ready"] and not got["problems"]
    assert got["workspace_id"] == WS, "the model's own workspace by default"
    assert got["confirm_text"] == "Sales dev" and got["name"] == "Sales overview" and got["conflict"] is None
    assert "definition.pbir" in got["parts"] and got["fingerprint"]
    assert svc.fabric.writes == []
    assert "appstudio_publish_check" in audit.READ_ONLY and "appstudio_publish" not in audit.READ_ONLY


def test_publish_needs_the_typed_workspace_name_then_writes_once(svc):
    check = svc.appstudio_publish_check(svc.project)
    assert publish(svc, check, confirm_name="sales dev")["needs_confirm"]
    assert svc.fabric.writes == []
    got = publish(svc, check)
    assert got["done"] and got["report_id"] == REPORT and not got["replaced"]
    assert got["url"] == f"https://app.powerbi.com/groups/{WS}/reports/{REPORT}"
    [(kind, ws, item_type, name, definition)] = svc.fabric.writes
    assert (kind, ws, item_type, name) == ("create", WS, "Report", "Sales overview")
    parts = {p["path"]: base64.b64decode(p["payload"]).decode() for p in definition["parts"]}
    assert json.loads(parts["definition.pbir"])["datasetReference"]["byConnection"]["connectionString"] == f"semanticmodelid={ITEM}"
    entries = project.publish_history(svc._appstudio_project(svc.project))
    assert [e["state"] for e in entries] == ["started", "done"] and entries[-1]["report_id"] == REPORT
    shown = svc.appstudio_history(svc.project)
    assert shown[0]["kind"] == "publish" and shown[0]["state"] == "done"
    assert "payload" not in json.dumps(entries), "the definition isn't kept in history"


def test_a_check_that_changed_is_asked_again(svc):
    check = svc.appstudio_publish_check(svc.project)
    spec = svc.appstudio_open(svc.project)["spec"]
    svc.appstudio_change(svc.project, [{"op": "set", "id": spec["pages"][0]["components"][0]["id"], "field": "title", "value": "Total"}])
    assert publish(svc, check)["stale"]
    other = svc.appstudio_publish_check(svc.project, workspace_id=TARGET_WS)
    assert other["fingerprint"] != check["fingerprint"]
    assert publish(svc, other, workspace_id=WS)["stale"], "a check for one workspace can't publish to another"
    assert svc.fabric.writes == []


def test_a_report_already_there_needs_replace_and_says_whose_it_is(svc):
    svc.fabric.items.append({"id": REPORT, "type": "Report", "displayName": "Sales overview"})
    check = svc.appstudio_publish_check(svc.project)
    assert check["conflict"] == {"id": REPORT, "ours": False}
    assert publish(svc, check)["needs_replace"]
    assert svc.fabric.writes == []
    got = publish(svc, check, replace=True)
    assert got["done"] and got["replaced"] and svc.fabric.writes[0][:3] == ("update", WS, REPORT)
    again = svc.appstudio_publish_check(svc.project)
    assert again["conflict"] == {"id": REPORT, "ours": True}, "now this project published it"


def test_a_failure_midway_is_recorded_and_the_report_it_left_is_found(svc):
    svc.fabric.fail, svc.fabric.leave_behind = RuntimeError("operation failed: Bearer abc.def.ghi"), True
    check = svc.appstudio_publish_check(svc.project)
    with pytest.raises(RuntimeError, match="is in the workspace now") as err:
        publish(svc, check)
    assert "abc.def.ghi" not in str(err.value)
    entries = project.publish_history(svc._appstudio_project(svc.project))
    assert [e["state"] for e in entries] == ["started", "failed"] and entries[-1]["report_id"] == REPORT
    svc.fabric.fail = None
    retry = svc.appstudio_publish_check(svc.project)
    assert retry["conflict"] == {"id": REPORT, "ours": True}
    assert publish(svc, retry, replace=True)["done"]


def test_an_unfinished_publish_is_reported(svc):
    folder = svc._appstudio_project(svc.project)
    project.record_publish(folder, "started", WS, "", "Sales overview")
    assert svc.appstudio_publish_check(svc.project)["unfinished"]["workspace_id"] == WS


def test_sample_data_and_missing_fields_block_publishing(svc):
    blank = svc.appstudio_create("Blank one", "sales")["project"]
    got = svc.appstudio_publish_check(blank)
    assert not got["ready"] and any("sample data" in p for p in got["problems"])
    with pytest.raises(ValueError):
        svc.appstudio_publish(blank, fingerprint="x", confirm_name="Sales dev")
    spec = svc.appstudio_open(svc.project)["spec"]
    comp = next(c for p in spec["pages"] for c in p["components"] if c["fields"])
    svc.appstudio_change(svc.project, [{"op": "set", "id": comp["id"], "field": "fields", "value": ["Sales[Gone]"]}])
    got = svc.appstudio_publish_check(svc.project)
    assert not got["ready"] and any("Sales[Gone]" in p for p in got["problems"])
    assert svc.fabric.writes == []


def test_only_one_publish_at_a_time(svc, monkeypatch):
    from fabric_desktop import appstudio_service
    check = svc.appstudio_publish_check(svc.project)
    assert appstudio_service._PUBLISH_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            publish(svc, check)
    finally:
        appstudio_service._PUBLISH_LOCK.release()


def test_report_names_are_one_line():
    assert publishing.report_name("Sales\nreport", "x") == "Sales report"
    assert publishing.report_name("", "Fallback") == "Fallback"


def test_a_create_that_returns_no_id_is_looked_up_by_name(svc):
    svc.fabric.no_id = True
    got = publish(svc, svc.appstudio_publish_check(svc.project))
    assert got["done"] and got["report_id"] == REPORT
    assert project.publish_history(svc._appstudio_project(svc.project))[-1]["report_id"] == REPORT
