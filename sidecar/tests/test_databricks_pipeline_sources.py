"""WR-322: a bundle's Lakeflow pipelines checked on this PC. The pipeline settings the properties and
Pipelines API create pages name (edition, channel, serverless with clusters, tags, target, glob), and the
datasets and flows each pipeline's own .sql and .py files define, read and never run
(engines/databricks_pipeline_sources.py). The SQL and Python are written from the examples of the
CREATE STREAMING TABLE, CREATE FLOW and Python reference pages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import cli
from fabric_desktop.engines import databricks_pipeline_sources as ps
from fabric_desktop.engines.databricks_bundle_check import ERROR, WARNING, Finding, pipelines, validate


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def bundle(settings: str = "", libraries: str = "        - glob: {include: ./transformations/**}\n") -> str:
    return ("bundle:\n  name: x\nresources:\n  pipelines:\n    p:\n      name: p\n      catalog: main\n"
            "      schema: sales\n" + settings + ("      libraries:\n" + libraries if libraries else ""))


def messages(findings: list[Finding], severity: str | None = None) -> list[str]:
    return [f.message for f in findings if severity is None or f.severity == severity]


BRONZE = """-- Databricks notebook source
CREATE OR REFRESH STREAMING TABLE customers_bronze
AS SELECT * FROM STREAM read_files('/Volumes/main/raw/customers', format => 'json');
"""
SILVER_SQL = """CREATE OR REFRESH MATERIALIZED VIEW customers_summary AS
SELECT country, count(*) AS n FROM customers_silver GROUP BY country;
"""
SILVER_PY = '''from pyspark import pipelines as dp


@dp.table()
def customers_silver():
    return spark.readStream.table("customers_bronze").where("id IS NOT NULL")
'''


def test_a_good_pipeline_has_no_findings_and_lists_its_graph(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/bronze.sql": BRONZE,
                     "transformations/silver.py": SILVER_PY, "transformations/summary.sql": SILVER_SQL,
                     "transformations/README.md": "notes"})
    assert validate(tmp_path) == []
    graph = pipelines(tmp_path)["pipelines"]["resources.pipelines.p"]
    assert [(d["kind"], d["name"]) for d in graph["datasets"]] == [
        ("streaming table", "customers_bronze"), ("streaming table", "customers_silver"),
        ("materialized view", "customers_summary")]
    reads = {(r["name"], r["stream"], r["defined_here"]) for r in graph["reads"]}
    assert reads == {("customers_bronze", True, True), ("customers_silver", False, True)}
    assert graph["datasets"][0]["file"] == "transformations/bronze.sql" and graph["datasets"][0]["line"] == 2


def test_a_dataset_defined_twice_is_an_error_at_the_second(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/a.sql": BRONZE,
                     "transformations/b.sql": "CREATE MATERIALIZED VIEW `Customers_Bronze` AS SELECT 1;"})
    (f,) = validate(tmp_path)
    assert (f.severity, f.file, f.line, f.path) == (ERROR, "transformations/b.sql", 1,
                                                    "resources.pipelines.p.libraries")
    assert f.message.startswith("Customers_Bronze is defined again here (materialized view); it's already a "
                                "streaming table at transformations/a.sql:2.")


def test_flows_into_one_streaming_table_and_a_reused_flow_name(tmp_path):
    # CREATE FLOW's example: a streaming table, and two flows that append to it
    flows = """CREATE OR REFRESH STREAMING TABLE all_events;
CREATE FLOW east AS INSERT INTO all_events BY NAME SELECT * FROM STREAM(events_east);
CREATE FLOW west AS INSERT INTO all_events BY NAME SELECT * FROM STREAM(events_west);
"""
    write(tmp_path, {"databricks.yml": bundle(), "transformations/f.sql": flows})
    assert validate(tmp_path) == []
    write(tmp_path, {"transformations/g.sql": "CREATE FLOW west AS INSERT ONCE INTO all_events BY NAME "
                                              "SELECT * FROM backfill;"})
    (f,) = validate(tmp_path)
    assert f.file == "transformations/g.sql" and "The flow name west is used again here" in f.message


def test_a_flow_named_as_a_streaming_table_with_its_own_query_reuses_its_flow_name(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/a.sql": BRONZE + (
        "CREATE FLOW customers_bronze AS INSERT INTO customers_bronze BY NAME SELECT * FROM more;")})
    assert ["The flow name customers_bronze is used again" in m for m in messages(validate(tmp_path))] == [True]


def test_a_flow_into_a_materialized_view_or_into_nothing_here(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/a.sql": SILVER_SQL.replace("customers_silver", "x")
                     + "CREATE FLOW f AS INSERT INTO customers_summary BY NAME SELECT * FROM y;\n"
                     + "CREATE FLOW g AS AUTO CDC INTO nowhere FROM STREAM(z) KEYS (id) SEQUENCE BY ts;\n"})
    findings = validate(tmp_path)
    assert [(f.severity, f.line) for f in findings] == [(ERROR, 3), (WARNING, 4)]
    assert "A flow's target must be a streaming table." in findings[0].message
    assert "which no file of this pipeline defines" in findings[1].message


def test_a_view_read_as_a_stream(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/a.sql": (
        "CREATE TEMPORARY VIEW recent AS SELECT * FROM raw;\n"
        "CREATE OR REFRESH STREAMING TABLE t AS SELECT * FROM STREAM(recent);\n")})
    (f,) = validate(tmp_path)
    assert f.line == 2 and "A view can't be used as a streaming source." in f.message


def test_python_definitions_names_and_flows():
    code = '''import dlt
from pyspark import pipelines as dp

dp.create_streaming_table("orders")
dp.create_streaming_table(name="returns")


@dp.append_flow(target="orders", name="orders_backfill")
def backfill():
    return spark.read.table("old_orders")


@dp.materialized_view(name="daily")
def ignored_name():
    return spark.read.table("orders").groupBy("day").count()


@dlt.table
def legacy():
    return dlt.read_stream("orders")


@dp.temporary_view(name=f"{prefix}_v")
def unknown_name():
    return spark.table("orders")


dp.create_auto_cdc_flow(target="returns", source="returns_feed", keys=["id"], sequence_by="ts")
'''
    found = ps.Sources()
    ps._python(code, "p.py", found)
    assert [(d.name, d.kind) for d in found.datasets] == [
        ("orders", "streaming table"), ("returns", "streaming table"), ("daily", "materialized view"),
        ("legacy", "table")]
    assert {(f.name, f.target, f.named) for f in found.flows if not f.default} == {
        ("orders_backfill", "orders", True), ("returns", "returns", False)}
    assert {(r.name, r.stream) for r in found.reads} == {("old_orders", False), ("orders", False),
                                                         ("orders", True)}
    assert ps.check(found) == []


def test_python_that_doesnt_parse_is_a_warning_and_holds_back_the_unknown_target_check(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/bad.py": "def (:\n",
                     "transformations/f.sql": "CREATE FLOW f AS INSERT INTO elsewhere BY NAME SELECT 1;"})
    (f,) = validate(tmp_path)
    assert f.severity == WARNING and "couldn't be parsed as Python" in f.message


def test_comments_and_strings_dont_define_or_read_anything():
    found = ps.Sources()
    ps._sql("-- CREATE STREAMING TABLE a\n/* CREATE MATERIALIZED VIEW b\n AS SELECT 1 */\n"
            "CREATE MATERIALIZED VIEW c AS SELECT 'FROM d; CREATE VIEW e' AS s FROM f;", "x.sql", found)
    assert [(d.name, d.line) for d in found.datasets] == [("c", 4)]
    assert [r.name for r in found.reads] == ["f"]


def test_a_workspace_source_keeps_the_unknown_target_check_back(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(libraries="        - file: {path: ./f.sql}\n"
                                                    "        - notebook: {path: /Workspace/Shared/more}\n"),
                     "f.sql": "CREATE FLOW f AS INSERT INTO elsewhere BY NAME SELECT 1;"})
    assert validate(tmp_path) == []


@pytest.mark.parametrize(("settings", "path", "severity", "text"), [
    ("      edition: basic\n", "edition", ERROR, "edition is CORE, PRO or ADVANCED, not 'basic'."),
    ("      edition: pro\n      channel: Preview\n", None, None, None),
    ("      channel: beta\n", "channel", ERROR, "channel is CURRENT or PREVIEW, not 'beta'."),
    ("      serverless: true\n      clusters: [{label: default, num_workers: 2}]\n", "clusters", ERROR,
     "A serverless pipeline takes no clusters."),
    ("      serverless: 'yes'\n", "serverless", ERROR, "serverless is true or false, not a string."),
    ("      clusters: [{label: default, spark_version: 15.4.x-scala2.12}]\n", "clusters[0].spark_version", WARNING,
     "spark_version can't be set for a pipeline's cluster; Databricks sets it."),
    ("      target: old\n", "target", WARNING, ("target is a legacy setting; use schema instead. Set schema or "
                                                "target, not both.")),
    ("      tags: {" + ", ".join(f"t{i}: v" for i in range(26)) + "}\n", "tags", ERROR,
     "A pipeline takes at most 25 tags; this one has 26."),
    ("      ingestion_definition: {connection_name: sf}\n", "catalog", ERROR,
     "A pipeline with ingestion_definition takes no catalog."),
])
def test_pipeline_settings(tmp_path, settings, path, severity, text):
    write(tmp_path, {"databricks.yml": bundle(settings, libraries=""), })
    findings = [f for f in validate(tmp_path) if not f.path.endswith((".schema", ".libraries"))]
    if path is None:
        assert findings == []
        return
    hit = [f for f in findings if f.path == f"resources.pipelines.p.{path}"]
    assert len(hit) == 1 and hit[0].severity == severity and text in hit[0].message, findings


@pytest.mark.parametrize(("include", "text"), [
    ("./missing/**", "names a folder that doesn't exist: missing"),
    ("../elsewhere/**", "leads outside the bundle folder"),
    ("./src/*.py", "a glob include is a file, a notebook, or a folder ending in /**"),
    ("./src/nope.py", "doesn't exist"),
])
def test_glob_includes(tmp_path, include, text):
    write(tmp_path, {"databricks.yml": bundle(libraries=f"        - glob: {{include: '{include}'}}\n"),
                     "src/a.py": ""})
    (f,) = validate(tmp_path)
    assert f.severity == ERROR and f.path == "resources.pipelines.p.libraries[0].glob.include" and text in f.message


def test_a_glob_folder_with_no_source_and_a_workspace_glob(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(libraries="        - glob: {include: ./docs/**}\n"
                                                    "        - glob: {include: /Workspace/Shared/p/**}\n"),
                     "docs/readme.md": ""})
    (f,) = validate(tmp_path)
    assert f.severity == WARNING and "holds no .py or .sql file" in f.message


def test_a_whl_library_is_deprecated(tmp_path):
    write(tmp_path, {"databricks.yml": bundle(libraries="        - whl: /Volumes/main/libs/a.whl\n")})
    (f,) = validate(tmp_path)
    assert f.severity == WARNING and f.path == "resources.pipelines.p.libraries[0].whl"


def test_the_pipelines_command(tmp_path, capsys):
    write(tmp_path, {"databricks.yml": bundle(), "transformations/bronze.sql": BRONZE,
                     "transformations/silver.py": SILVER_PY})
    assert cli._databricks_bundle_validate(cli.argparse.Namespace(
        folder=str(tmp_path), json=False, databricks_bundle_command="pipelines")) == 0
    out = capsys.readouterr().out
    assert "  streaming table customers_bronze  (transformations/bronze.sql:2)" in out
    assert "0 errors" in out
    assert cli._databricks_bundle_validate(cli.argparse.Namespace(
        folder=str(tmp_path), json=True, databricks_bundle_command="pipelines")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert list(shown["pipelines"]) == ["resources.pipelines.p"] and shown["findings"] == []


# Large or deeply nested files are checked in time that grows with their size, and a file too deep to
# parse is a warning, not a failure of the whole check. Each would take many seconds (or fail) if not.

def test_many_statements_are_scanned_in_linear_time():
    import time

    found = ps.Sources()
    started = time.monotonic()
    ps._sql(";\n" * 200_000 + "CREATE STREAMING TABLE last AS SELECT * FROM src;", "f.sql", found)
    assert time.monotonic() - started < 3
    assert [(d.name, d.line) for d in found.datasets] == [("last", 200_001)]


def test_flow_heads_without_into_are_scanned_in_linear_time():
    import time

    found = ps.Sources()
    started = time.monotonic()
    ps._sql("CREATE FLOW x " * 15_000, "f.sql", found)
    assert time.monotonic() - started < 3
    assert found.flows == []


def test_python_too_deep_to_parse_is_a_warning():
    found = ps.Sources()
    ps._python("x = a" + ".b" * 200_000 + "\n", "deep.py", found)
    (p,) = found.problems
    assert p.severity == WARNING and "couldn't be parsed as Python" in p.message


def test_a_flow_still_finds_its_target_after_the_head():
    found = ps.Sources()
    ps._sql("\nCREATE FLOW f COMMENT 'c' AS\n  INSERT ONCE INTO t BY NAME SELECT 1;", "f.sql", found)
    assert [(f.name, f.target, f.line) for f in found.flows] == [("f", "t", 2)]
