"""Best practice check registry and the built-in checkers: what the first mutation run of
governance_checks.py left alive (batch 334)."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import governance_checks as gc

NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)


def on(rule_id: str, **thresholds) -> dict:
    return {rule_id: {"enabled": True, "severity": "Warning", "thresholds": thresholds}}


@pytest.fixture
def temp_kind():
    rules = [{"id": "tmp.rule", "kind": "thing", "severity": "Info", "category": "c", "name": "n",
              "description": "d"}]
    yield lambda: gc.register("tmp_kind", lambda: [dict(r) for r in rules], lambda item, settings: [])
    gc.unregister("tmp_kind")


def test_register_returns_the_checker_and_checkers_lists_it(temp_kind):
    c = temp_kind()
    assert isinstance(c, gc.Checker) and c.kind == "tmp_kind" and c.runs_in == "local"
    assert c in gc.checkers()
    assert {"naming", "ownership", "classification", "contract", "tmp_kind"} <= {x.kind for x in gc.checkers()}


def test_a_rule_no_checker_has_is_reported_missing():
    assert gc.run_rule("no.such_rule", [], {}) == {"rule_id": "no.such_rule", "missing": True}


def test_a_table_name_of_exactly_the_limit_is_checked_and_one_over_isnt():
    settings = on("naming.table_name", pattern="^[a-z]+$")
    ok = gc.naming_check({"kind": "entity", "id": "e", "table": "a" * 256}, settings)
    assert ok == []
    long = gc.naming_check({"kind": "entity", "id": "e", "table": "a" * 257}, settings)
    assert len(long) == 1 and "is 257 characters, too long to check" in long[0]["description"]


def test_naming_checks_only_entities():
    settings = on("naming.table_name", pattern="^x$")
    assert gc.naming_check({"kind": "product", "id": "p", "table": "y"}, settings) is None


def test_the_entity_owner_rule_applies_only_when_on_and_flags_only_a_missing_owner():
    assert gc.ownership_check({"kind": "entity", "id": "e", "owner": ""}, {}) is None
    settings = on("ownership.entity_owner")
    assert gc.ownership_check({"kind": "entity", "id": "e", "owner": "ana"}, settings) == []
    (f,) = gc.ownership_check({"kind": "entity", "id": "e", "owner": " "}, settings)
    assert f["description"] == "Names no owner." and f["rule_id"] == "ownership.entity_owner"


def test_classification_required_applies_only_in_its_layers():
    settings = on("classification.required", layers=["gold"])
    (f,) = gc.classification_check({"kind": "entity", "id": "e", "layer": "Gold"}, settings)
    assert f["description"] == "Has no classification."
    assert gc.classification_check({"kind": "entity", "id": "e", "layer": "silver"}, settings) is None
    assert gc.classification_check({"kind": "entity", "id": "e", "layer": "silver"},
                                   on("classification.required")) == [f]


def test_a_load_time_without_a_zone_is_read_as_utc():
    assert gc._age_hours("2026-10-24T10:00:00", NOW) == 2.0
    assert gc._age_hours("2026-10-24T10:00:00Z", NOW) == 2.0


def published(**kw) -> dict:
    return {"kind": "product", "id": "p", "name": "Sales", "project": "crm", "status": "published", **kw}


def test_contract_checks_only_published_products_and_only_rules_that_are_on():
    assert gc.contract_check(published(status="draft"), on("contract.description"), now=NOW) is None
    assert gc.contract_check(published(), {}, now=NOW) is None
    (f,) = gc.contract_check(published(), on("contract.description"), now=NOW)
    assert f["description"] == "Has no description."


def test_freshness_by_default_allows_24_hours():
    rule = gc._CONTRACT["contract.freshness"]
    settings = {"contract.freshness": gc.settings_for(rule, "medium", {})}
    late = (NOW - dt.timedelta(hours=24, minutes=30)).isoformat()
    (f,) = gc.contract_check(published(last_load=late), settings, now=NOW)
    assert "the contract says within 24 h" in f["description"]
    fresh = (NOW - dt.timedelta(hours=23, minutes=30)).isoformat()
    assert gc.contract_check(published(last_load=fresh), settings, now=NOW) == []


def test_a_rule_with_a_severity_that_isnt_error_warning_or_info_is_refused():
    rules = [{"id": "tmp.bad", "kind": "thing", "severity": "high", "category": "c", "name": "n", "description": "d"}]
    with pytest.raises(ValueError, match="rule tmp.bad: severity is Error, Warning or Info"):
        gc.register("tmp_bad_kind", lambda: rules, lambda item, settings: [])
    assert "tmp_bad_kind" not in {c.kind for c in gc.checkers()}


def test_a_finding_links_the_rules_page_or_the_docs():
    rule = {"id": "r", "severity": "Info", "name": "n", "category": "c"}
    assert gc.finding(rule, {}, "entity", "e", "d")["url"] == gc.DOCS
    assert gc.finding(dict(rule, url="https://example.com/r"), {}, "entity", "e", "d")["url"] == "https://example.com/r"


def test_settings_without_enabled_run_the_rule():
    item = {"kind": "entity", "id": "e", "layer": "gold", "table": "dim_x"}
    out = gc.run_rule("naming.table_name", [item], {"severity": "Warning", "thresholds": {"pattern": "^dim_x$"}})
    assert "disabled" not in out and out["checked"] == 1 and out["passed"] == 1


def test_a_disabled_rule_checks_and_passes_nothing():
    item = {"kind": "entity", "id": "e", "layer": "gold", "table": "dim_x"}
    out = gc.run_rule("naming.table_name", [item], {"enabled": False})
    assert out["disabled"] and (out["checked"], out["passed"], out["findings"]) == (0, 0, [])


def test_a_product_needs_a_steward_unless_the_policy_says_otherwise():
    (f,) = gc.ownership_check(published(owner="ana", steward=""), on("ownership.product_owner"))
    assert f["description"] == "Names no steward."
    assert gc.ownership_check(published(owner="ana", steward=""),
                              on("ownership.product_owner", steward_required=False)) == []


def test_a_rule_missing_a_field_is_refused_by_id_and_field():
    rules = [{"id": "tmp.half", "kind": "thing", "severity": "Info", "name": "n", "description": "d"}]
    with pytest.raises(ValueError, match="rule tmp.half of tmp_half_kind needs category"):
        gc.register("tmp_half_kind", lambda: rules, lambda item, settings: [])


def test_a_checker_that_fails_reports_unknown_for_the_item_it_was_checking():
    rules = [{"id": "tmp.boom", "kind": "thing", "severity": "Info", "category": "c", "name": "n", "description": "d"}]

    def boom(item, settings):
        raise RuntimeError("bad setting")

    gc.register("tmp_boom_kind", lambda: rules, boom)
    try:
        out = gc.run_rule("tmp.boom", [{"kind": "thing", "id": "x1"}], {})
    finally:
        gc.unregister("tmp_boom_kind")
    (u,) = out["unknown"]
    assert (u["object_type"], u["object"]) == ("thing", "x1")
    assert "RuntimeError: bad setting" in u["description"]


def test_freshness_without_hours_allows_24_and_offers_the_first_entity():
    late = (NOW - dt.timedelta(hours=24, minutes=30)).isoformat()
    (f,) = gc.contract_check(published(last_load=late, entities=["silver.orders", "gold.x"]),
                             on("contract.freshness"), now=NOW)
    assert "within 24 h" in f["description"]
    assert f["fix"] == {"action": "open_entity", "label": "Open the entity", "entity": "silver.orders"}
    (f,) = gc.contract_check(published(last_load=late), on("contract.freshness"), now=NOW)
    assert "fix" not in f


def test_a_load_exactly_at_the_limit_is_fresh():
    at_limit = (NOW - dt.timedelta(hours=24)).isoformat()
    assert gc.contract_check(published(last_load=at_limit), on("contract.freshness", within_hours=24), now=NOW) == []


def test_row_count_without_a_minimum_allows_zero_and_exactly_the_minimum_passes():
    assert gc.contract_check(published(last_rows=0), on("contract.row_count"), now=NOW) == []
    assert gc.contract_check(published(last_rows=5), on("contract.row_count", min_rows=5), now=NOW) == []
    (f,) = gc.contract_check(published(last_rows=4), on("contract.row_count", min_rows=5), now=NOW)
    assert "wrote 4 rows" in f["description"]


def test_a_too_long_name_shows_its_first_40_characters():
    table = "b" * 40 + "c" * 217  # 257 characters, one over the limit
    (f,) = gc.naming_check({"kind": "entity", "id": "e", "table": table}, on("naming.table_name", pattern="^.*$"))
    assert f["description"].startswith("b" * 40 + "...")


def test_a_pattern_of_exactly_the_limit_is_used_and_one_over_isnt():
    pattern = "dim_x|" + "b" * 294
    assert len(pattern) == 300
    item = {"kind": "entity", "id": "e", "table": "dim_x"}
    assert gc.naming_check(item, on("naming.table_name", pattern=pattern)) == []
    (f,) = gc.naming_check(item, on("naming.table_name", pattern=pattern + "b"))
    assert f["outcome"] == "unknown"


def test_a_name_of_exactly_max_length_is_allowed():
    item = {"kind": "entity", "id": "e", "table": "dim_x"}
    assert gc.naming_check(item, on("naming.table_name", pattern="^dim_x$", max_length=5)) == []
    (f,) = gc.naming_check(item, on("naming.table_name", pattern="^dim_x$", max_length=4))
    assert "is 5 characters; at most 4" in f["description"]
