"""proof_spark paths that mutation testing (batch 20, docs/quality/mutation-testing.md) showed no test
checked: names on a lakehouse without schemas, file: and Tables/ paths, reading a pinned version, the
DROP VIEW rule, and what the patched readers hand back."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop.proof_spark import ProofRefused, ProofSpark
from test_prove_spark import ROOT, SHARED, spark_proof
from test_prove_spark_install import TABLE, spark  # noqa: F401 - the fixture


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# ---- names and paths


def test_dbo_names_a_table_on_a_lakehouse_without_schemas_and_other_schemas_are_refused():
    ps, _ = spark_proof(flat=True)
    assert ps.parts(["dbo", "orders"]) == ("dbo", "orders")
    assert ps.parts(["DBO", "orders"]) == ("DBO", "orders")
    with pytest.raises(ProofRefused, match="isn't a table of the attached lakehouse"):
        ps.parts(["sales", "orders"])


def test_a_file_uri_without_a_slash_is_the_mount_too():
    ps, _ = spark_proof()
    assert ps.lakehouse_path("file:lakehouse/default/Tables/dbo/orders") == ("table", "dbo", "orders")
    assert ps.lakehouse_path("file:/lakehouse/default/Tables/dbo/orders") == ("table", "dbo", "orders")


def test_a_tables_folder_is_one_name_without_schemas_and_two_with_them():
    flat, _ = spark_proof(flat=True)
    assert flat.lakehouse_path(f"{ROOT}/Tables/orders") == ("table", "dbo", "orders")
    with pytest.raises(ProofRefused, match="isn't a table's folder"):
        flat.lakehouse_path(f"{ROOT}/Tables/dbo/orders")
    schemas, _ = spark_proof()
    assert schemas.lakehouse_path(f"{ROOT}/Tables/dbo/orders") == ("table", "dbo", "orders")
    assert schemas.lakehouse_path(f"{ROOT}/Tables/orders") == ("table", "dbo", "orders")
    with pytest.raises(ProofRefused, match="isn't a table's folder"):
        schemas.lakehouse_path(f"{ROOT}/Tables/a/b/c")


# ---- the proof's state


def test_a_table_pinned_without_a_version_still_exists():
    ps, _ = spark_proof(pins={"dbo.orders": None})
    assert ps.exists("dbo", "orders")
    assert not ps.exists("dbo", "missing")


def test_a_frame_reads_the_pinned_version_and_a_copy_reads_its_latest():
    ps, fake = spark_proof()
    ps.frame("dbo", "orders")
    assert fake.calls[-1] == ("load", f"{SHARED}/orders", {"versionAsOf": 3})
    ps.rewrite("CREATE TABLE dbo.fresh AS SELECT 1 AS a")
    ps.frame("dbo", "fresh")
    assert fake.calls[-1][2] == {}


# ---- SQL


def test_dropping_a_view_the_notebook_didnt_make_is_refused():
    ps, _ = spark_proof()
    with pytest.raises(ProofRefused, match="doesn't drop"):
        ps.rewrite("DROP VIEW lasting")
    ps.views.add("mine")
    with pytest.raises(ProofRefused, match="doesn't drop"):
        ps.rewrite("DROP VIEW mine, lasting")
    assert ps.rewrite("DROP VIEW mine") == "DROP VIEW mine" and "mine" not in ps.views


def test_a_table_name_with_no_place_in_the_sql_is_refused():
    with pytest.raises(ProofRefused, match="couldn't find a table name's place"):
        ProofSpark._apply("SELECT 1", [(exp.Table(this=exp.Identifier(this="orders")), "x")])


# ---- once bound


def test_a_load_outside_the_lakehouse_hands_back_sparks_own_frame(spark):  # noqa: F811
    frame = spark.session.read.load("/tmp/elsewhere.parquet", format="parquet")
    assert isinstance(frame, spark.Frame)
    assert spark.calls[-1] == ("load", "/tmp/elsewhere.parquet", {})


def test_every_parquet_path_is_checked_but_only_the_first_of_the_others(spark):  # noqa: F811
    with pytest.raises(ProofRefused, match="read it with spark.read.table"):
        spark.session.read.parquet("/tmp/scratch.parquet", TABLE)
    # csv takes one path (or a list) and then options: the second argument is not a path.
    spark.session.read.csv("/tmp/scratch.csv", TABLE)
    assert spark.calls[-1] == ("read.csv", ("/tmp/scratch.csv", TABLE))
