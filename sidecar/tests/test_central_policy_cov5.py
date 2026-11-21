"""Governance policies on the platform (central/policy.py), the paths the other tests leave out: ids and
definitions that are refused, drafts and versions that aren't there, status changes that do nothing,
exceptions that can't be asked for or granted, assignment targets and settings that are refused, an
entity's chain, a pinned older version and a parameter the version doesn't have, which way each kind
of setting gets weaker, and templates that aren't there. Runs on a SQLite platform in tmp_path."""

import datetime as dt
import sqlite3

import pytest

from fabric_desktop.central import core, policy

ADMIN = "admin@contoso.com"
BASE = {"name": "Owners named", "category": "ownership"}


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "platform.db")
    d = core.Db(conn, "sqlite")
    core.ensure_schema(d)
    core.claim_admin(d, ADMIN)
    yield d
    conn.close()


def _published(db, pid="owners", **extra):
    policy.create_policy(db, dict(BASE, id=pid, **extra), ADMIN)
    return policy.publish_policy(db, pid, by=ADMIN)


# ---- ids and definitions


@pytest.mark.parametrize("value", ["", "-x", "a b/c", "x" * 65])
def test_a_policy_id_is_short_lowercase_and_plain(value):
    with pytest.raises(core.PlatformError, match="isn't a policy id"):
        policy.policy_id(value)


@pytest.mark.parametrize("change, message", [
    ({"name": " "}, "A policy needs a name"),
    ({"scopes": ["galaxy"]}, "scopes are some of enterprise, domain, project, entity"),
    ({"applies_to": {"regions": ["eu"]}}, "applies_to may list layers, environments and asset_types"),
    ({"applies_to": ["layers"]}, "applies_to may list"),
    ({"effective_from": "2027-01-31", "expires_on": "2027-01-31"}, "A policy must expire after it takes effect"),
    ({"expires_on": "next year"}, "Expires on is a date like 2027-01-31"),
    ({"params": ["max_age"]}, "params is a set of named parameters"),
    ({"params": {"tags": {"value": "pii", "stricter": "superset"}}}, "parameter tags gets stricter as a superset, so its value is a list"),
    ({"params": {"on": {"value": 1, "stricter": "true"}}}, "parameter on gets stricter when true, so its value is true or false"),
    ({"params": {"days": {"value": True, "stricter": "lower"}}}, "parameter days gets stricter lower, so its value is a number"),
    ({"params": {"days": 30}}, "parameter days needs a value"),
])
def test_a_definition_that_doesnt_fit_is_refused_by_what_is_wrong(change, message):
    with pytest.raises(core.PlatformError, match=message):
        policy.clean_definition(dict(BASE, **change))


# ---- drafts, versions and status


def test_drafts_and_versions_that_arent_there_are_refused(db):
    _published(db)
    with pytest.raises(core.PlatformError, match="owners has no draft$"):
        policy.discard_policy_draft(db, "owners", ADMIN)
    with pytest.raises(core.PlatformError, match="owners has no draft to publish"):
        policy.publish_policy(db, "owners", by=ADMIN)
    with pytest.raises(core.PlatformError, match="owners has no version 7"):
        policy.policy_version(db, "owners", 7)
    # A draft pointing at a version already published is never changed.
    policy._set(db, "owners", {"draft_version": "1"})
    with pytest.raises(core.PlatformError, match="Version 1 of owners is published and can't be changed"):
        policy.save_policy_draft(db, "owners", dict(BASE, name="Changed"), by=ADMIN)
    with pytest.raises(core.PlatformError, match="owners has no draft to publish"):
        policy.publish_policy(db, "owners", by=ADMIN)
    assert policy.policy_version(db, "owners", 1)["definition"]["name"] == "Owners named"


def test_a_never_published_policy_cant_be_deprecated_or_dropped_while_assigned(db):
    policy.create_policy(db, dict(BASE, id="draft-only"), ADMIN)
    with pytest.raises(core.PlatformError, match="draft-only was never published: discard its draft instead"):
        policy.set_policy_status(db, "draft-only", "deprecated", by=ADMIN)
    core._upsert(db, "catalog", "policy_assignment", "id", {"id": "draft-only|enterprise|*", "policy_id": "draft-only",
                                                           "scope": "enterprise", "target": "*", "settings": "{}"})
    with pytest.raises(core.PlatformError, match="draft-only is assigned somewhere; remove its assignments first"):
        policy.discard_policy_draft(db, "draft-only", ADMIN)


def test_setting_the_status_it_already_has_changes_nothing(db):
    before = _published(db)
    after = policy.set_policy_status(db, "owners", "published", by=ADMIN)
    assert after["status"] == "published" and len(after["history"]) == len(before["history"])


# ---- exceptions


def test_an_exception_lasts_a_year_at_most_even_from_29_february():
    assert policy._one_year_from(dt.date(2028, 2, 29)) == dt.date(2029, 2, 28)
    assert policy._one_year_from(dt.date(2027, 3, 1)) == dt.date(2028, 3, 1)


def test_an_exception_needs_a_published_policy_and_a_date(db):
    policy.create_policy(db, dict(BASE, id="draft-only"), ADMIN)
    with pytest.raises(core.PlatformError, match="draft-only isn't a published policy, so it needs no exception"):
        policy._check_exception(db, "draft-only", {"scope": "project", "scope_target": "crm"}, "why", ADMIN)
    _published(db)
    with pytest.raises(core.PlatformError, match="Say until when: an exception ends on its date"):
        policy._check_exception(db, "owners", {"scope": "project", "scope_target": "crm"}, "why", ADMIN)


def test_an_exception_whose_date_passed_before_it_was_approved_isnt_granted(db):
    _published(db)
    yesterday = (core._today() - dt.timedelta(days=1)).isoformat()
    req = {"id": "r1", "detail": {"policy": "owners", "scope": "project", "scope_target": "crm", "until": yesterday}}
    with pytest.raises(core.PlatformError, match=f"This exception would have ended on {yesterday}: decline it"):
        policy._approve_exception(db, req, ADMIN)
    assert policy.list_policy_exceptions(db) == []


def test_no_exceptions_before_the_platform_has_their_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "bare.db")
    try:
        bare = core.Db(conn, "sqlite")
        assert policy.list_policy_exceptions(bare, active=True) == []
        assert policy._template_authors(bare) == "admins"
    finally:
        conn.close()


# ---- assignments


@pytest.mark.parametrize("scope, target, message", [
    ("domain", "nowhere", "domain nowhere isn't one of your organization's domains"),
    ("project", " ", "Say which project the policy applies to"),
    ("entity", "silver.customer", "An entity is named <project>:<entity>, like crm:silver.customer"),
])
def test_an_assignment_names_a_target_that_exists(db, scope, target, message):
    _published(db)
    with pytest.raises(core.PlatformError, match=message):
        policy.save_assignment(db, "owners", scope, target, by=ADMIN)


@pytest.mark.parametrize("settings, message", [
    ({"color": "red"}, "color can't be set on an assignment; it takes enforcement, severity, mandatory, version, params"),
    ({"params": {"max_age": 3}}, "max_age isn't one of this policy's parameters"),
])
def test_an_assignment_takes_only_known_settings(db, settings, message):
    _published(db)
    with pytest.raises(core.PlatformError, match=message):
        policy.save_assignment(db, "owners", "enterprise", settings=settings, by=ADMIN)


def test_an_entity_assignment_inherits_from_its_project_and_can_be_removed_once(db):
    _published(db)
    a = policy.save_assignment(db, "owners", "entity", "crm:silver.customer", settings={"severity": "high"}, by=ADMIN)
    assert a["settings"] == {"severity": "high"}
    assert policy._chain(db, "entity", "crm:silver.customer") == [("enterprise", "*"), ("project", "crm"),
                                                                  ("entity", "crm:silver.customer")]
    assert policy.remove_assignment(db, "owners", "entity", "crm:silver.customer", by=ADMIN) == []
    with pytest.raises(core.PlatformError, match="owners isn't assigned to entity crm:silver.customer"):
        policy.remove_assignment(db, "owners", "entity", "crm:silver.customer", by=ADMIN)


def test_a_project_can_pin_an_older_version_and_a_missing_parameter_is_a_conflict(db):
    params = {"days": {"value": 30, "stricter": "lower"}}
    _published(db, params=params)
    policy.save_policy_draft(db, "owners", dict(BASE, name="Owners named v2", params=params), by=ADMIN)
    policy.publish_policy(db, "owners", by=ADMIN)
    policy.save_assignment(db, "owners", "enterprise", by=ADMIN)
    policy.save_assignment(db, "owners", "project", "crm", settings={"version": 1}, by=ADMIN)
    out = policy.resolve_policies(db, project="crm", local=[{"policy": "owners", "scope": "project", "target": "crm",
                                                             "settings": {"params": {"retention": 5}}}])
    [p] = out["policies"]
    assert (p["version"], p["name"]) == (1, "Owners named")
    assert {"change": "version", "from": 2, "to": 1, "outcome": "applied"}.items() <= p["steps"][1].items()
    assert [c["message"] for c in out["conflicts"]] == ["owners version 1 has no parameter retention"]


# ---- which way a setting gets weaker


def test_each_kind_of_setting_gets_weaker_its_own_way():
    w = policy._weaker
    assert w("version", None, 3, 2) and not w("version", None, 2, 3)
    assert w("params", {"stricter": "higher"}, 10, 5) and not w("params", {"stricter": "higher"}, 5, 10)
    assert w("params", {"stricter": "superset"}, ["a", "b"], ["a"]) and not w("params", {"stricter": "superset"}, ["a"], ["a", "b"])
    assert w("params", {"stricter": "subset"}, ["a"], ["a", "b"]) and not w("params", {"stricter": "subset"}, ["a", "b"], ["a"])
    assert w("params", {"stricter": "true"}, True, False) and not w("params", {"stricter": "true"}, False, True)
    assert w("params", None, 1, 2) and not w("params", None, 1, 1)


# ---- templates


def test_templates_that_arent_there_are_named(db):
    with pytest.raises(core.PlatformError, match="There's no template gone"):
        policy.delete_policy_template(db, "gone", ADMIN)
    policy.create_policy(db, dict(BASE, id="empty"), ADMIN)
    policy._set(db, "empty", {"draft_version": None})
    with pytest.raises(core.PlatformError, match="empty has no version to copy"):
        policy.save_policy_as_template(db, "empty", by=ADMIN)


def test_a_policy_saved_as_a_template_can_take_a_new_name(db):
    _published(db)
    t = policy.save_policy_as_template(db, "owners", name="  Owners everywhere ", by=ADMIN)
    assert (t["id"], t["name"], t["definition"]["category"]) == ("owners-template", "Owners everywhere", "ownership")
