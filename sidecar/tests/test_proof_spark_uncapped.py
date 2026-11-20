"""proof_spark paths that the uncapped mutation run (batch 190, docs/quality/mutation-testing.md) showed
no test checked. pyspark and delta are the stand-in modules of test_prove_spark_install, so nothing here
needs Spark."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import proof_spark
from test_prove_spark import SANDBOX, SHARED
from test_prove_spark_install import spark  # noqa: F401 - the fixture


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_the_file_runs_on_its_own_as_fabric_runs_it():
    # In Fabric this file is the first cell of the published copy, outside any package, so it must
    # not import the rest of Fabric Desktop when it runs.
    source = Path(proof_spark.__file__).read_text(encoding="utf-8")
    namespace: dict = {"__name__": "proof_spark_cell"}
    exec(compile(source, "proof_spark_cell", "exec"), namespace)  # noqa: S102 - the module's own source
    assert "ProofSpark" in namespace


def test_opening_a_pinned_table_copies_its_pinned_version_in_first(spark):
    spark.DeltaTable.forName(spark.session, "orders")
    assert ("reader.option", "versionAsOf", 3) in spark.calls
    assert ("load", f"{SHARED}/orders", {}) in spark.calls
    assert any(c[0] == "save" and c[1] == f"{SANDBOX}/orders" for c in spark.calls)


def test_a_writer_s_options_still_hand_back_the_writer(spark):
    writer = spark.Writer()
    assert writer.option("mergeSchema", "true") is writer
    assert writer.options(mergeSchema="true") is writer


def test_a_safe_command_runs_as_written(spark):
    assert spark.ps.rewrite("SHOW TABLES") == "SHOW TABLES"
    assert spark.ps.rewrite("EXPLAIN SELECT 1") == "EXPLAIN SELECT 1"


def test_a_table_opened_and_changed_since_stays_a_write(spark):
    import types
    spark.DeltaTable.forName(spark.session, "orders")
    spark.ps.orig["sql"] = lambda session, q: types.SimpleNamespace(collect=lambda: [{"version": 1}])
    spark.ps.finish()
    assert spark.ps.proof.log["writes"]["dbo.orders"] == ["open"]


def test_a_write_of_files_outside_onelake_hands_back_spark_s_answer(spark):
    spark.Writer.parquet = lambda self, path, *a, **k: ("written", path)
    spark.ps.install()  # wraps the writer above
    assert spark.Writer().parquet("/tmp/elsewhere/out") == ("written", "/tmp/elsewhere/out")


def test_making_a_temp_view_hands_back_spark_s_answer(spark):
    spark.Frame.createOrReplaceTempView = lambda self, name: ("view made", name)
    spark.ps.install()  # wraps the method above
    assert spark.Frame().createOrReplaceTempView("Recent") == ("view made", "Recent")
    assert "recent" in spark.ps.views


def test_the_lakehouse_s_own_name_is_dropped_only_before_a_full_table_name():
    from test_prove_spark import spark_proof
    schemas, _ = spark_proof()
    assert schemas.parts(["LH_Proof", "dbo", "orders"]) == ("dbo", "orders")
    assert schemas.parts(["LH_Proof", "orders"]) == ("LH_Proof", "orders")  # a schema called LH_Proof
    assert schemas.parts(["sales", "orders"]) == ("sales", "orders")
    flat, _ = spark_proof(flat=True)
    assert flat.parts(["lh_proof", "orders"]) == ("dbo", "orders")


def test_insert_overwrite_and_insert_into_are_kept_as_their_modes(spark):
    spark.ps.rewrite("INSERT OVERWRITE dbo.orders SELECT 1 AS a")
    spark.ps.rewrite("INSERT INTO dbo.orders SELECT 2 AS a")
    assert spark.ps.proof.log["writes"]["dbo.orders"] == ["overwrite", "append"]


def test_a_path_under_the_proof_s_own_mount_is_the_lakehouse(spark):
    spark.ps.proof.mount = "/mnt/proof"
    assert spark.ps.lakehouse_path("/mnt/proof/Tables/dbo/orders") == ("table", "dbo", "orders")
    assert spark.ps.lakehouse_path("/lakehouse/default/Tables/dbo/orders") is None  # not this proof's mount


def test_without_a_mount_fabric_s_default_mount_is_the_lakehouse():
    from test_prove_spark import spark_proof
    ps, _ = spark_proof()
    ps.proof.mount = None
    assert ps.lakehouse_path("/lakehouse/default/Tables/dbo/orders") == ("table", "dbo", "orders")


def test_a_table_s_folder_read_as_csv_is_refused(spark):
    from test_prove_spark_install import TABLE
    with pytest.raises(proof_spark.ProofRefused, match="is a table's folder"):
        spark.session.read.csv(TABLE)
