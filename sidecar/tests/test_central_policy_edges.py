"""central/policy.py at its edges, on the SQLite platform: definitions and assignments that are refused
(and the message each gets), drafts and versions in states the app never leaves them in but a platform
copy can hold, exception requests that can't be granted, how a parameter or version counts as weaker,
a version pinned lower down, and templates that aren't there."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.central import core, policy
from fabric_desktop.central.client import LocalPlatform

ADMIN, OWNER = "ana@contoso.com", "omar@contoso.com"
RULE = {"id": "owner-named", "name": "Every table names an owner", "category": "ownership",
        "params": {"approvers": {"value": ["steward"], "stricter": "superset"},
                   "labels": {"value": ["confidential", "restricted"], "stricter": "subset"},
                   "min_rows": {"value": 1, "stricter": "higher"},
                   "steward_required": {"value": True, "stricter": "true"}}}


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: ADMIN)
    p.call("save_domain", domain={"name": "Sales", "owners": OWNER})
    p.call("register_project", project={"name": "crm", "domain": "sales"})
    return p


def published(platform, definition=RULE):
    platform.call("create_policy", policy=definition)
    return platform.call("publish_policy", id=definition["id"])


def sql(platform, statement, params=()):
    with platform._db() as db:
        db.run(statement.format(**{t: db.t("catalog", t) for t in ("policy", "policy_version", "policy_assignment",
                                                                     "policy_exception")}), params)
        db.commit()


# --------------------------------------------------------------------------- definitions


@pytest.mark.parametrize("pid", ["-starts-with-a-dash", "x" * 65, "semi;colon"])
def test_a_policy_id_that_isnt_one_is_refused(platform, pid):
    with pytest.raises(core.PlatformError, match="isn't a policy id"):
        platform.call("create_policy", policy={"id": pid, "name": "x", "category": "naming"})


@pytest.mark.parametrize(("spec", "message"), [
    ({"value": "steward", "stricter": "superset"}, "stricter as a superset, so its value is a list"),
    ({"value": "steward", "stricter": "subset"}, "stricter as a subset, so its value is a list"),
    ({"value": "yes", "stricter": "true"}, "stricter when true, so its value is true or false"),
])
def test_a_parameter_whose_value_doesnt_fit_its_direction_is_refused(spec, message):
    with pytest.raises(core.PlatformError, match=message):
        policy.clean_definition({"name": "x", "category": "naming", "params": {"who": spec}})


@pytest.mark.parametrize(("field", "value", "message"), [
    ("scopes", ["enterprise", "galaxy"], "scopes are some of enterprise, domain, project, entity"),
    ("applies_to", {"teams": ["a"]}, "applies_to may list layers, environments and asset_types"),
    ("applies_to", ["gold"], "applies_to may list"),
    ("params", ["max_length"], "params is a set of named parameters"),
])
def test_a_definition_with_a_wrong_shape_is_refused(field, value, message):
    with pytest.raises(core.PlatformError, match=message):
        policy.clean_definition({"name": "x", "category": "naming", field: value})


# --------------------------------------------------------------------------- drafts, versions and status


def test_a_draft_pointing_at_a_published_version_isnt_changed(platform):
    published(platform)
    sql(platform, "UPDATE {policy} SET draft_version = '1' WHERE id = ?", ("owner-named",))
    with pytest.raises(core.PlatformError, match="Version 1 of owner-named is published and can't be changed"):
        platform.call("save_policy_draft", id="owner-named", policy=RULE)
    assert platform.call("policy_version", id="owner-named", version=1)["status"] == "published"


def test_discarding_needs_a_draft(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="owner-named has no draft$"):
        platform.call("discard_policy_draft", id="owner-named")


def test_a_never_published_policy_that_is_assigned_isnt_deleted_with_its_draft(platform):
    platform.call("create_policy", policy=RULE)
    sql(platform, "INSERT INTO {policy_assignment} (id, policy_id, scope, target, settings) VALUES (?, ?, ?, ?, ?)",
        ("owner-named|enterprise|*", "owner-named", "enterprise", "*", "{}"))
    with pytest.raises(core.PlatformError, match="is assigned somewhere; remove its assignments first"):
        platform.call("discard_policy_draft", id="owner-named")
    assert platform.call("get_policy", id="owner-named")["draft_version"] == 1


def test_publishing_needs_a_draft(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="owner-named has no draft to publish"):
        platform.call("publish_policy", id="owner-named")


def test_publishing_a_draft_whose_version_row_is_gone_is_refused(platform):
    platform.call("create_policy", policy=RULE)
    sql(platform, "DELETE FROM {policy_version} WHERE id = ?", ("owner-named@1",))
    with pytest.raises(core.PlatformError, match="owner-named has no draft to publish"):
        platform.call("publish_policy", id="owner-named")


def test_a_status_change_needs_a_published_version(platform):
    platform.call("create_policy", policy=RULE)
    with pytest.raises(core.PlatformError, match="was never published: discard its draft instead"):
        platform.call("set_policy_status", id="owner-named", status="deprecated")


def test_setting_the_status_it_already_has_changes_nothing(platform):
    before = published(platform)
    after = platform.call("set_policy_status", id="owner-named", status="published")
    assert after["status"] == "published" and after["history"] == before["history"]


def test_a_version_that_isnt_there_is_refused(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="owner-named has no version 7"):
        platform.call("policy_version", id="owner-named", version=7)


# --------------------------------------------------------------------------- exceptions


def test_a_year_from_29_february_is_28_february():
    assert policy._one_year_from(dt.date(2028, 2, 29)) == dt.date(2029, 2, 28)
    assert policy._one_year_from(dt.date(2027, 3, 1)) == dt.date(2028, 3, 1)


def exception_request(platform, **detail):
    until = (core._today() + dt.timedelta(days=30)).isoformat()
    return platform.call("request", kind="exception", target="", reason="A masked copy isn't ready",
                         detail={"policy": "owner-named", "scope": "project", "scope_target": "crm", "until": until,
                                 **detail})


def test_an_exception_to_an_unpublished_or_retired_policy_isnt_asked_for(platform):
    platform.call("create_policy", policy=RULE)
    with pytest.raises(core.PlatformError, match="owner-named isn't a published policy, so it needs no exception"):
        exception_request(platform)
    platform.call("publish_policy", id="owner-named")
    platform.call("set_policy_status", id="owner-named", status="retired", note="Replaced")
    with pytest.raises(core.PlatformError, match="isn't a published policy"):
        exception_request(platform)


def test_an_exception_needs_an_end_date(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="Say until when: an exception ends on its date"):
        exception_request(platform, until="")


def test_an_exception_that_would_already_have_ended_isnt_granted(platform):
    published(platform)
    past = (core._today() - dt.timedelta(days=1)).isoformat()
    req = {"id": "r1", "reason": "x", "requested_by": OWNER,
           "detail": {"policy": "owner-named", "scope": "project", "scope_target": "crm", "until": past}}
    with platform._db() as db, pytest.raises(core.PlatformError, match=f"would have ended on {past}: decline it"):
        policy._approve_exception(db, req, ADMIN)
    assert platform.call("list_policy_exceptions") == []


def test_a_platform_without_the_exception_table_has_no_exceptions(platform):
    with platform._db() as db:
        db.run(f"DROP TABLE {db.t('catalog', 'policy_exception')}")
        db.commit()
    assert platform.call("list_policy_exceptions", active=True) == []


# --------------------------------------------------------------------------- assignments


@pytest.mark.parametrize(("scope", "target", "message"), [
    ("domain", "nowhere", "domain nowhere isn't one of your organization's domains"),
    ("project", "  ", "Say which project the policy applies to"),
    ("entity", "crm", "An entity is named <project>:<entity>"),
])
def test_an_assignment_to_a_place_that_isnt_one_is_refused(platform, scope, target, message):
    published(platform)
    with pytest.raises(core.PlatformError, match=message):
        platform.call("save_assignment", policy="owner-named", scope=scope, target=target)


def test_an_assignment_takes_only_its_settings_and_the_policys_parameters(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="owner, rule can't be set on an assignment; it takes enforcement"):
        platform.call("save_assignment", policy="owner-named", scope="enterprise", settings={"rule": "x", "owner": "y"})
    with pytest.raises(core.PlatformError, match="max_length isn't one of this policy's parameters"):
        platform.call("save_assignment", policy="owner-named", scope="enterprise", settings={"params": {"max_length": 3}})
    assert platform.call("list_assignments", policy="owner-named") == []


def test_an_entity_assignment_applies_below_its_project_only_for_that_entity(platform):
    published(platform)
    platform.call("save_assignment", policy="owner-named", scope="entity", target="crm:silver.customer",
                  settings={"enforcement": "block"})
    one = platform.call("resolve_policies", project="crm", entity="silver.customer")
    assert [(c["scope"], c["target"]) for c in one["chain"]] == [
        ("enterprise", "*"), ("domain", "sales"), ("project", "crm"), ("entity", "crm:silver.customer")]
    [p] = one["policies"]
    assert p["enforcement"] == "block" and p["introduced_at"] == "entity crm:silver.customer"
    assert platform.call("resolve_policies", project="crm", entity="silver.orders")["policies"] == []


def test_removing_an_assignment_that_isnt_there_is_refused(platform):
    published(platform)
    with pytest.raises(core.PlatformError, match="owner-named isn't assigned to project crm"):
        platform.call("remove_assignment", policy="owner-named", scope="project", target="crm")


def test_a_domain_is_resolved_without_a_project(platform):
    published(platform)
    platform.call("save_assignment", policy="owner-named", scope="domain", target="sales")
    out = platform.call("resolve_policies", domain="sales")
    assert out["chain"] == [{"scope": "enterprise", "target": "*"}, {"scope": "domain", "target": "sales"}]
    assert [p["id"] for p in out["policies"]] == ["owner-named"] and out["project"] == ""
    assert platform.call("resolve_policies", project="crm")["chain"][-1] == {"scope": "project", "target": "crm"}


# --------------------------------------------------------------------------- what counts as weaker


@pytest.mark.parametrize(("field", "how", "old", "new", "weaker"), [
    ("version", None, 3, 2, True), ("version", None, 2, 3, False),
    ("params", "higher", 10, 5, True), ("params", "higher", 10, 20, False),
    ("params", "lower", 10, 20, True), ("params", "lower", 10, 5, False),
    ("params", "subset", ["a"], ["a", "b"], True), ("params", "subset", ["a", "b"], ["a"], False),
    ("params", "superset", ["a", "b"], ["a"], True), ("params", "superset", ["a"], ["a", "b"], False),
    ("params", "true", True, False, True), ("params", "true", False, True, False),
    ("params", "fixed", "x", "y", True), ("params", "fixed", "x", "x", False),
])
def test_what_counts_as_weaker(field, how, old, new, weaker):
    spec = {"stricter": how} if how else None
    assert policy._weaker(field, spec, old, new) is weaker


def snap(versions: int = 2, mandatory: bool = False):
    d = policy.clean_definition(dict(RULE, mandatory=mandatory))
    return {"policies": {"owner-named": {"current_version": str(versions), "status": "published"}},
            "versions": {f"owner-named@{n}": {"version": n, "definition": d, "hash": f"h{n}", "intact": True}
                         for n in range(1, versions + 1)},
            "assignments": [{"policy_id": "owner-named", "scope": "enterprise", "target": "*",
                             "settings": {"version": 1}}]}


CHAIN = [("enterprise", "*"), ("project", "crm")]


def test_a_project_may_pin_a_newer_version_than_the_enterprise():
    out = policy.resolve(snap(), CHAIN, local=[{"policy": "owner-named", "scope": "project", "target": "crm",
                                               "settings": {"version": 2}}], today="2027-01-01")
    [p] = out["policies"]
    assert p["version"] == 2 and p["hash"] == "h2" and out["conflicts"] == []
    assert [s["change"] for s in p["steps"]] == ["introduced", "version"]
    assert p["steps"][1] == {"scope": "project", "target": "crm", "origin": "local", "change": "version",
                                  "from": 1, "to": 2, "outcome": "applied"}


def test_under_a_mandatory_policy_an_older_version_is_ignored():
    s = snap(mandatory=True)
    s["assignments"][0]["settings"] = {"version": 2}
    out = policy.resolve(s, CHAIN, local=[{"policy": "owner-named", "scope": "project", "target": "crm",
                                           "settings": {"version": 1}}], today="2027-01-01")
    assert out["policies"][0]["version"] == 2
    assert out["conflicts"][0]["message"].startswith("owner-named is mandatory from enterprise: version can't go from 2 to 1")


def test_a_parameter_the_version_doesnt_have_is_a_conflict_and_changes_nothing():
    out = policy.resolve(snap(), CHAIN, local=[{"policy": "owner-named", "scope": "project", "target": "crm",
                                               "settings": {"params": {"max_length": 3, "min_rows": 5}}}],
                         today="2027-01-01")
    assert [c["message"] for c in out["conflicts"]] == ["owner-named version 1 has no parameter max_length"]
    assert out["policies"][0]["params"]["min_rows"] == 5 and "max_length" not in out["policies"][0]["params"]


# --------------------------------------------------------------------------- templates


def test_template_authors_are_admins_on_a_platform_with_no_settings_table():
    class NoSettings:
        def exists(self, schema, table):
            return False
    assert policy._template_authors(NoSettings()) == "admins"


def test_a_template_that_isnt_there_is_refused(platform):
    with pytest.raises(core.PlatformError, match="There's no template not-here"):
        platform.call("delete_policy_template", id="not-here")
    with pytest.raises(core.PlatformError, match="There's no template not-here"):
        platform.call("use_policy_template", template="not-here")


def test_saving_a_policy_as_a_template_takes_a_new_name(platform):
    published(platform)
    t = platform.call("save_policy_as_template", policy="owner-named", name="  Owners everywhere  ")
    assert t["id"] == "owner-named-template" and t["name"] == "Owners everywhere"
    assert t["definition"]["params"]["min_rows"]["value"] == 1


def test_a_policy_whose_version_row_is_gone_cant_be_saved_as_a_template(platform):
    published(platform)
    sql(platform, "DELETE FROM {policy_version} WHERE policy_id = ?", ("owner-named",))
    with pytest.raises(core.PlatformError, match="owner-named has no version to copy"):
        platform.call("save_policy_as_template", policy="owner-named")
    assert [t["id"] for t in platform.call("list_policy_templates")["templates"] if not t["builtin"]] == []
