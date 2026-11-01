"""KUDP's guard rails against false compatibility (hardening): a descriptor can't claim what it can't
back, usage never mixes or invents numbers, and SQL the checker can't read is never called compatible.
Test-only; each case is a path the other tests didn't reach."""

from __future__ import annotations

import pytest

from fabric_desktop import sql_portability as sp
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support
from fabric_desktop.engines.sessions import SessionManager
from fabric_desktop.engines.usage import UsageRecord

T = "unit_tests"
EXECUTE = {"execute": Support("native", evidence=T)}


# ---------------------------------------------------------------- descriptors


def test_evidence_must_be_a_known_kind():
    with pytest.raises(ValueError, match="Unknown evidence 'a blog post'"):
        Support("native", evidence="a blog post")


@pytest.mark.parametrize("location, platform, billing", [
    ("cloud", "local", "none"), ("local", "oracle", "none"), ("local", "local", "free"),
])
def test_location_platform_and_billing_are_known_values(location, platform, billing):
    with pytest.raises(ValueError, match="not one of the known values"):
        EngineDescriptor("x", "X", location, platform, billing, adapter=True, capabilities=EXECUTE)


def test_a_language_nobody_defined_is_refused():
    with pytest.raises(ValueError, match=r"unknown languages \['cobol'\]"):
        EngineDescriptor("x", "X", "local", "local", "none", adapter=True, capabilities=EXECUTE,
                         languages={"cobol": Support("native", evidence=T)})


def test_an_engine_without_an_adapter_cant_claim_a_language_or_dialect_either():
    for field in ("languages", "dialects"):
        with pytest.raises(ValueError, match="can't claim support"):
            EngineDescriptor("x", "X", "local", "local", "none", adapter=False,
                             **{field: {"sql": Support("partial", evidence=T)}})


def test_a_remote_engine_claiming_only_unit_tested_dialects_must_still_be_preview():
    with pytest.raises(ValueError, match="must be Preview"):
        EngineDescriptor("x", "X", "remote", "databricks", "databricks_dbu", adapter=True,
                         capabilities={"execute": Support("native", evidence="live_verified")},
                         dialects={"databricks": Support("native", evidence=T)})


def test_asking_for_a_capability_nobody_defined_is_an_error_not_unsupported():
    with pytest.raises(ValueError, match="Unknown capability 'teleport'"):
        registry.get("local-duckdb").capability("teleport")


def test_an_unknown_engine_names_the_ones_there_are():
    with pytest.raises(ValueError, match="Unknown engine 'oracle'.*local-duckdb"):
        registry.get("oracle")


def test_every_registered_claim_carries_evidence():
    for d in registry.ENGINES.values():
        for support in [*d.languages.values(), *d.dialects.values(), *d.capabilities.values()]:
            assert not support.available or support.evidence in ("unit_tests", "live_verified"), d.id


# ---------------------------------------------------------------- usage


@pytest.mark.parametrize("kwargs, problem", [
    ({"kind": "guessed"}, "Unknown kind"),
    ({"amount": -0.5}, "can't be negative"),
    ({"kind": "estimated"}, "says what it assumes"),
])
def test_usage_that_would_mislead_is_refused(kwargs, problem):
    args = {"engine": "local-duckdb", "unit": "seconds", "amount": 1.0, "kind": "measured", **kwargs}
    with pytest.raises(ValueError, match=problem):
        UsageRecord(**args)


def test_adding_usage_from_two_engines_names_neither_as_the_engine():
    a = UsageRecord("local-duckdb", "seconds", 1.0, "measured", "engine session")
    b = UsageRecord("local-sqlite", "seconds", 2.0, "measured", "engine session")
    total = a.add(b)
    assert total.engine == "*" and total.amount == 3.0


# ---------------------------------------------------------------- sessions


def test_cancel_and_close_for_a_notebook_with_no_session_do_nothing():
    opened = []
    manager = SessionManager(lambda engine: opened.append(engine))
    assert manager.cancel("nb") is False
    manager.close("nb")
    assert opened == [] and manager.session("nb") is None


# ---------------------------------------------------------------- SQL portability


@pytest.mark.parametrize("query", ["SHOW TABLES", "VACUUM t RETAIN 168 HOURS"])
def test_sql_kept_only_as_text_is_unverified_on_every_target_never_compatible(query):
    report = sp.portability(query, "spark")
    assert report["targets"]
    for target in report["targets"]:
        assert target["grade"] == "unverified", target
        assert target["statements"][0]["rewritten"] is None


def test_no_grades_means_unverified_and_unreadable_sql_has_no_normal_form():
    assert sp._worst([]) == "unverified"
    assert sp._normal("SELECT ((", "spark") is None
    assert sp._normal("SELECT 1; SELECT 2", "spark") is None
