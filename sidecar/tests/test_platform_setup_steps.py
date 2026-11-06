"""Creating or joining the platform in Fabric (central/setup.py), step by step: what each run creates or
keeps, that a failing step reports itself and the rest still run, and that joining refuses a half-made
workspace. Fabric's REST API and the platform's functions are stood in for: nothing reaches a tenant."""

import json

import pytest

from fabric_desktop.central import setup, udf


class Fabric:
    """The Fabric REST calls setup makes, over in-memory workspaces and items."""

    def __init__(self, workspaces=(), items=(), fail=()):
        self.workspaces = [dict(w) for w in workspaces]
        self.items = [dict(i) for i in items]
        self.fail = set(fail)  # the calls that raise
        self.created = []

    def _check(self, name):
        if name in self.fail:
            raise RuntimeError(f"{name}: Fabric answered 403 InsufficientPrivileges")

    def list_workspaces(self):
        self._check("list_workspaces")
        return self.workspaces

    def create_workspace(self, name, capacity_id, description):
        self._check("create_workspace")
        ws = {"id": f"ws-{len(self.workspaces) + 1}", "displayName": name, "capacityId": capacity_id}
        self.workspaces.append(ws)
        self.created.append(("workspace", name))
        return ws

    def get_workspace(self, workspace_id):
        return next(w for w in self.workspaces if w["id"] == workspace_id)

    def list_items(self, workspace_id):
        return [i for i in self.items if i["ws"] == workspace_id]

    def find_lakehouse(self, ws, name):
        self._check("find_lakehouse")
        return next((i for i in self.items if i["ws"] == ws and i["type"] == "Lakehouse" and i["displayName"] == name), None)

    def create_lakehouse(self, ws, name, description):
        self._check(f"create_lakehouse:{name}")
        item = {"id": f"lh-{name}", "ws": ws, "type": "Lakehouse", "displayName": name}
        self.items.append(item)
        self.created.append(("lakehouse", name))
        return item

    def save_item(self, ws, item_type, name, definition=None, description=""):
        self._check(f"save_item:{item_type}")
        found = next((i for i in self.items if i["ws"] == ws and i["type"] == item_type and i["displayName"] == name), None)
        if found:
            found["definition"] = definition
            return {"id": found["id"], "created": False}
        item = {"id": f"{item_type}-{name}", "ws": ws, "type": item_type, "displayName": name, "definition": definition}
        self.items.append(item)
        self.created.append((item_type, name))
        return {"id": item["id"], "created": True}

    def save_variable_library(self, ws, name, parts, description):
        self._check("save_variable_library")
        self.varlib = parts
        found = next((i for i in self.items if i["ws"] == ws and i["displayName"] == name), None)
        if found:
            return {"id": found["id"], "created": False}
        self.items.append({"id": "vl-1", "ws": ws, "type": "VariableLibrary", "displayName": name})
        return {"id": "vl-1", "created": True}


class Platform:
    """UDF_Platform: answers ensure_schema, whoami and claim_admin."""
    calls = []
    answers = {}

    def __init__(self, token, workspace_id, functions_id):
        self.where = (workspace_id, functions_id)

    def call(self, op, **kw):
        Platform.calls.append(op)
        answer = Platform.answers.get(op, {})
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def platform(monkeypatch):
    Platform.calls, Platform.answers = [], {"ensure_schema": {"created": ["catalog.domains", "keys.tables"]},
                                            "whoami": {"can_claim": True}}
    monkeypatch.setattr(setup, "FabricPlatform", Platform)
    monkeypatch.setattr(udf, "definition", lambda ws, sql_id: {"parts": [ws, sql_id]})
    return Platform


def _token():
    return "execute-token"


def _steps(out):
    return [(s["step"], s["status"]) for s in out["steps"]]


# --------------------------------------------------------------------------- a new platform

def test_a_new_platform_creates_every_item_and_makes_you_its_first_admin():
    fabric = Fabric()
    out = setup.setup(fabric, _token, "dev", capacity_id="cap-1")
    assert out["ready"] and all(status == "done" for _, status in _steps(out))
    assert fabric.created == [("workspace", "Fabric Desktop Platform - Dev"), ("lakehouse", "LH_Meta"),
                              ("lakehouse", "LH_Landing"), ("SQLDatabase", "SQL_Platform"),
                              ("UserDataFunction", setup.FUNCTIONS)]
    assert fabric.workspaces[0]["capacityId"] == "cap-1"
    cfg = out["config"]
    assert cfg["workspace_name"] == "Fabric Desktop Platform - Dev" and cfg["varlib_id"] == "vl-1"
    tables = next(s for s in out["steps"] if s["step"] == "Platform tables")["detail"]
    assert tables == "Created 2 tables, you're its first admin"
    assert Platform.calls == ["ensure_schema", "whoami", "claim_admin"]
    variables = {v["name"]: v["value"] for v in json.loads(fabric.varlib["variables.json"])["variables"]}
    assert variables == {"PLATFORM_WORKSPACE_ID": cfg["workspace_id"], "PLATFORM_META_LAKEHOUSE_ID": "lh-LH_Meta",
                         "PLATFORM_LANDING_LAKEHOUSE_ID": "lh-LH_Landing", "PLATFORM_SQL_DATABASE_ID": cfg["sql_id"],
                         "PLATFORM_FUNCTIONS_ID": cfg["functions_id"]}
    assert json.loads(fabric.varlib["settings.json"])["valueSetsOrder"] == []


def test_a_named_workspace_is_found_by_name_rather_than_made_twice():
    fabric = Fabric(workspaces=[{"id": "ws-9", "displayName": "Platform Prod"}])
    out = setup.setup(fabric, _token, "prod", name="  Platform Prod ")
    assert out["steps"][0]["detail"] == "Found Platform Prod" and out["config"]["workspace_id"] == "ws-9"
    assert ("workspace", "Platform Prod") not in fabric.created


def test_running_again_keeps_what_is_there_and_an_admin_is_not_claimed_twice(platform):
    fabric = Fabric()
    first = setup.setup(fabric, _token, "test")
    Platform.calls, Platform.answers = [], {"ensure_schema": {"created": []}, "whoami": {"can_claim": False}}
    again = setup.setup(fabric, _token, "test", existing=first["config"])
    details = {s["step"]: s["detail"] for s in again["steps"]}
    assert details["Platform workspace"] == "Kept Fabric Desktop Platform - Test"
    assert details["LH_Meta"] == "Kept LH_Meta" and details["SQL_Platform"] == "Kept SQL_Platform"
    assert details[setup.FUNCTIONS] == f"Updated {setup.FUNCTIONS}" and details["VL_Platform"] == "Updated VL_Platform"
    assert details["Platform tables"] == "Tables were already there"
    assert "claim_admin" not in Platform.calls and again["ready"]


def test_an_unknown_environment_is_refused_before_fabric_is_called():
    fabric = Fabric(fail={"list_workspaces"})
    with pytest.raises(ValueError, match="dev, test, prod"):
        setup.setup(fabric, _token, "staging")


# --------------------------------------------------------------------------- a step that fails

def test_no_workspace_means_nothing_else_is_tried():
    fabric = Fabric(fail={"create_workspace"})
    out = setup.setup(fabric, _token, "dev")
    assert _steps(out) == [("Platform workspace", "failed")] and not out["ready"]
    assert "InsufficientPrivileges" in out["steps"][0]["detail"] and fabric.created == []


def test_a_failed_lakehouse_is_reported_and_the_other_steps_still_run():
    fabric = Fabric(fail={"create_lakehouse:LH_Meta"})
    out = setup.setup(fabric, _token, "dev")
    assert dict(_steps(out))["LH_Meta"] == "failed" and dict(_steps(out))["LH_Landing"] == "done"
    assert dict(_steps(out))["VL_Platform"] == "done" and not out["ready"]


def test_without_the_sql_database_the_functions_and_tables_wait_for_the_next_run():
    fabric = Fabric(fail={"save_item:SQLDatabase"})
    out = setup.setup(fabric, _token, "dev")
    assert [s for s, _ in _steps(out)] == ["Platform workspace", "LH_Meta", "LH_Landing", "SQL_Platform", "VL_Platform"]
    assert not out["ready"] and Platform.calls == []


def test_tables_that_cant_be_made_say_how_to_connect_the_functions(platform):
    Platform.answers["ensure_schema"] = RuntimeError("Connection alias 'platform' not found")
    out = setup.setup(Fabric(), _token, "dev")
    failed = next(s for s in out["steps"] if s["step"] == "Platform tables")
    assert failed["status"] == "failed" and failed["detail"].startswith("Connection alias 'platform' not found. If ")
    assert f'alias "{udf.ALIAS}"' in failed["detail"] and "Finish setup" in failed["detail"]
    assert dict(_steps(out))["VL_Platform"] == "done" and not out["ready"]


def test_an_odd_ensure_schema_answer_counts_as_tables_already_there(platform):
    Platform.answers["ensure_schema"] = ["not", "a", "dict"]
    Platform.answers["whoami"] = {}
    out = setup.setup(Fabric(), _token, "dev")
    assert next(s for s in out["steps"] if s["step"] == "Platform tables")["detail"] == "Tables were already there"


# --------------------------------------------------------------------------- one landing workspace

def test_the_landing_workspace_is_made_once_and_kept_after():
    fabric = Fabric()
    out = setup.landing_setup(fabric, capacity_id="cap-1")
    assert out["ready"] and out["config"]["lakehouse_id"] == "lh-LH_Landing"
    assert [s["detail"] for s in out["steps"]] == ["Created Platform - Landing", "Created LH_Landing"]
    again = setup.landing_setup(fabric, existing=out["config"])
    assert [s["detail"] for s in again["steps"]] == ["Kept Platform - Landing", "Kept LH_Landing"]
    found = setup.landing_setup(fabric)
    assert found["steps"][0]["detail"] == "Found Platform - Landing"


def test_a_landing_workspace_that_cant_be_made_stops_there():
    out = setup.landing_setup(Fabric(fail={"list_workspaces"}))
    assert _steps(out) == [("Landing workspace", "failed")] and not out["ready"]


def test_a_landing_lakehouse_that_cant_be_made_is_reported_for_finish_to_run_again():
    out = setup.landing_setup(Fabric(fail={"create_lakehouse:LH_Landing"}))
    assert _steps(out) == [("Landing workspace", "done"), ("LH_Landing", "failed")] and not out["ready"]
    assert "lakehouse_id" not in out["config"]


# --------------------------------------------------------------------------- joining one that exists

def _platform_items(ws, skip=()):
    items = [("Lakehouse", "LH_Meta"), ("Lakehouse", "LH_Landing"), ("SQLDatabase", "SQL_Platform"),
             ("UserDataFunction", setup.FUNCTIONS), ("VariableLibrary", "VL_Platform"),
             ("Lakehouse", "LH_Meta_old")]  # a name that only starts the same isn't taken
    return [{"id": f"{t}-{n}", "ws": ws, "type": t, "displayName": n} for t, n in items if n not in skip]


def test_joining_finds_each_item_by_type_and_name():
    fabric = Fabric(workspaces=[{"id": "ws-1", "displayName": "Central Platform"}], items=_platform_items("ws-1"))
    cfg = setup.join(fabric, "prod", "ws-1")
    assert cfg == {"workspace_id": "ws-1", "workspace_name": "Central Platform", "meta_id": "Lakehouse-LH_Meta",
                   "landing_id": "Lakehouse-LH_Landing", "sql_id": "SQLDatabase-SQL_Platform",
                   "functions_id": f"UserDataFunction-{setup.FUNCTIONS}", "varlib_id": "VariableLibrary-VL_Platform"}


def test_joining_a_half_made_workspace_names_what_is_missing():
    fabric = Fabric(workspaces=[{"id": "ws-1", "displayName": "Central Platform"}],
                    items=_platform_items("ws-1", skip={"SQL_Platform"}))
    with pytest.raises(LookupError, match="Central Platform isn't a complete Platform workspace: SQL_Platform is missing"):
        setup.join(fabric, "dev", "ws-1")
    fabric = Fabric(workspaces=[{"id": "ws-2"}], items=_platform_items("ws-2", skip={"LH_Meta", setup.FUNCTIONS}))
    with pytest.raises(LookupError, match=f"ws-2 isn't .*LH_Meta, {setup.FUNCTIONS} are missing"):
        setup.join(fabric, "dev", "ws-2")


def test_joining_without_a_variable_library_still_works():
    fabric = Fabric(workspaces=[{"id": "ws-1", "displayName": "P"}], items=_platform_items("ws-1", skip={"VL_Platform"}))
    assert setup.join(fabric, "test", "ws-1")["varlib_id"] == ""


def test_joining_an_unknown_environment_is_refused():
    with pytest.raises(ValueError, match="dev, test, prod"):
        setup.join(Fabric(), "qa", "ws-1")
