"""Mutation batch 51: capability_registry.py with a fixed set of engines (docs/quality/mutation-testing.md)."""

import dataclasses

import pytest

from fabric_desktop import capability_registry as cr
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support

T = "unit_tests"

LOCAL = EngineDescriptor(
    "loc", "Local", "local", "local", "none", adapter=True, requirements=("duckdb",),
    capabilities={"execute": Support("native", evidence=T)},
    languages={"python": Support("native", "Plain CPython.", T),
               "pyspark": Support("simulated", "DuckDB's Spark API.", T)},
    dialects={"duckdb": Support("native", evidence=T), "spark": Support("partial", evidence=T)})
REMOTE = EngineDescriptor(
    "rem", "Remote", "remote", "databricks", "databricks_dbu", adapter=True, preview=True,
    capabilities={"execute": Support("native", evidence=T)},
    languages={"python": Support("partial", "No widgets.", T)},
    dialects={"spark": Support("native", "Runs on the cluster.", T)})


@pytest.fixture
def engines(monkeypatch):
    monkeypatch.setattr(registry, "ENGINES", {"loc": LOCAL, "rem": REMOTE})


def test_capability_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        cr.CAPABILITIES[0].label = "x"


def test_requirements_are_listed_only_where_the_capability_runs():
    scala = next(c for c in cr.CAPABILITIES if c.id == "scala")
    python = next(c for c in cr.CAPABILITIES if c.id == "python")
    assert cr.entry(scala, LOCAL)["requirements"] == []
    assert cr.entry(python, LOCAL)["requirements"] == ["duckdb"]


def test_listing_has_every_capability_and_the_classifications(engines):
    out = cr.listing()
    assert out["classifications"] == cr.CLASSIFICATIONS
    assert [c["id"] for c in out["capabilities"]] == [c.id for c in cr.CAPABILITIES]


def test_docs_table_rows_and_known_differences(engines):
    assert cr.docs_table() == (
        "| Capability | Engine | How it runs | Where |\n"
        "| --- | --- | --- | --- |\n"
        "| Run SQL on this PC | Local | Native local execution | This PC · no Fabric job |\n"
        "| Run Spark SQL | Local | Compatible local execution, with documented differences | This PC · no Fabric job |\n"
        "| Run Spark SQL | Remote | Remote cloud execution (Preview) | Databricks · Databricks DBUs |\n"
        "| Run a Python cell | Local | Native local execution | This PC · no Fabric job |\n"
        "| Run a Python cell | Remote | Remote cloud execution (Preview) | Databricks · Databricks DBUs |\n"
        "| Run a PySpark cell | Local | Local simulation or validation | This PC · no Fabric job |\n"
        "\n"
        "Known differences:\n"
        "\n"
        "- **Run a Python cell, Remote:** No widgets.\n"
        "- **Run a PySpark cell, Local:** DuckDB's Spark API.\n")


def test_docs_table_without_notes_has_no_known_differences(engines, monkeypatch):
    bare = EngineDescriptor("b", "Bare", "local", "local", "none", adapter=True,
                            capabilities={"execute": Support("native", evidence=T)},
                            dialects={"duckdb": Support("native", evidence=T)})
    monkeypatch.setattr(registry, "ENGINES", {"b": bare})
    assert cr.docs_table() == (
        "| Capability | Engine | How it runs | Where |\n"
        "| --- | --- | --- | --- |\n"
        "| Run SQL on this PC | Bare | Native local execution | This PC · no Fabric job |\n")
