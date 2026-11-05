"""The platform-neutral capability registry (capability_registry.py, docs/capabilities.md)."""

from pathlib import Path

import pytest

from fabric_desktop import capability_registry as cr
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support

DOC = Path(__file__).resolve().parents[2] / "docs" / "capabilities.md"
BEGIN, END = "<!-- BEGIN GENERATED: capabilities -->\n", "<!-- END GENERATED: capabilities -->"
T = Support("native", evidence="unit_tests")


def _engine(location="local", platform="local", billing="none", preview=False, **langs):
    return EngineDescriptor("x", "X", location, platform, billing, True, preview=preview,
                            languages={"python": T, **langs}, capabilities={"execute": T})


def test_docs_page_matches_the_code():
    text = DOC.read_text(encoding="utf-8")
    assert text[text.index(BEGIN) + len(BEGIN):text.index(END)] == cr.docs_table()


@pytest.mark.parametrize("level, expected", [("native", "native_local"), ("partial", "compatible_local"),
                                             ("translated", "compatible_local"), ("simulated", "simulated")])
def test_local_levels_map_to_one_classification(level, expected):
    assert cr.classify(_engine(), Support(level, evidence="unit_tests")) == expected


def test_a_cloud_engine_is_always_remote_and_asks_first():
    cloud = _engine("remote", "fabric", "fabric_capacity", preview=True)
    assert cr.classify(cloud, T) == "remote"
    e = cr.entry(cr.CAPABILITIES[4], cloud)  # python
    assert e["asks_before_running"] is True and e["where"] == "Fabric · Fabric capacity"


def test_unsupported_is_unsupported_everywhere():
    assert cr.classify(_engine(), Support()) == "unsupported"
    assert cr.classify(_engine("remote", "fabric", "fabric_capacity", preview=True), Support()) == "unsupported"


def test_no_local_run_costs_cloud_money_and_no_cloud_run_is_called_local():
    for cap in cr.CAPABILITIES:
        for e in cr.capability(cap.id)["engines"]:
            engine = registry.ENGINES[e["engine"]]
            assert e["classification"] in cr.CLASSIFICATIONS
            if engine.remote:
                assert e["classification"] == "remote" and e["billing"] != "none" and e["asks_before_running"]
            else:
                assert e["classification"] != "remote" and e["where"] == "This PC · no Fabric job"
                assert not e["asks_before_running"]


def test_the_registry_says_what_the_engines_say():
    spark = {e["engine"]: e for e in cr.capability("sql.spark")["engines"]}
    assert spark["local-duckdb"]["classification"] == "compatible_local"   # sqlglot translation
    assert spark["fabric-spark"]["classification"] == "remote"
    pyspark = {e["engine"]: e for e in cr.capability("pyspark")["engines"]}
    assert pyspark["local-python"]["classification"] == "simulated"         # DuckDB's PySpark API
    # Snowflake SQL has no local runtime: on this PC it is only ever translated, never native.
    assert all(e["classification"] != "native_local" for e in cr.capability("sql.snowflake")["engines"])


def test_engines_without_an_adapter_claim_nothing():
    listed = {e["engine"] for c in cr.CAPABILITIES for e in cr.capability(c.id)["engines"]}
    assert not listed & {e.id for e in registry.ENGINES.values() if not e.adapter}


def test_capabilities_are_checked():
    with pytest.raises(ValueError, match="kind"):
        cr.Capability("x", "X", ("local",), "magic", "x")
    with pytest.raises(ValueError, match="platforms"):
        cr.Capability("x", "X", ("oracle",), "language", "python")
    with pytest.raises(ValueError, match="Unknown capability"):
        cr.capability("nope")
    assert len({c.id for c in cr.CAPABILITIES}) == len(cr.CAPABILITIES)
