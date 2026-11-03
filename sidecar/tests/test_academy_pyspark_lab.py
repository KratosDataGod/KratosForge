"""DBA-003: a PySpark exercise end to end on this PC (the Databricks path's vertical slice): Delta tables
built from setup, the answer run with the generated kf_learner package, grading on the tables it leaves
(including rows it never saw and a rerun), and a pass that moves its objective in readiness.

The pack's own reference solution and planted wrong answers run in a plain ``python -I`` process; a
learner's answer runs only through the Academy learner process (run_learner, academy/sandbox.py), and
the tests here run it there too."""

from __future__ import annotations

import ast
import os
import subprocess
import sys

import deltalake
import pytest

from fabric_desktop import progress
from fabric_desktop.academy import exercises, learner_pkg, pyspark_lab, pyspark_shim, readiness, registry
from fabric_desktop.academy import workspace as ws

PACK = "dbx-de-associate"


@pytest.fixture(scope="module")
def e19():
    return exercises.self_test(PACK, "E19")


def test_the_reference_solution_passes_every_check(e19):
    sol = e19["solution"]
    assert sol["passed"] and sol["score"] == 1.0
    assert [r["kind"] for r in sol["results"]] == ["result_matches", "result_matches", "table_shape", "idempotent"]


@pytest.mark.parametrize(("wrong", "failing"), [
    ("wrong-append.py", {"idempotent"}),
    ("wrong-keeps-null-ids.py", {"result_matches", "table_shape"}),
    ("wrong-one-date-format.py", {"result_matches"}),
    ("wrong-blank-amount-zero.py", {"result_matches"}),
    ("wrong-status-not-trimmed.py", {"result_matches"}),
])
def test_each_planted_wrong_answer_fails_for_its_reason(e19, wrong, failing):
    res = e19["wrong"][wrong]
    assert not res["passed"] and 0 <= res["score"] < 1
    assert {r["kind"] for r in res["results"] if not r["passed"]} == failing
    for r in res["results"]:
        assert r["detail"].endswith(".") and "1001" not in r["detail"]   # explained, never the answer's rows


def test_hidden_rows_catch_an_answer_that_only_fits_the_practice_data(e19):
    res = e19["wrong"]["wrong-status-not-trimmed.py"]["results"]
    practice, hidden = res[0], res[1]
    assert practice["passed"] and not hidden["passed"] and "haven't seen" in hidden["detail"]


def test_a_rerun_that_appends_is_explained(e19):
    r = next(x for x in e19["wrong"]["wrong-append.py"]["results"] if x["kind"] == "idempotent")
    assert "10 rows, then 20" in r["detail"]


def test_a_pass_is_evidence_for_3_1_and_completes_it(e19):
    cert = registry.certification(PACK)
    live = {**exercises.get(PACK, "E19"), "status": "live"}
    row = progress.from_academy(e19["solution"], live, cert)
    assert row["detail"]["class"] == "local_verified" and row["detail"]["objectives"] == ["3.1"]
    profile = progress.empty()
    progress.add(profile, row)
    out = readiness.readiness(profile, PACK)
    x = next(o for o in out["objectives"] if o["id"] == "3.1")
    assert x["state"] == "shown" and x["measures"]["guided"] == [f"{PACK}/E19"] and not x["needs_platform"]
    assert next(d for d in out["domains"] if d["id"] == "3")["shown"] == 1
    # The same local class never completes an objective that needs Databricks.
    assert not exercises.completes(registry.objective(cert, "2.3"), "local_verified", "databricks")


def test_a_draft_exercise_leaves_no_evidence(e19):
    assert progress.from_academy(e19["solution"], exercises.get(PACK, "E19"), registry.certification(PACK)) is None


# ---- a learner's answer, in the Academy learner process (sandbox.py)

def _learner_grade(answer):
    ex, ref = exercises.get(PACK, "E19"), pyspark_lab.reference(PACK, "E19")
    return pyspark_lab.grade(ex, ref["setup"], ref["hidden"], answer, ref["solution"], pyspark_lab.run_learner)


def test_a_learners_answer_runs_in_the_learner_process_and_is_graded():
    ref = pyspark_lab.reference(PACK, "E19")
    assert _learner_grade(ref["solution"])["passed"]
    wrong = _learner_grade(ref["wrong"]["wrong-append.py"])
    assert [r["kind"] for r in wrong["results"] if not r["passed"]] == ["idempotent"]


@pytest.mark.parametrize("code", [
    "import fabric_desktop",
    "open('../../x.txt', 'w').write('x')",
    "import socket; socket.create_connection(('example.com', 443))",
    "import subprocess; subprocess.run(['whoami'])",
])
def test_what_the_learner_process_refuses_fails_the_answer(tmp_path, code):
    work = tmp_path / "data" / "academy" / "w"
    work.mkdir(parents=True)
    pyspark_lab.prepare(work, exercises.file_text(PACK, "E19", "setup.sql"))
    run = pyspark_lab.run_learner(work, code, data_root=tmp_path / "data")
    assert run["status"] != "ok"
    assert not (tmp_path / "x.txt").exists() and not (tmp_path / "data" / "x.txt").exists()


def test_an_answer_file_the_last_run_made_a_link_is_refused(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (tmp_path / "elsewhere.py").write_text("", encoding="utf-8")
    try:
        (work / pyspark_lab.ANSWER_FILE).symlink_to(tmp_path / "elsewhere.py")
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused):
        pyspark_lab.run_learner(work, "print('hi')")
    assert (tmp_path / "elsewhere.py").read_text(encoding="utf-8") == ""


def test_the_result_label_is_honest():
    lab = exercises.label(exercises.get(PACK, "E19"))
    assert lab["label"] == "LOCAL VERIFIED" and "not Apache Spark" in lab["engine"]
    assert any("Databricks workspace" in s for s in lab["does_not_verify"])


# ---- the learner package (Security read, 2026-11-03)

def test_the_package_is_its_sources_with_only_the_shims_parent_imports_changed(tmp_path):
    learner_pkg.build(tmp_path)
    pkg = tmp_path / learner_pkg.PACKAGE
    assert sorted(p.name for p in pkg.glob("*.py")) == sorted(["__init__.py", *learner_pkg.SOURCES])
    for name, src in learner_pkg.SOURCES.items():
        built, original = (pkg / name).read_text(encoding="utf-8"), src.read_text(encoding="utf-8")
        if name == "pyspark_shim.py":
            assert "from .." not in built and built.count("\n") == original.count("\n")
        else:
            assert built == original


def test_the_packages_files_import_nothing_a_learner_shouldnt_have():
    assert learner_pkg.import_problems() == []


def test_a_new_engine_import_would_be_caught(monkeypatch, tmp_path):
    bad = tmp_path / "sql_dialects.py"
    bad.write_text("from . import onelake\nimport msal\n", encoding="utf-8")
    monkeypatch.setitem(learner_pkg.SOURCES, "sql_dialects.py", bad)
    found = learner_pkg.import_problems()
    assert any("onelake" in p for p in found) and any("msal" in p for p in found)


def test_spark_compat_imports_only_duckdb_sqlglot_and_the_standard_library():
    tree = ast.parse(learner_pkg.SOURCES["spark_compat.py"].read_text(encoding="utf-8"))
    roots = {(n.module or "").split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and not n.level}
    roots |= {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    assert roots - set(sys.stdlib_module_names) - {"__future__"} == {"duckdb", "sqlglot"}


PROBE = """
import os, sys
sys.path.insert(0, sys.argv[1])
from kf_learner import pyspark_shim
spark = pyspark_shim.session(sys.argv[2])
from pyspark.sql import functions as F
df = spark.read.table("bronze.t")
(df.withColumn("d", F.to_date(F.col("s"), "MM/dd/yyyy")).withColumn("n", F.regexp_replace("a", ",", "").cast("decimal(10,2)"))
   .dropDuplicates(["k"]).write.mode("overwrite").saveAsTable("silver.t"))
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("fabric_desktop", "msal", "requests", "azure")
             or any(k in m for k in ("auth", "connprofiles", "onelake", "fabric_api", "lakehouse")))
blocked = []
for name in ("fabric_desktop", "msal"):
    try:
        __import__(name)
        blocked.append(name)
    except ImportError:
        pass
print(repr((bad, blocked)))
sys.stdout.flush(); os._exit(0)
"""


def test_a_learner_like_process_loads_no_engine_or_sign_in_code(tmp_path):
    """Run the shim's functions once in a ``python -I`` process whose path has kf_learner but not the
    engine, then look at what it loaded."""
    work = tmp_path / "work"
    work.mkdir()
    pyspark_lab.prepare(work, "CREATE SCHEMA bronze; CREATE TABLE bronze.t AS SELECT 1 AS k, '03/01/2026' AS s, '1,000.50' AS a")
    pkg = learner_pkg.build(tmp_path / "pkg")
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP", "PATH")}
    out = subprocess.run([sys.executable, "-I", "-c", PROBE, str(pkg), str(work)], cwd=work, env=env,
                         capture_output=True, text=True, timeout=120, check=True).stdout
    bad, _importable = eval(out.strip().splitlines()[-1])  # noqa: S307 - our own probe's repr
    # Nothing from the engine or sign-in was loaded. (Whether fabric_desktop can be imported at all is the
    # sandbox's import guard, tested in PR-F; this interpreter has the engine installed for the test run.)
    assert bad == []
    assert deltalake.DeltaTable(str(pyspark_shim.table_path(work, "silver.t"))).to_pyarrow_table().num_rows == 1


# ---- the shim's table names

@pytest.mark.parametrize("name", ["../x", "a.b.c", "x;drop", "", "silver/orders", "1abc"])
def test_table_names_that_could_leave_the_tables_folder_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        pyspark_shim.table_path(tmp_path, name)


def test_a_table_reached_through_a_link_is_refused(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "tables").mkdir()
    try:
        (tmp_path / "tables" / "silver").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ValueError, match="link"):
        pyspark_shim.table_path(tmp_path, "silver.orders")


def test_prepare_writes_one_delta_table_per_setup_table(tmp_path):
    names = pyspark_lab.prepare(tmp_path, exercises.file_text(PACK, "E19", "setup.sql"))
    assert names == ["bronze.orders"]
    assert deltalake.DeltaTable(str(pyspark_shim.table_path(tmp_path, "bronze.orders"))).to_pyarrow_table().num_rows == 13


def test_the_session_sets_nothing_after_it_connects(monkeypatch, tmp_path):
    """The learner process locks each DuckDB connection's settings as it opens (academy/sandbox.py), so
    the session's settings go in at connect time and a table is read without a pyarrow file system."""
    import duckdb

    from fabric_desktop.academy import pyspark_shim
    connect = duckdb.connect

    def locked(database=":memory:", *args, **kwargs):
        con = connect(database, *args, **kwargs)
        con.execute("SET lock_configuration = true")
        return con

    pyspark_lab.prepare(tmp_path, "CREATE SCHEMA bronze; CREATE TABLE bronze.t AS SELECT 1 AS x")
    monkeypatch.setattr(duckdb, "connect", locked)
    spark = pyspark_shim.AcademySession(tmp_path)
    assert spark.read.table("bronze.t").collect()[0][0] == 1
    assert spark.conn.execute("SELECT current_setting('default_null_order')").fetchone()[0].lower() == "nulls_first_on_asc_last_on_desc"


# ---- reading what the answer left: checked before the engine opens it

def _bronze(tmp_path):
    pyspark_lab.prepare(tmp_path, exercises.file_text(PACK, "E19", "setup.sql"))
    return pyspark_shim.table_path(tmp_path, "bronze.orders")


def test_an_unwritten_table_reads_as_none(tmp_path):
    _bronze(tmp_path)
    assert pyspark_lab._rows(tmp_path, "silver.orders") is None
    assert pyspark_lab._rows(tmp_path, "bronze.orders")[1]


def test_a_log_entry_pointing_outside_the_table_is_refused(tmp_path):
    table = _bronze(tmp_path)
    commit = sorted((table / "_delta_log").glob("*.json"))[-1]
    with open(commit, "a", encoding="utf-8") as fh:
        fh.write('\n{"add": {"path": "/etc/hosts", "size": 1, "modificationTime": 0, "dataChange": true, '
                 '"partitionValues": {}}}\n')
    with pytest.raises(ws.Refused, match="outside the table"):
        pyspark_lab._rows(tmp_path, "bronze.orders")


def test_a_checkpoint_is_refused(tmp_path):
    table = _bronze(tmp_path)
    (table / "_delta_log" / "00000000000000000000.checkpoint.parquet").write_bytes(b"x")
    with pytest.raises(ws.Refused, match="checkpoint"):
        pyspark_lab._rows(tmp_path, "bronze.orders")


def test_a_data_file_that_is_a_link_is_refused(tmp_path):
    table = _bronze(tmp_path)
    data = next(table.glob("*.parquet"))
    outside = tmp_path / "outside.parquet"
    data.rename(outside)
    try:
        data.symlink_to(outside)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused):
        pyspark_lab._rows(tmp_path, "bronze.orders")


def test_a_delta_log_that_is_a_link_is_refused(tmp_path):
    table = _bronze(tmp_path)
    (table / "_delta_log").rename(tmp_path / "log")
    try:
        (table / "_delta_log").symlink_to(tmp_path / "log", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused):
        pyspark_lab._rows(tmp_path, "bronze.orders")


def test_a_refused_table_fails_its_checks_in_words(tmp_path):
    ex = exercises.get(PACK, "E19")
    ref = pyspark_lab.reference(PACK, "E19")
    plain = pyspark_lab.run_reference

    def tampering(work, code):
        out = plain(work, code)
        table = pyspark_shim.table_path(work, "silver.orders")
        if (table / "_delta_log").is_dir():
            (table / "_delta_log" / "00000000000000000000.checkpoint.parquet").write_bytes(b"x")
        return out

    result = pyspark_lab.grade(ex, ref["setup"], ref["hidden"], ref["solution"], ref["solution"],
                               lambda work, code: tampering(work, code) if work.name == "answer" else plain(work, code))
    assert not result["passed"]
    assert any("checkpoint" in r["detail"] for r in result["results"])
