"""The local data read-only tools for an external AI tool (#2363): local data projects, their tables
and a Delta table's history, through the registry the MCP server and local API use.

The MCP server is a process of its own (``fabric-desktop mcp``), with its own engine. A DuckDB
file has one writer, so these tools read each project's saved catalog and Delta logs and never
open its database: the app keeps it open while the tool reads. The tests below hold the app's
connection open in one Service and call the tools through another on the same data folder."""

import pytest

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.service import Service

IDS = ("fabric_desktop_list_local_projects", "fabric_desktop_get_local_tables", "fabric_desktop_get_local_table_history")


@pytest.fixture
def reg():
    r = Registry()
    register_core(r)
    return r


@pytest.fixture
def app(tmp_path):
    """The app's engine, with a project reading a folder of CSV and holding a Delta table of its own,
    its database left open."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "orders.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    s = Service(root=tmp_path / "app")
    pid = s.local_create_project("Sales")["id"]
    s.local_add_source(pid, str(data))
    s.local_write_delta(pid, "main.big_orders", "SELECT * FROM orders WHERE amount > 5")
    s.local_write_delta(pid, "main.big_orders", "SELECT 3 AS id, 30 AS amount", mode="append")
    assert s.local_data.engine(pid).is_open
    s.pid = pid
    yield s
    s.local_data.close_all()


@pytest.fixture
def tool(app, tmp_path):
    """The MCP server's own engine on the same data folder."""
    s = Service(root=tmp_path / "app")
    yield s
    s.local_data.close_all()


def call(reg, svc, cap_id, **args):
    return reg.invoke(svc, cap_id, args, CallContext(Caller("t", "test"), timeout=30))


def opened(svc) -> list:
    return [pid for pid, e in svc.local_data._engines.items() if e.is_open]


def test_the_tools_are_read_only_metadata_and_refused_for_a_workspace_limited_tool(reg):
    from fabric_desktop.integrations import scope
    for cap_id in IDS:
        cap = reg.get(cap_id)
        assert cap.read_only and not cap.destructive and cap.approval == "never", cap_id
        assert cap.permission == "READ_METADATA" and cap.locations == ("this_pc",), cap_id
        assert cap.workspaces == scope.UNKNOWN, cap_id  # a source folder can be a OneLake folder


def test_projects_are_listed_without_opening_a_database(reg, app, tool):
    out = call(reg, tool, "fabric_desktop_list_local_projects")
    assert [p["id"] for p in out["projects"]] == [app.pid]
    assert out["projects"][0]["name"] == "Sales" and out["projects"][0]["tables"] == 2
    assert opened(tool) == []


def test_tables_come_from_the_saved_catalog_with_their_columns(reg, app, tool):
    out = call(reg, tool, "fabric_desktop_get_local_tables", project=app.pid)
    assert out["project"] == app.pid and out["total"] == 2
    by_name = {t["table"]: t for t in out["items"]}
    assert by_name["orders"]["kind"] == "file" and by_name["orders"]["format"] == "csv"
    assert [c["name"] for c in by_name["orders"]["columns"]] == ["id", "amount"]
    assert by_name["big_orders"]["format"] == "delta"
    assert all("rows" not in t for t in out["items"])
    assert opened(tool) == []
    page = call(reg, tool, "fabric_desktop_get_local_tables", project=app.pid, limit=1)
    assert len(page["items"]) == 1 and page["next_offset"] == 1


def test_a_delta_tables_history_is_read_from_its_log(reg, app, tool):
    out = call(reg, tool, "fabric_desktop_get_local_table_history", project=app.pid, table="main.big_orders")
    assert out["table"] == "main.big_orders" and out["version"] == 1
    assert [v["version"] for v in out["versions"]] == [1, 0]
    assert out["versions"][0]["rows_added"] == 1
    assert opened(tool) == []


def test_a_file_table_has_no_history(reg, app, tool):
    with pytest.raises(IntegrationError) as err:
        call(reg, tool, "fabric_desktop_get_local_table_history", project=app.pid, table="main.orders")
    assert err.value.code == "invalid_input" and "isn't a Delta table" in err.value.message


def test_an_unknown_project_is_not_found_with_a_hint(reg, tool):
    for cap_id, args in (("fabric_desktop_get_local_tables", {"project": "nope"}),
                         ("fabric_desktop_get_local_table_history", {"project": "nope", "table": "main.t"})):
        with pytest.raises(IntegrationError) as err:
            call(reg, tool, cap_id, **args)
        assert err.value.code == "not_found" and "fabric_desktop_list_local_projects" in err.value.hint, cap_id


def test_a_project_id_that_could_be_a_path_is_refused_before_anything_runs(reg, tool):
    for bad in ("../app", "C:\\Users", "a/b", ""):
        with pytest.raises(IntegrationError) as err:
            call(reg, tool, "fabric_desktop_get_local_tables", project=bad)
        assert err.value.code == "invalid_input", bad


def test_the_tables_are_a_resource_too(reg, app):
    cap, args = reg.resolve(f"fabric-desktop://local-data/{app.pid}/tables")
    assert cap.id == "fabric_desktop_get_local_tables" and args == {"project": app.pid}
