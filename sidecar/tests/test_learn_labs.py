"""LX-03: Learn datasets are the same every time, each check passes and fails for the right reasons,
and a lab runs on this PC only: no file or network access, answers checked without changing the lab,
reset back to the start, and a lab folder never outside <data>/learn."""

from __future__ import annotations

import ast
from pathlib import Path

import duckdb
import pytest

from fabric_desktop.learn import checks, content, datasets, labs

LEARN = Path(labs.__file__).resolve().parent


# Datasets ----------------------------------------------------------------------------------------

def test_same_seed_same_data_other_seed_other_data():
    a, b, c = duckdb.connect(), duckdb.connect(), duckdb.connect()
    ma, mb = datasets.generate(a, seed=7), datasets.generate(b, seed=7)
    datasets.generate(c, seed=8)
    assert ma == mb
    assert datasets.fingerprint(a) == datasets.fingerprint(b) != datasets.fingerprint(c)


def test_sizes_and_unknown_names():
    con = duckdb.connect()
    assert datasets.generate(con, size="M")["tables"]["orders"] == datasets.SIZES["M"]
    with pytest.raises(ValueError):
        datasets.generate(con, name="payroll")
    with pytest.raises(ValueError):
        datasets.generate(con, size="XL")


def test_every_recorded_defect_is_really_there():
    con = duckdb.connect()
    found = {d["kind"]: d["key"] for d in datasets.generate(con, seed=3)["defects"]}
    assert set(found) == set(datasets.DEFECT_KINDS)
    one = lambda sql, key: con.execute(sql, [key]).fetchall()[0][0]  # noqa: E731
    assert one("SELECT count(*) FROM customers WHERE customer_id = ?", found["duplicate_customer"]) == 2
    assert one("SELECT email IS NULL FROM customers WHERE customer_id = ?", found["missing_email"])
    assert one("SELECT region = lower(region) FROM customers WHERE customer_id = ?", found["region_case"])
    assert one("SELECT amount < 0 FROM orders WHERE order_id = ?", found["negative_amount"])
    assert one("SELECT order_date > DATE '2030-01-01' FROM orders WHERE order_id = ?", found["future_date"])
    assert one("SELECT store_id NOT IN (SELECT store_id FROM stores) FROM orders WHERE order_id = ?", found["orphan_store"])
    clean = duckdb.connect()
    assert datasets.generate(clean, defects=False)["defects"] == []
    assert clean.execute("SELECT count(*) FROM orders WHERE amount < 0").fetchall()[0][0] == 0


# Checks ------------------------------------------------------------------------------------------

@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a', 2.50), (2, 'b', 3.00)) v(id, name, amount)")
    return c


def test_result_matches(con):
    ref = "SELECT id, name FROM t ORDER BY id"
    assert checks.result_matches(con, "SELECT id, name FROM t ORDER BY id DESC", reference=ref)["passed"]
    assert not checks.result_matches(con, "SELECT id, name FROM t ORDER BY id DESC", reference=ref, ordered=True)["passed"]
    assert "columns" in checks.result_matches(con, "SELECT id FROM t", reference=ref)["detail"]
    assert "1 rows" in checks.result_matches(con, "SELECT id, name FROM t LIMIT 1", reference=ref)["detail"]
    assert "didn't run" in checks.result_matches(con, "SELEC 1", reference=ref)["detail"]
    exp = {"columns": ["amount"], "rows": [[2.5], [3.0]]}
    assert checks.result_matches(con, "SELECT amount FROM t", expected=exp)["passed"], "decimals match floats"
    assert not checks.result_matches(con, "SELECT 1 AS x")["passed"], "no reference never passes"


def test_the_answer_never_changes_the_data(con):
    checks.result_matches(con, "DELETE FROM t; SELECT 1 AS id, 'a' AS name", reference="SELECT 1 AS id, 'a' AS name")
    assert con.execute("SELECT count(*) FROM t").fetchall()[0][0] == 2


def test_table_shape_and_integrity(con):
    assert checks.table_shape(con, "t", {"id": "INTEGER", "name": "VARCHAR"}, unique=["id"], not_null=["name"])["passed"]
    assert "no table" in checks.table_shape(con, "nope")["detail"]
    assert "no column" in checks.table_shape(con, "t", {"price": ""})["detail"]
    assert "should be" in checks.table_shape(con, "t", {"id": "VARCHAR"})["detail"]
    con.execute("INSERT INTO t VALUES (2, NULL, 1)")
    assert "NULL" in checks.table_shape(con, "t", not_null=["name"])["detail"]
    assert "more than once" in checks.table_shape(con, "t", unique=["id"])["detail"]
    con.execute("CREATE TABLE child AS SELECT * FROM (VALUES (1), (2), (9), (NULL)) v(id)")
    assert "1 rows of child" in checks.integrity(con, "child", "id", "t")["detail"]
    con.execute("DELETE FROM child WHERE id = 9")
    assert checks.integrity(con, "child", "id", "t")["passed"]


def test_no_findings():
    assert checks.no_findings("SELECT a FROM dbo.t WHERE a >= 1", ["SQ002"])["passed"]
    out = checks.no_findings("SELECT a FROM dbo.t WHERE YEAR(d) = 2026", ["SQ002"])
    assert not out["passed"] and out["detail"].startswith("SQ002")
    assert not checks.no_findings("try:\n    x()\nexcept Exception:\n    pass\n", ["NB012"], language="python")["passed"]


def _clock(baseline, mine, runs: int = 3):
    """A fake clock for runs that take turns, baseline first. ``baseline`` and ``mine`` are seconds
    per run: one number for every run, or a list with one per run."""
    base = baseline if isinstance(baseline, list) else [baseline] * runs
    own = mine if isinstance(mine, list) else [mine] * runs
    times, t = [], 0.0
    for b, m in zip(base, own):
        for d in (b, m):
            times += [t, t + d]
            t += d
    return iter(times).__next__


def test_faster_than_needs_the_same_answer_and_the_factor(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2, runs=3, clock=_clock(3.0, 1.0))
    assert out["passed"] and "3.0×" in out["detail"] and "this PC" in out["detail"]
    slow = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2, runs=3, clock=_clock(1.5, 1.0))
    assert not slow["passed"] and "The goal is 2×" in slow["detail"]
    wrong = checks.faster_than(con, "SELECT 1 AS id", "SELECT id FROM t", factor=2, clock=_clock(9.0, 1.0))
    assert not wrong["passed"] and "same answer" in wrong["detail"]


def test_faster_than_is_not_failed_by_a_busy_pc(con):
    # The PC was busy for most of the learner's runs (LAB-02 measured 1.83× on a shared Windows CI
    # runner): each query's fastest run is compared, so pauses that only add time don't count.
    busy = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2, runs=5,
                              clock=_clock([3.0, 3.1, 3.0, 3.2, 3.0], [2.5, 1.0, 2.6, 2.4, 1.1], runs=5))
    assert busy["passed"], busy["detail"]
    assert busy["measured"] == {"seconds": 1.0, "baseline_seconds": 3.0, "speedup": 3.0}
    # A query that is never twice as fast still fails, however its runs vary.
    close = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2, runs=5,
                               clock=_clock([3.0, 3.5, 3.0, 4.0, 3.0], [1.6, 2.5, 1.7, 1.6, 3.0], runs=5))
    assert not close["passed"] and close["measured"]["speedup"] == 1.88


def test_defects_found(con):
    defects = [{"id": "D1", "kind": "negative_amount", "table": "orders", "key": 5},
               {"id": "D2", "kind": "missing_email", "table": "customers", "key": 9}]
    assert checks.defects_found(con, "SELECT 'orders', 5 UNION ALL SELECT 'customers', 9", defects)["passed"]
    out = checks.defects_found(con, "SELECT 'orders', 5", defects)
    assert not out["passed"] and "1 of the 2" in out["detail"] and "customers" in out["detail"]
    assert checks.defects_found(con, "SELECT 'orders', 5", defects, kinds=["negative_amount"])["passed"]


# Labs --------------------------------------------------------------------------------------------

def test_lab_folder_is_always_under_learn(tmp_path):
    assert labs.lab_dir(tmp_path, "LM-01") == (tmp_path / "learn" / "LM-01").resolve()
    for bad in ("../LM-01", "LM-01/..", "learn", "", "LM-1", "C:\\x"):
        with pytest.raises(ValueError):
            labs.lab_dir(tmp_path, bad)


def test_start_check_hint_and_reset(tmp_path):
    with pytest.raises(ValueError):
        labs.check(tmp_path, "LM-01", "north-big-orders", "SELECT 1")
    st = labs.start(tmp_path, "LM-01")
    assert st["started"] and st["answers"]["north-big-orders"] == content.file_text("LM-01", "starter.sql")
    assert not labs.check(tmp_path, "LM-01", "north-big-orders")["passed"], "the starter isn't the answer"
    labs.save_answer(tmp_path, "LM-01", "north-big-orders", content.file_text("LM-01", "solution.sql"))
    assert labs.check(tmp_path, "LM-01", "north-big-orders")["passed"]
    assert labs.start(tmp_path, "LM-01")["answers"]["north-big-orders"].startswith("SELECT order_id"), "start keeps work"
    labs.reset(tmp_path, "LM-01")
    assert labs.state(tmp_path, "LM-01")["answers"]["north-big-orders"] == content.file_text("LM-01", "starter.sql")
    assert labs.hint("LAB-02", "store-revenue-2026", 1)["of"] == 3
    assert labs.hint("LAB-02", "store-revenue-2026", 99)["level"] == 3
    assert labs.hint("LM-01", "north-big-orders", 1)["of"] == 3
    with pytest.raises(ValueError):
        labs.save_answer(tmp_path, "LM-01", "nope", "x")


def test_a_lab_answer_cant_reach_files_or_change_the_lab(tmp_path):
    labs.start(tmp_path, "LM-01")
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n", encoding="utf-8")
    for sql in (f"SELECT * FROM read_csv('{secret.as_posix()}')", f"ATTACH '{(tmp_path / 'x.db').as_posix()}' AS x",
                f"SET enable_external_access = true; SELECT * FROM read_csv('{secret.as_posix()}')",
                f"COPY (SELECT 1) TO '{(tmp_path / 'out.csv').as_posix()}'"):
        out = labs.check(tmp_path, "LM-01", "north-big-orders", sql)
        assert not out["passed"], sql
    assert not (tmp_path / "out.csv").exists() and not (tmp_path / "x.db").exists()
    labs.check(tmp_path, "LM-01", "north-big-orders", "DROP TABLE orders; SELECT 1 AS x")
    for sql in ("COMMIT; DROP TABLE orders; SELECT 1 AS x", "ROLLBACK; DELETE FROM orders; SELECT 1 AS x",
                "begin transaction; DELETE FROM orders; commit; SELECT 1 AS x"):
        out = labs.check(tmp_path, "LM-01", "north-big-orders", sql)
        assert not out["passed"] and "BEGIN, COMMIT or ROLLBACK" in out["results"][0]["detail"], sql
    labs.save_answer(tmp_path, "LM-01", "north-big-orders", content.file_text("LM-01", "solution.sql"))
    assert labs.check(tmp_path, "LM-01", "north-big-orders")["passed"], "the DROP was rolled back"


@pytest.mark.parametrize("module_id", [m["id"] for m in content.modules() if "duckdb" in m["engines"]])
def test_every_reference_solution_passes_its_own_checks(tmp_path, module_id):
    module = content.get(module_id)
    labs.start(tmp_path, module_id)
    for ex in module["exercises"]:
        out = labs.check(tmp_path, module_id, ex["id"], content.file_text(module_id, ex["solution"]))
        assert out["passed"], out["results"]


@pytest.mark.parametrize("module_id", [m["id"] for m in content.modules() if "duckdb" in m["engines"]])
def test_no_starter_already_passes(tmp_path, module_id):
    module = content.get(module_id)
    labs.start(tmp_path, module_id)
    for ex in module["exercises"]:
        if ex.get("starter"):
            out = labs.check(tmp_path, module_id, ex["id"], content.file_text(module_id, ex["starter"]))
            assert not out["passed"], f"{module_id}/{ex['id']}: the starter is already the answer"


def test_labs_import_nothing_that_reaches_fabric():
    allowed = {"__future__", "contextlib", "datetime", "decimal", "threading", "hashlib", "json", "re", "shutil", "statistics", "time", "collections",
               "collections.abc", "pathlib", "typing", "duckdb", "jsonfile", "notebook_practices",
               "sqlcompat", "model_practices", "datasets", "checks", "content", "guides", "sys", "",
               # fixes.py (GD-02) reads SQL with the compatibility checker's tokens: a parser, no I/O
               "sqlglot.tokens", "sqlcompat.analyzer", "sqlcompat.profiles", "sqlcompat.rules",
               "duckdb_rows"}  # one-row reads from DuckDB (#1341): no I/O of its own
    for path in LEARN.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + ([a.name for a in node.names] if not node.module else [])
            for n in names:
                assert n in allowed, f"{path.name} imports {n}"


def test_guard_refuses_only_transaction_statements(con):
    checks.guard(con, "CREATE TABLE x AS SELECT 1; SELECT * FROM x")
    checks.guard(con, "SELEC broken")   # left to fail when it runs
    for sql in ("COMMIT", "SELECT 1; ROLLBACK", "BEGIN; SELECT 1"):
        with pytest.raises(ValueError):
            checks.guard(con, sql)
