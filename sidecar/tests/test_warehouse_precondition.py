"""A warehouse deploy change runs only against the object its preview saw (#947): the plan gives each
change a precondition, and the worker reads that object again in the change's own transaction."""

import pytest

from fabric_desktop import warehouse, warehouse_compare

SERVER = "abc.datawarehouse.fabric.microsoft.com"
VIEW = "CREATE VIEW dbo.v_sales AS SELECT 1 AS a"
TABLE = "CREATE TABLE [dbo].[sales] (\n  [id] int NOT NULL,\n  [amount] decimal(18, 2) NULL\n);\n"


class Catalog:
    """sys.schemas, sys.objects, sys.columns and sys.sql_modules for a few objects, as a cursor sees them."""

    def __init__(self, objects):
        self.objects = dict(objects)  # "[dbo].[v_sales]" -> {"type", "definition"} or {"type", "columns"}
        self.schemas = {"dbo"}
        self.ran, self._rows, self.description, self.rowcount = [], [], None, -1

    def execute(self, sql, *params):
        self.ran.append(sql)
        self.description, self._rows = None, []
        if "FROM sys." not in sql:
            return  # the change itself
        self.description = [("x", str, None, None, None, None, True)]
        name = params[0]
        if "sys.schemas" in sql:
            self._rows = [(name,)] if name in self.schemas else []
            return
        obj = self.objects.get(name)
        if obj is None:
            return
        if "sys.objects" in sql:
            self._rows = [(obj["type"],)]
        elif "sys.columns" in sql:
            self._rows = [(c,) for c in obj.get("columns", [])]
        elif "sys.sql_modules" in sql:
            self._rows = [(obj["definition"],)]

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, size):
        return self.fetchall()

    def nextset(self):
        return False

    def changes(self):
        return [q for q in self.ran if "FROM sys." not in q]


class Con:
    timeout = 0

    def __init__(self, cursor):
        self._cursor, self.events = cursor, []

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def plan_of(state, kind="view", warehouse_text=VIEW, project_text="CREATE VIEW dbo.v_sales AS SELECT 2 AS a"):
    row = {"kind": kind, "schema": "dbo", "name": "v_sales" if kind == "view" else "sales", "path": "x.sql",
           "state": state, "project_text": project_text, "warehouse_text": warehouse_text}
    return warehouse_compare.deploy_plan([row])[0]


def deploy(monkeypatch, tmp_path, catalog, change):
    con = Con(catalog)
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said = []
    warehouse.run({"query": change["sql"], "out": str(tmp_path / "r.arrow"), "writes": True,
                   "warehouse": {"server": SERVER, "database": "WH_Gold"}, "precondition": change["precondition"]},
                  lambda: "t", lambda event, **f: said.append((event, f)))
    return said[-1], con


def test_the_plan_gives_each_change_the_object_as_the_preview_saw_it():
    alter = plan_of("changed")
    assert alter["precondition"] == {"kind": "view", "schema": "dbo", "name": "v_sales", "exists": True,
                                     "definition": warehouse.definition_fingerprint(VIEW)}
    assert plan_of("only_project")["precondition"]["exists"] is False
    add = plan_of("changed", "table", TABLE, TABLE.replace("NULL\n);", "NULL,\n  [note] varchar(10) NULL\n);"))
    assert add["action"] == "add_columns" and add["precondition"]["columns"] == ["id", "amount"]
    assert plan_of("only_warehouse", "table", TABLE)["precondition"]["columns"] == ["id", "amount"]
    for change in (alter, add):
        assert warehouse.precondition_problem(change["precondition"]) is None


def test_the_fingerprint_reads_the_definition_as_the_catalog_gives_it():
    assert warehouse.definition_fingerprint(f"{VIEW}\nGO\n") == warehouse.definition_fingerprint(f"\r\n{VIEW}  ")
    assert warehouse.definition_fingerprint(VIEW) != warehouse.definition_fingerprint(VIEW + " -- edited")


def test_an_unchanged_object_runs_the_change(monkeypatch, tmp_path):
    catalog = Catalog({"[dbo].[v_sales]": {"type": "V", "definition": VIEW}})
    change = plan_of("changed")
    (event, _), con = deploy(monkeypatch, tmp_path, catalog, change)
    assert event == "done" and catalog.changes() == [change["sql"]] and "commit" in con.events


@pytest.mark.parametrize("objects, says", [
    ({"[dbo].[v_sales]": {"type": "V", "definition": VIEW + " WHERE 1 = 1"}}, "changed definition"),
    ({}, "dropped"),
    ({"[dbo].[v_sales]": {"type": "P", "definition": VIEW}}, "another kind of object now"),
])
def test_a_view_changed_since_the_preview_is_refused_and_nothing_is_sent(monkeypatch, tmp_path, objects, says):
    catalog = Catalog(objects)
    (event, fields), con = deploy(monkeypatch, tmp_path, catalog, plan_of("changed"))
    assert event == "error" and fields["kind"] == "changed", says
    assert fields["message"] == "WH_Gold's dbo.v_sales changed since the preview, so nothing was sent. Compare again."
    assert catalog.changes() == [] and "commit" not in con.events and "rollback" in con.events


def test_an_object_someone_else_created_since_the_preview_is_refused(monkeypatch, tmp_path):
    catalog = Catalog({"[dbo].[v_sales]": {"type": "V", "definition": "CREATE VIEW dbo.v_sales AS SELECT 3 AS a"}})
    (event, fields), _ = deploy(monkeypatch, tmp_path, catalog, plan_of("only_project"))
    assert event == "error" and fields["kind"] == "changed" and catalog.changes() == []


def test_a_table_whose_columns_changed_is_refused(monkeypatch, tmp_path):
    add = plan_of("changed", "table", TABLE, TABLE.replace("NULL\n);", "NULL,\n  [note] varchar(10) NULL\n);"))
    same = Catalog({"[dbo].[sales]": {"type": "U", "columns": ["id", "amount"]}})
    assert deploy(monkeypatch, tmp_path, same, add)[0][0] == "done" and same.changes() == [add["sql"]]
    moved = Catalog({"[dbo].[sales]": {"type": "U", "columns": ["id", "amount", "note"]}})
    assert deploy(monkeypatch, tmp_path, moved, add)[0][0] == "error" and moved.changes() == []


def test_a_schema_precondition_checks_whether_it_exists(monkeypatch, tmp_path):
    change = {"sql": "CREATE SCHEMA [stage];", "precondition": {"kind": "schema", "schema": "stage", "name": "stage",
                                                               "exists": False}}
    catalog = Catalog({})
    assert deploy(monkeypatch, tmp_path, catalog, change)[0][0] == "done"
    catalog = Catalog({})
    catalog.schemas.add("stage")
    (event, fields), _ = deploy(monkeypatch, tmp_path, catalog, change)
    assert event == "error" and "Schema stage changed since the preview" in fields["message"]


@pytest.mark.parametrize("bad", [
    "dbo.v", {"kind": "index", "schema": "dbo", "name": "v", "exists": True},
    {"kind": "view", "schema": "dbo", "name": "v", "exists": True},
    {"kind": "view", "schema": "dbo", "name": "v", "exists": True, "definition": "abc"},
    {"kind": "table", "schema": "dbo", "name": "t", "exists": True, "columns": []},
    {"kind": "view", "schema": "", "name": "v", "exists": False},
])
def test_a_malformed_precondition_is_refused_before_anything_runs(bad, tmp_path, monkeypatch):
    from fabric_desktop import app_settings, sql_exec
    assert warehouse.precondition_problem(bad)
    monkeypatch.setattr(app_settings, "load", lambda: {"sql_warehouse_writes": {"value": True, "source": "user"}})
    runs = sql_exec.SqlExecutions(tmp_path, lambda: "t", resolve_warehouse=lambda ref: pytest.fail("resolved"))
    ref = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"}
    with pytest.raises(ValueError, match="The change wasn't sent"):
        runs.start("CREATE VIEW dbo.v AS SELECT 1", target="warehouse", dialect="tsql", warehouse_ref=ref,
                   allow=["warehouse_write"], confirm=True, confirm_name="WH", precondition=bad)


def test_a_precondition_on_a_query_that_reads_is_refused(tmp_path):
    from fabric_desktop import sql_exec
    runs = sql_exec.SqlExecutions(tmp_path, lambda: "t")
    with pytest.raises(ValueError, match="for a warehouse change"):
        runs.start("SELECT 1", target="warehouse", dialect="tsql",
                   warehouse_ref={"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"},
                   precondition={"kind": "view", "schema": "dbo", "name": "v", "exists": False})
