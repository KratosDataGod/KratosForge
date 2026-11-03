"""DBA-004: Databricks SQL exercises on this PC (the dbsql_lab adapter). The learner's Databricks SQL is
translated by the DuckDB engine's own Databricks path and checked like a DuckDB lab answer; a table an
answer writes is compared after one run and after two; hidden data catches hard-coded answers; and an
exercise's reference solution may use only functions the parity table shows answer the same here."""

from __future__ import annotations

import json
import shutil

import duckdb
import pytest

from fabric_desktop.academy import dbsql_lab, exercises, lab, registry
from fabric_desktop.learn import checks

PACK = "dbx-de-associate"
EXERCISES = ("E20", "E25")


@pytest.mark.parametrize("eid", EXERCISES)
def test_the_reference_passes_and_every_planted_wrong_answer_fails(eid):
    assert exercises.problems() == []
    result = exercises.self_test(PACK, eid)
    assert result["solution"]["passed"], result["solution"]
    assert len(result["wrong"]) >= 2
    for name, r in result["wrong"].items():
        assert not r["passed"], f"{eid} {name} passed"


def test_insert_into_is_right_once_and_wrong_on_the_rerun():
    r = exercises.self_test(PACK, "E20")["wrong"]["wrong-insert-into.sql"]
    failed = [x for x in r["results"] if not x["passed"]]
    assert [x["kind"] for x in failed] == ["idempotent"]
    assert "twice" in failed[0]["detail"] and "20 rows" in failed[0]["detail"]
    assert "CREATE OR REPLACE TABLE" in failed[0]["message"]


def test_a_hard_coded_answer_passes_the_practice_rows_and_fails_the_hidden_ones():
    ex, ref = exercises.get(PACK, "E25"), dbsql_lab.reference(PACK, "E25")
    con = dbsql_lab.connect(ref["setup"])
    try:
        rows = con.execute(dbsql_lab.translate(ref["solution"])).fetchall()
    finally:
        con.close()
    def lit(v):
        return "NULL" if v is None else (f"DATE '{v}'" if hasattr(v, "isoformat") else str(v))
    values = ", ".join("(" + ", ".join(lit(v) for v in r) + ")" for r in rows)
    answer = (f"SELECT * FROM VALUES {values} AS t(customer_id, order_id, order_date, amount, running_total, "
              "previous_amount, amount_rank)")
    by_data = {c.get("data"): r["passed"] for c, r in zip(ex["validation"], dbsql_lab.grade(ex, ref, answer)["results"], strict=True)}
    assert by_data == {"practice": True, "hidden": False}


def test_sql_that_cant_be_translated_fails_every_check_without_running():
    ex, ref = exercises.get(PACK, "E20"), dbsql_lab.reference(PACK, "E20")
    r = dbsql_lab.grade(ex, ref, "SELECT (")
    assert not r["passed"] and len(r["results"]) == len(ex["validation"])
    assert all("can't run on this PC" in x["detail"] for x in r["results"])


def test_translation_is_the_engines_databricks_path():
    duck = dbsql_lab.translate("SELECT substring('Spark SQL', 5), to_date('03/01/2026', 'MM/dd/yyyy')")
    assert "STRPTIME" in duck
    con = duckdb.connect()
    assert con.execute(duck).fetchone()[0] == "k SQL"   # Databricks counts from 1, as DuckDB's SUBSTRING does here
    with pytest.raises(ValueError, match="can't run on this PC"):
        dbsql_lab.translate("SELECT (")


def test_only_calls_count_as_functions_and_unproven_ones_are_named():
    text = ("SELECT CASE WHEN a LIKE 'x%' THEN to_date(a, 'MM/dd/yyyy') END, CAST(b AS INT), sum(c), "
            "row_number() OVER (ORDER BY d), made_up_fn(e) FROM t WHERE f BETWEEN 1 AND 2")
    assert dbsql_lab.functions(text) == {"to_date", "cast", "sum", "row_number", "made_up_fn"}
    assert dbsql_lab.unproven(text) == ["made_up_fn"]
    assert {"to_date", "lag", "dense_rank", "row_number", "coalesce", "nullif", "trim"} <= dbsql_lab.PROVEN


def test_translated_sql_runs_in_utc_with_files_and_settings_locked():
    con = dbsql_lab.connect("CREATE TABLE t AS SELECT 1 AS x")
    try:
        assert con.execute("SELECT current_setting('TimeZone')").fetchone()[0] == "UTC"
        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM read_csv('/etc/hosts')")
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")
    finally:
        con.close()


def test_table_shape_reads_a_schema_qualified_table():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA silver; CREATE TABLE silver.orders (id INTEGER, name VARCHAR); "
                "INSERT INTO silver.orders VALUES (1, 'a'), (1, NULL); CREATE TABLE orders (other INTEGER)")
    assert "no column id" in checks.table_shape(con, "main.orders", {"id": "INTEGER"})["detail"]
    assert checks.table_shape(con, "silver.orders", {"id": "INTEGER"})["passed"]
    assert "NULL" in checks.table_shape(con, "silver.orders", not_null=["name"])["detail"]
    assert "more than once" in checks.table_shape(con, "silver.orders", unique=["id"])["detail"]
    assert "no table" in checks.table_shape(con, "gold.orders")["detail"]


def test_the_workspace_runs_and_validates_databricks_sql(tmp_path):
    st = lab.start(tmp_path, PACK, "E20")
    assert st["started"] and "CREATE OR REPLACE" not in st["answer"]
    shown = lab.run(tmp_path, PACK, "E20", "SELECT trim(order_id) AS id FROM bronze.orders WHERE order_id IS NOT NULL "
                                           "ORDER BY id LIMIT 2")
    assert shown["columns"] == ["id"] and shown["rows"] == [["1001"], ["1002"]]
    with pytest.raises(ValueError, match="can't run on this PC"):
        lab.run(tmp_path, PACK, "E20", "SELECT (")
    solution = exercises.reference(PACK, "E20")["solution"]
    assert lab.validate(tmp_path, PACK, "E20", solution)["passed"]
    wrong = dbsql_lab.reference(PACK, "E20")["wrong"]["wrong-insert-into.sql"]
    result = lab.validate(tmp_path, PACK, "E20", wrong)
    assert not result["passed"] and result["verification"]["label"] == "LOCAL VERIFIED"
    assert not (tmp_path / "academy" / PACK / "E20" / "solution.sql").exists()
    with duckdb.connect(str(tmp_path / "academy" / PACK / "E20" / lab.DB_FILE)) as con:
        # runs and checks are rolled back: the workspace still has no silver table
        assert con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'silver'").fetchone()[0] == 0


def test_a_transaction_in_the_answer_is_refused():
    ex, ref = exercises.get(PACK, "E20"), dbsql_lab.reference(PACK, "E20")
    r = dbsql_lab.grade(ex, ref, "BEGIN; " + ref["solution"] + " COMMIT;")
    assert not r["passed"]


@pytest.fixture
def packs(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    return root


def _edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_reference_solution_may_use_only_proven_functions(packs):
    sol = packs / PACK / "grading" / "E25" / "solution.sql"
    sol.write_text(sol.read_text(encoding="utf-8").replace("dense_rank()", "made_up_rank()"), encoding="utf-8")
    assert any("E25: the reference solution uses made_up_rank" in p for p in exercises.problems(packs))


def test_check_rules_for_sql_adapters(packs):
    ex = packs / PACK / "exercises" / "E20" / "exercise.json"
    _edit(ex, lambda d: d["validation"][3].pop("table"))
    _edit(ex, lambda d: d["validation"][0].update(data="staging"))
    found = exercises.problems(packs)
    assert any("E20: check idempotent needs table" in p for p in found)
    assert any("E20: check result_matches's data is practice or hidden" in p for p in found)
    (packs / PACK / "grading" / "E25" / "hidden.sql").unlink()
    assert any("E25: a check on hidden data needs grading/E25/hidden.sql" in p for p in exercises.problems(packs))
    dp = next((packs / "dp-700" / "exercises").glob("E21")) / "exercise.json"
    _edit(dp, lambda d: d["validation"][0].update(table="silver_orders"))
    assert any("a duckdb_lab check reads the answer's last result" in p for p in exercises.problems(packs))
