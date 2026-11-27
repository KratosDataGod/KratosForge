"""Lineage, change impact and the OpenLineage export on the awkward shapes a project can take: cycles (what
the save gate lets through and what only a hand-written file can hold), odd names, and changes in the middle
of a chain."""

import json

import pytest

from fabric_desktop.meta import change_impact, lineage, openlineage
from fabric_desktop.meta.project import entity_name_problem, validate
from fabric_desktop.service import Service
from tests.test_lineage_scale import validate_all, write_project
from tests.test_openlineage import spec  # noqa: F401 - the fixture is used by name


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def edit(svc, name, old, new):
    text = svc.meta_entity(name)["text"]
    assert old in text, old
    return svc.meta_save_entity(name, text.replace(old, new, 1))


def hand_edit(svc, name, old, new):
    """Change an entity's file on disk, as a person or a merge could, without the save path."""
    path = svc._project().entities_dir / f"{name}.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def pairs(steps):
    return [(s["entity"], s["column"]) for s in steps]


def every_run(project, status="ok"):
    return [{"run_id": f"r-{e}", "entity": e, "status": status, "started_at": "2026-11-14T10:00:00+00:00", "seconds": 1}
            for e in project.entities]


def by_job(out):
    return {e["job"]["name"]: e for e in out["events"]}


def tables(res):
    return {(i["name"], i["severity"]) for i in res["items"] if i["kind"] == "table"}


# ---- 2. cycles -------------------------------------------------------------------------------------------

def test_two_tables_that_read_each_other_end_and_add_no_edge(tmp_path, spec):
    """A cycle the save gate refuses (dependency cycle), as a hand-written project can still hold it."""
    project = write_project(tmp_path / "p", {
        "silver.x": {"source": {"entity": "silver.y"}, "contract": {"columns": {"id": "int64", "v": "int64"}}},
        "silver.y": {"source": {"entity": "silver.x"}, "contract": {"columns": {"id": "int64", "v": "int64", "w": "int64"}},
                     "derive": [{"column": "w", "expr": "v + 1"}]},
    })
    assert any(e.startswith("dependency cycle: ") for e in validate(project))
    assert lineage.entity_columns(project, "silver.x")["v"]["inputs"] == [{"entity": "silver.y", "column": "v"}]
    assert lineage.entity_columns(project, "silver.y")["v"]["inputs"] == [{"entity": "silver.x", "column": "v"}]
    assert pairs(lineage.upstream(project, "silver.x", "v")) == [("silver.x", "v"), ("silver.y", "v")]
    assert pairs(lineage.downstream(project, "silver.x", "v")) == [("silver.y", "v"), ("silver.y", "w")]
    # x.w doesn't exist: x's source has w, but x doesn't write it
    assert pairs(lineage.upstream(project, "silver.x", "w")) == [("silver.x", "w")]
    res = change_impact.column_impact(project, "silver.x", "v", "drop")
    assert tables(res) == {("silver.y", "breaks")}  # never x itself, which the cycle brings back
    out = openlineage.run_events(project, every_run(project), "dev")
    ev = by_job(out)
    assert [i["name"] for i in ev["silver.x"]["inputs"]] == ["LH_Silver.dbo.y"]
    assert [i["name"] for i in ev["silver.y"]["inputs"]] == ["LH_Silver.dbo.x"]
    validate_all(spec, out["events"])


def test_a_cycle_through_the_save_path_is_listed_and_lineage_still_ends(svc, spec):
    """Saving never refuses a cycle; the save gate lists it. silver.orders already checks values in
    silver.customers, so silver.customers reading silver.orders closes a loop."""
    edit(svc, "silver.customers", 'entity = "bronze.customers"', 'entity = "silver.orders"')
    project = svc._project()
    assert "dependency cycle: silver.customers -> silver.orders -> silver.customers" in validate(project)
    up = pairs(lineage.upstream(project, "silver.customers", "customer_id"))
    assert up == [("silver.customers", "customer_id"), ("silver.orders", "customer_id"), ("bronze.orders", "customer_id")]
    down = pairs(lineage.downstream(project, "bronze.orders", "customer_id"))
    assert ("silver.customers", "customer_id") in down and ("gold.dim_customer", "customer_id") in down
    res = change_impact.column_impact(project, "silver.orders", "customer_id", "drop")
    assert "bronze.orders" not in {n for n, _ in tables(res)}
    validate_all(spec, openlineage.run_events(project, every_run(project), "dev")["events"])


def test_a_table_that_reads_itself_passes_the_save_gate_and_adds_only_its_own_edge(svc, spec):
    saved = edit(svc, "silver.customers", 'entity = "bronze.customers"', 'entity = "silver.customers"')
    project = svc._project()
    assert saved["errors"] == [] and validate(project) == []  # the gate allows a table to read itself
    cols = lineage.entity_columns(project, "silver.customers")
    assert cols["city"]["inputs"] == [{"entity": "silver.customers", "column": "city"}]
    assert pairs(lineage.upstream(project, "silver.customers", "city")) == [("silver.customers", "city")]
    assert pairs(lineage.downstream(project, "silver.customers", "city")) == [("gold.dim_customer", "city")]
    assert not any(e == "bronze.customers" for e, _ in pairs(lineage.downstream(project, "bronze.customers", "city")))
    res = change_impact.column_impact(project, "silver.customers", "city", "drop")
    assert tables(res) == {("gold.dim_customer", "breaks")}
    out = openlineage.run_events(project, every_run(project), "dev")
    me = by_job(out)["silver.customers"]
    assert me["inputs"] == []  # its own table is its output, never an input of its own
    validate_all(spec, out["events"])


def test_derives_that_name_each_other_follow_their_order_not_a_loop(svc, spec):
    """Found here: derives run in order, but lineage linked a derive to a column of the table even when that
    column is only derived after it, so amount = discount * 2 then discount = amount + 1 read as a loop
    (amount <- discount <- amount), and net_amount, derived before both, traced to neither bronze column.
    The save gate allows it (each derive reads what's there when it runs)."""
    saved = edit(svc, "silver.orders", 'expr = "amount - coalesce(discount, 0)"\n',
                 'expr = "amount - coalesce(discount, 0)"\n\n[[derive]]\ncolumn = "amount"\nexpr = "discount * 2"\n\n'
                 '[[derive]]\ncolumn = "discount"\nexpr = "amount + 1"\n')
    project = svc._project()
    assert saved["errors"] == [] and validate(project) == []
    cols = lineage.entity_columns(project, "silver.orders")
    assert cols["net_amount"]["inputs"] == [{"entity": "bronze.orders", "column": "amount"},
                                            {"entity": "bronze.orders", "column": "discount"}]
    assert cols["amount"]["inputs"] == [{"entity": "bronze.orders", "column": "discount"}]
    assert cols["discount"]["inputs"] == [{"entity": "silver.orders", "column": "amount"}]  # amount comes first
    assert pairs(lineage.upstream(project, "silver.orders", "discount")) == [
        ("silver.orders", "discount"), ("silver.orders", "amount"), ("bronze.orders", "discount")]
    assert ("silver.orders", "amount") not in pairs(lineage.downstream(project, "bronze.orders", "amount"))
    res = change_impact.column_impact(project, "bronze.orders", "amount", "drop")
    assert ("silver.orders", "breaks") in tables(res) and "bronze.orders" not in {n for n, _ in tables(res)}
    ev = by_job(openlineage.run_events(project, every_run(project), "dev"))["silver.orders"]
    fields = ev["outputs"][0]["facets"]["columnLineage"]["fields"]
    assert {(f["name"], f["field"]) for f in fields["net_amount"]["inputFields"]} == {
        ("LH_Bronze.dbo.orders", "amount"), ("LH_Bronze.dbo.orders", "discount")}
    assert [(f["name"], f["field"]) for f in fields["discount"]["inputFields"]] == [("LH_Bronze.dbo.orders", "discount")]
    validate_all(spec, [ev])


def test_hand_written_derives_in_a_loop_end_and_only_name_what_they_read(tmp_path, spec):
    """x = y + 1, y = x + 1, z = z * 2 and the same column derived twice: the gate refuses the first (y
    isn't there yet); lineage still ends, with no loop."""
    project = write_project(tmp_path / "p", {
        "bronze.raw": {"source": {"files": "landing/raw/*.csv"}, "contract": {"columns": {"a": "int64", "z": "int64"}}},
        "silver.s": {"source": {"entity": "bronze.raw"},
                     "contract": {"columns": {"a": "int64", "x": "int64", "y": "int64", "z": "int64"}},
                     "derive": [{"column": "x", "expr": "y + 1"}, {"column": "y", "expr": "x + 1"},
                                {"column": "z", "expr": "z * 2"}, {"column": "a", "expr": "a + 1"},
                                {"column": "a", "expr": "a + x"}]},
    })
    assert "silver.s: derive x: uses unknown column y" in validate(project)
    cols = lineage.entity_columns(project, "silver.s")
    assert cols["x"]["inputs"] == [{"entity": "bronze.raw", "column": "y", "missing": True}]
    assert cols["y"]["inputs"] == [{"entity": "silver.s", "column": "x"}]
    assert cols["z"]["inputs"] == [{"entity": "bronze.raw", "column": "z"}]
    assert cols["a"]["inputs"] == [{"entity": "bronze.raw", "column": "a"}, {"entity": "silver.s", "column": "x"}]
    for col in cols:
        steps = pairs(lineage.upstream(project, "silver.s", col))
        assert len(steps) == len(set(steps))
    assert set(pairs(lineage.downstream(project, "silver.s", "x"))) == {("silver.s", "y"), ("silver.s", "a")}
    for change in change_impact.CHANGES:
        change_impact.column_impact(project, "silver.s", "x", change, "x2", "string")
    validate_all(spec, openlineage.run_events(project, every_run(project), "dev")["events"])


def test_a_lookup_into_a_table_that_isnt_a_dimension_says_its_key_is_missing(tmp_path):
    """Found by the random projects: a lookup whose dim isn't a dimension (the gate refuses it; a file can
    hold it) pointed at the dim's surrogate key as if the dim made it."""
    project = write_project(tmp_path / "p", {
        "silver.d": {"source": {"files": "landing/d/*.csv"}, "load": {"mode": "full"},
                     "scd": {"surrogate_key": "sk"}, "contract": {"columns": {"id": "int64"}}},
        "gold.f": {"source": {"entity": "silver.d"}, "contract": {"columns": {"id": "int64", "d_sk": "int64"}},
                   "lookup": [{"dim": "silver.d", "on": "id", "as": "d_sk"}]},
    })
    assert "gold.f: lookup silver.d is not a dimension" in validate(project)
    assert lineage.entity_columns(project, "gold.f")["d_sk"]["inputs"][0] == {
        "entity": "silver.d", "column": "sk", "missing": True}
    assert pairs(lineage.upstream(project, "gold.f", "d_sk")) == [("gold.f", "d_sk"), ("gold.f", "id"), ("silver.d", "id")]


def test_a_run_stamp_read_from_the_source_is_there_not_missing(tmp_path):
    """Found by the random projects: the gate lets a table read its source's _run_id, but lineage called that
    column missing, so upstream stopped short while downstream of the source's _run_id still listed it."""
    project = write_project(tmp_path / "p", {
        "bronze.raw": {"source": {"files": "landing/raw/*.csv"}, "contract": {"columns": {"id": "int64"}}},
        "silver.s": {"source": {"entity": "bronze.raw"}, "contract": {"columns": {"id": "int64", "batch": "string"}},
                     "derive": [{"column": "batch", "expr": "_run_id"}]},
    })
    assert validate(project) == []
    assert lineage.entity_columns(project, "silver.s")["batch"]["inputs"] == [{"entity": "bronze.raw", "column": "_run_id"}]
    assert ("bronze.raw", "_run_id") in pairs(lineage.upstream(project, "silver.s", "batch"))
    assert pairs(lineage.downstream(project, "bronze.raw", "_run_id")) == [("silver.s", "batch")]


# ---- 3. odd names ----------------------------------------------------------------------------------------

ODD_COLUMNS = ["a b", "ünïcødé ✓", "it's \"quoted\"", "dot.ted", "x" * 300, "tab\there", "Ω≈ç√", "SELECT", "1st"]
CLEANED_COLUMNS = {  # names that would carry a user or a secret out of the PC: cleaned in what the export sends
    "https://user:pw@example.com/col?sig=abc": "https://example.com/col",  # pragma: allowlist secret (made-up)
    "C:\\Users\\bob\\data.csv": "<this PC>/data.csv",
    "/home/alice/data.csv": "<this PC>/data.csv",
}
ODD_ENTITIES = ["silver.ünïcødé tàble", "silver.it's a table", "silver.my schema.a b", "silver." + "t" * 120,
                "silver.example.com", "silver.%2Fetc%2Fpasswd", "silver. leading space"]


def test_which_odd_entity_names_the_loader_takes():
    assert entity_name_problem("silver.example.com") is None  # three parts: schema example, table com
    assert entity_name_problem("silver.www.example.com") is not None  # four parts
    for bad in ("silver.https://x", "silver.C:\\x", "silver./etc", "silver.", "silver..x"):
        assert entity_name_problem(bad) is not None, bad


def sql_name(column):
    return '"' + column.replace('"', '""') + '"'


def _odd_project(root):
    cols = ODD_COLUMNS + list(CLEANED_COLUMNS)
    ents = {"bronze.raw": {"source": {"files": "landing/ünï cødé/*.csv"}, "contract": {"columns": {c: "string" for c in cols}}}}
    for n, name in enumerate(ODD_ENTITIES):
        if entity_name_problem(name) is not None:
            continue
        src = "bronze.raw" if n == 0 else ODD_ENTITIES[0]
        ents[name] = {"source": {"entity": src}, "contract": {"columns": {c: "string" for c in cols} | {"both": "string"}},
                      "derive": [{"column": "both", "expr": " || ".join(sql_name(c) for c in cols[:3])}]}
    return ents


def test_odd_names_come_through_lineage_impact_and_the_export_unchanged(tmp_path, spec):
    """Found here: a contract column named like a path or a URL with a user in it (a CSV header can be
    anything) left the PC as it is in its table's schema and column lineage, while the events of the tables
    that read it named it cleaned, so the two sides of the edge didn't meet either."""
    ents = _odd_project(tmp_path)
    assert set(ODD_ENTITIES) <= set(ents)  # the loader takes every one of them
    project = write_project(tmp_path / "p", ents)
    assert set(project.entities) == set(ents)
    first = ODD_ENTITIES[0]
    for col in ODD_COLUMNS:
        rec = lineage.entity_columns(project, first)[col]
        assert rec["inputs"] == [{"entity": "bronze.raw", "column": col}]
        assert pairs(lineage.upstream(project, ODD_ENTITIES[1], col))[-1] == ("bronze.raw", col)
    both = lineage.entity_columns(project, first)["both"]
    assert both["label"] == "declared" and [i["column"] for i in both["inputs"]] == ODD_COLUMNS[:3]
    down = pairs(lineage.downstream(project, "bronze.raw", ODD_COLUMNS[2]))
    assert (first, ODD_COLUMNS[2]) in down and (first, "both") in down and (ODD_ENTITIES[1], "both") in down
    res = change_impact.column_impact(project, first, ODD_COLUMNS[1], "rename", "plain")
    assert {n for n, _ in tables(res)} == set(ODD_ENTITIES)  # its own derive and every table that reads it, as named

    out = openlineage.run_events(project, every_run(project), "dev")
    validate_all(spec, out["events"])
    for ws in (None, "5b9c1d2e-3f4a-4b5c-8d6e-7f8091a2b3c4"):
        validate_all(spec, openlineage.run_events(project, every_run(project), "dev", ws)["events"])
    ev = by_job(out)
    assert set(ev) == set(ents)  # job names unchanged
    for name in ODD_ENTITIES:
        e = project.entities[name]
        output = ev[name]["outputs"][0]
        assert output["name"] == f"LH_Silver.{e.schema}.{e.table}"
        schema = [f["name"] for f in output["facets"]["schema"]["fields"]]
        assert schema[:len(ODD_COLUMNS)] == ODD_COLUMNS
        # two that clean to one name are one schema field, so the facet never lists a name twice
        assert schema[len(ODD_COLUMNS):len(ODD_COLUMNS) + 2] == list(dict.fromkeys(CLEANED_COLUMNS.values()))
        assert len(schema) == len(set(schema))
        fields = output["facets"]["columnLineage"]["fields"]
        for col in ODD_COLUMNS:
            assert [f["field"] for f in fields[col]["inputFields"]] == [col]
        # a column named like a path or URL goes out cleaned on both sides of the edge, so they still meet
        # (two that clean to one name share it, and the input they share is listed once)
        assert [f["field"] for f in fields["https://example.com/col"]["inputFields"]] == ["https://example.com/col"]
        assert [f["field"] for f in fields["<this PC>/data.csv"]["inputFields"]] == ["<this PC>/data.csv"]
        inputs = {f["name"] for i in ev[name]["inputs"] for f in i["facets"]["schema"]["fields"]}
        assert set(ODD_COLUMNS) <= inputs and not set(CLEANED_COLUMNS) & inputs
    assert ev["bronze.raw"]["inputs"][0]["name"] == "landing/ünï cødé/*.csv"  # a relative path stays as it is
    assert "pw@" not in json.dumps(out) and "bob" not in json.dumps(out) and "alice" not in json.dumps(out)


# ---- 4. a change in the middle of a chain: bronze -> silver -> gold -> the semantic model ------------------

def items(res):
    return {(i["kind"], i["name"], i["severity"]) for i in res["items"]}


def test_dropping_a_silver_column_lists_only_what_comes_after(svc):
    project = svc._project()
    res = change_impact.column_impact(project, "silver.orders", "discount", "drop")
    assert items(res) == {("table", "silver.orders", "breaks"), ("table", "gold.fact_sales", "check")}
    res = change_impact.column_impact(project, "silver.orders", "order_date", "drop")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("model", "SM_Gold", "check")}
    res = change_impact.column_impact(project, "silver.orders", "net_amount", "drop")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("model", "SM_Gold", "check")}


def test_renaming_a_silver_column_lists_only_what_comes_after(svc):
    project = svc._project()
    res = change_impact.column_impact(project, "silver.orders", "order_date", "rename", "ordered_on")
    assert items(res) == {("table", "gold.fact_sales", "breaks")}
    why = res["items"][0]["why"]
    assert any("reads order_date by name" in w for w in why) and any("customer_sk is built from" in w for w in why)
    res = change_impact.column_impact(project, "silver.orders", "customer_id", "rename", "cust")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("table", "silver.orders", "breaks"),
                          ("model", "SM_Gold", "breaks")}
    # an edit of silver's contract that renames the column gives the same answer
    spec = {"contract": {"columns": {("ordered_on" if c == "order_date" else c): t
                                     for c, t in project.entities["silver.orders"].spec["contract"]["columns"].items()}}}
    [one] = change_impact.edit_impact(project, "silver.orders", spec)
    assert (one["column"], one["change"], items(one)) == ("order_date", "rename", {("table", "gold.fact_sales", "breaks")})


def _status_renamed_in_silver(svc):
    """silver.orders takes bronze's status as order_status ([source] rename) and gold carries it on."""
    hand_edit(svc, "silver.orders", 'entity = "bronze.orders"', 'entity = "bronze.orders"\nrename = { "status" = "order_status" }')
    hand_edit(svc, "silver.orders", 'status = "string"', 'order_status = "string"')
    hand_edit(svc, "silver.orders", 'column = "status"', 'column = "order_status"')
    hand_edit(svc, "silver.orders", "status <> 'returned'", "order_status <> 'returned'")
    hand_edit(svc, "gold.fact_sales", 'net_amount = "float64"', 'net_amount = "float64"\norder_status = "string"')
    return svc._project()


def test_a_column_renamed_in_the_middle_by_source_rename(svc):
    project = _status_renamed_in_silver(svc)
    assert pairs(lineage.upstream(project, "gold.fact_sales", "order_status")) == [
        ("gold.fact_sales", "order_status"), ("silver.orders", "order_status"), ("bronze.orders", "status")]
    res = change_impact.column_impact(project, "bronze.orders", "status", "drop")
    assert items(res) == {("table", "silver.orders", "breaks"), ("table", "gold.fact_sales", "check"),
                          ("model", "SM_Gold", "check")}
    res = change_impact.column_impact(project, "bronze.orders", "status", "rename", "state")
    assert items(res) == {("table", "silver.orders", "breaks"), ("table", "gold.fact_sales", "check")}
    # at the middle, under its new name: gold breaks, silver's own clean names it, bronze is never listed
    res = change_impact.column_impact(project, "silver.orders", "order_status", "drop")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("table", "silver.orders", "breaks"),
                          ("model", "SM_Gold", "check")}
    assert [i["why"] for i in res["items"] if i["name"] == "silver.orders"] == [
        ["a [[clean]] step names order_status; the save gate refuses the change until you update it"]]
    res = change_impact.column_impact(project, "silver.orders", "order_status", "rename", "st")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("table", "silver.orders", "breaks")}


def test_a_column_dropped_in_silver_and_derived_again_in_gold(svc):
    """discount stops at silver (net_amount still reads bronze's), and gold works it out again from what
    silver has: gold never reads discount by name, so dropping it upstream is a check there, not a break."""
    hand_edit(svc, "silver.orders", 'discount = "float64"\n', "")
    hand_edit(svc, "gold.fact_sales", 'net_amount = "float64"', 'net_amount = "float64"\ndiscount = "float64"')
    hand_edit(svc, "gold.fact_sales", "[contract]", '[[derive]]\ncolumn = "discount"\nexpr = "amount - net_amount"\n\n[contract]')
    project = svc._project()
    assert validate(project) == []
    cols = lineage.entity_columns(project, "silver.orders")
    assert "discount" not in cols and {"entity": "bronze.orders", "column": "discount"} in cols["net_amount"]["inputs"]
    assert lineage.entity_columns(project, "gold.fact_sales")["discount"]["inputs"] == [
        {"entity": "silver.orders", "column": "amount"}, {"entity": "gold.fact_sales", "column": "net_amount"}]
    res = change_impact.column_impact(project, "bronze.orders", "discount", "drop")
    assert items(res) == {("table", "silver.orders", "breaks"), ("table", "gold.fact_sales", "check")}
    gold = next(i for i in res["items"] if i["name"] == "gold.fact_sales")
    assert gold["why"] == ["net_amount is built from silver.orders.net_amount, which breaks first",
                           "discount is built from gold.fact_sales.net_amount, which breaks first"]
    res = change_impact.column_impact(project, "silver.orders", "amount", "drop")
    assert items(res) == {("table", "gold.fact_sales", "breaks"), ("table", "silver.orders", "breaks")}
    res = change_impact.column_impact(project, "silver.orders", "net_amount", "rename", "net")
    assert items(res) == {("table", "gold.fact_sales", "breaks")}
    for res in (change_impact.column_impact(project, "silver.orders", c, ch, "n", "string")
                for c in lineage.entity_columns(project, "silver.orders") for ch in change_impact.CHANGES):
        assert not {n for n, _ in tables(res)} & {"bronze.orders", "bronze.customers"}, res  # never upstream
