"""Mutation batch 51: the engine descriptor rules in engines/capabilities.py (docs/quality/mutation-testing.md)."""

import dataclasses

import pytest

from fabric_desktop.engines.capabilities import EngineDescriptor, Support, check_cell

T = "unit_tests"
LIVE = "live_verified"


def _local(**kw):
    base = dict(id="x", label="X", location="local", platform="local", billing="none", adapter=True,
                capabilities={"execute": Support("native", evidence=T)})
    return EngineDescriptor(**(base | kw))


def test_support_as_dict_lists_level_note_and_evidence():
    assert Support("partial", "no UDFs", T).as_dict() == {"level": "partial", "note": "no UDFs", "evidence": T}
    assert Support().as_dict() == {"level": "unsupported", "note": "", "evidence": "none"}


def test_unknown_evidence_is_refused():
    with pytest.raises(ValueError, match="Unknown evidence 'rumour'"):
        Support("native", evidence="rumour")


def test_support_and_descriptor_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Support().level = "native"
    with pytest.raises(dataclasses.FrozenInstanceError):
        _local().label = "Y"


@pytest.mark.parametrize("field,value", [("location", "moon"), ("platform", "oracle"), ("billing", "coins")])
def test_each_unknown_location_platform_or_billing_is_refused_on_its_own(field, value):
    with pytest.raises(ValueError, match="location, platform or billing is not one of the known values"):
        _local(**{field: value})


def test_unknown_languages_are_refused():
    with pytest.raises(ValueError, match=r"unknown languages \['cobol'\]"):
        _local(languages={"cobol": Support("native", evidence=T)})


def test_asking_for_an_unknown_capability_is_refused():
    with pytest.raises(ValueError, match="Unknown capability 'teleport'"):
        _local().capability("teleport")


def test_a_live_verified_remote_engine_may_list_unsupported_things_without_being_preview():
    e = EngineDescriptor("r", "R", "remote", "databricks", "databricks_dbu", adapter=True,
                         capabilities={"execute": Support("native", evidence=LIVE)},
                         languages={"python": Support("native", evidence=LIVE), "scala": Support()})
    assert e.preview is False and e.language("scala").available is False


def test_an_engine_without_an_adapter_says_what_is_planned_only_when_something_is():
    planned = EngineDescriptor("p", "Later", "local", "local", "none", adapter=False, planned="Runs SQL in phase 2.")
    assert check_cell(planned, "sql")["reason"] == "Later can't run cells in this version. Planned: Runs SQL in phase 2."
    bare = EngineDescriptor("q", "Bare", "local", "local", "none", adapter=False)
    assert check_cell(bare, "sql")["reason"] == "Bare can't run cells in this version."


def test_check_cell_reasons_for_native_simulated_and_translated():
    e = _local(languages={"python": Support("native", "Runs CPython.", T),
                          "pyspark": Support("simulated", evidence=T),
                          "sql": Support("translated", "Rewritten by sqlglot.", T)})
    assert check_cell(e, "python") == {"ok": True, "level": "native", "reason": "Runs CPython."}
    assert check_cell(e, "pyspark")["reason"] == "X runs pyspark simulated, not on the platform's own engine."
    assert check_cell(e, "sql")["reason"] == "X runs sql translated: Rewritten by sqlglot."
