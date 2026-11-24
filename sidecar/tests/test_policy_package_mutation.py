"""Mutation tests for central/policy_package.py: the behaviour its other tests left loose. The format version a
package is written with, the hash of text outside ASCII, which policies and exceptions a package carries, the
context an offline check is filtered by, and the age shown at the minute, hour and day boundaries. Built from
plain answers, as the platform gives them; no platform is called."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest

from fabric_desktop.central import policy_package as pp
from fabric_desktop.central.policy import POLICY_CATEGORIES, clean_definition, definition_hash

CATEGORY = next(iter(POLICY_CATEGORIES))
NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)


def _definition(name, **extra):
    return clean_definition({"name": name, "category": CATEGORY, "rule": f"{name} rule", **extra})


def _snapshot(*pids, applies=None):
    policies, versions = {}, {}
    for pid in pids:
        d = _definition(pid, applies_to=applies or {})
        policies[pid] = {"id": pid, "name": pid, "status": "published", "current_version": 1}
        versions[f"{pid}@1"] = {"version": 1, "definition": d, "hash": definition_hash(d)}
    return {"policies": policies, "versions": versions,
            "assignments": [{"policy_id": pid, "scope": "enterprise", "target": ""} for pid in pids]}


def _resolved(*pids, inactive=()):
    snap = _snapshot(*pids)
    return {"chain": [{"scope": "enterprise", "target": ""}],
            "policies": [{"id": p, "name": p, "version": 1, "hash": snap["versions"][f"{p}@1"]["hash"]} for p in pids],
            "inactive": [{"id": p} for p in inactive]}


def test_a_package_is_written_with_format_version_one_and_a_version_one_package_is_read():
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), _snapshot("p1"), [], taken_at="2026-11-20T10:00:00+00:00")
    assert package["format_version"] == pp.FORMAT_VERSION == 1
    assert pp.verify(package) is package


def test_the_package_hash_reads_text_outside_ascii_as_json_escapes():
    package = {"project": "Café", "sha256": "ignored"}
    want = hashlib.sha256(b'{"project":"Caf\\u00e9"}').hexdigest()
    assert pp.package_hash(package) == want


def test_a_package_carries_resolved_and_inactive_policies_even_when_not_assigned_on_the_chain():
    snap = _snapshot("p1", "p2", "p3", "p4")
    snap["assignments"] = []  # named by governance.toml or the resolver only, not by a platform assignment
    package = pp.take("crm", {"kind": "local"}, _resolved("p1", inactive=("p3",)), snap, [])
    assert sorted(package["policies"]) == ["p1", "p3"]
    assert sorted(package["versions"]) == ["p1@1", "p3@1"]


@pytest.mark.parametrize(("exception", "kept"), [
    ({"id": "x1", "policy_id": "p1"}, True),
    ({"id": "x2", "policy_id": "p1", "active": True}, True),
    ({"id": "x3", "policy_id": "other"}, False),        # for a policy the package doesn't carry
    ({"id": "x4", "policy_id": "p1", "active": False}, False),
    ({"id": "x5", "policy_id": "p1", "ended_at": "2026-11-19"}, False),
])
def test_only_active_unended_exceptions_of_the_packages_policies_are_carried(exception, kept):
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), _snapshot("p1"), [exception])
    assert [e["id"] for e in package["exceptions"]] == ([exception["id"]] if kept else [])


def test_an_offline_check_is_filtered_by_its_context():
    snap = _snapshot("p1", applies={"environments": ["prod"]})
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), snap, [])
    dev = pp.resolve_offline(package, today="2026-11-20", context={"environment": "dev"})
    assert dev["policies"] == [] and dev["inactive"][0]["reason"] == "applies to environment prod only"
    assert dev["project"] == "crm"
    prod = pp.resolve_offline(package, today="2026-11-20", context={"environment": "prod"})
    assert [p["id"] for p in prod["policies"]] == ["p1"]
    assert [p["id"] for p in pp.resolve_offline(package, today="2026-11-20")["policies"]] == ["p1"]


@pytest.mark.parametrize(("minutes", "shown"), [
    (59, "59 min"), (60, "1 h"), (61, "1 h"),
    (48 * 60 - 1, "47 h"), (48 * 60, "2 days"), (49 * 60, "2 days"), (72 * 60, "3 days"),
])
def test_age_turns_to_hours_at_an_hour_and_to_days_at_two_days(minutes, shown):
    taken = (NOW - dt.timedelta(minutes=minutes)).isoformat()
    assert pp.age(taken, NOW) == shown


def test_a_package_on_disk_round_trips(tmp_path):
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), _snapshot("p1"), [])
    path = tmp_path / "p.json"
    path.write_text(json.dumps(package))
    assert pp.read(path)["sha256"] == package["sha256"]


def test_the_package_hash_doesnt_depend_on_key_order():
    assert pp.package_hash({"b": 1, "a": {"y": 2, "x": 3}}) == pp.package_hash({"a": {"x": 3, "y": 2}, "b": 1})


def test_a_version_that_doesnt_match_its_hash_names_its_policy():
    snap = _snapshot("p1")
    snap["versions"]["p1@1"]["hash"] = "0" * 64
    with pytest.raises(pp.PackageError, match="^Version 1 of p1 doesn't match its stored SHA-256 in the platform"):
        pp.take("crm", {"kind": "local"}, _resolved("p1"), snap, [])


def test_a_changed_package_shows_twelve_characters_of_each_hash():
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), _snapshot("p1"), [])
    stored = package["sha256"]
    package["project"] = "other"
    actual = pp.package_hash(package)
    with pytest.raises(pp.PackageError, match=rf"its SHA-256 is {actual[:12]}\.\.\., not the {stored[:12]}\.\.\. it was"):
        pp.verify(package, "Pkg")
    package["sha256"] = ""
    with pytest.raises(pp.PackageError, match=r"not the missing\.\.\. it was taken with"):
        pp.verify(package, "Pkg")


def _on_disk(tmp_path, taken_at):
    package = pp.take("crm", {"kind": "local"}, _resolved("p1"), _snapshot("p1"), [], taken_at=taken_at)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(package))
    return path


def test_a_maximum_age_of_zero_takes_a_package_taken_now(tmp_path):
    assert pp.read(_on_disk(tmp_path, NOW.isoformat()), max_age_days=0, now=NOW)["project"] == "crm"


def test_a_package_exactly_the_maximum_age_is_still_used(tmp_path):
    path = _on_disk(tmp_path, (NOW - dt.timedelta(days=7)).isoformat())
    assert pp.read(path, max_age_days=7, now=NOW)["project"] == "crm"
    with pytest.raises(pp.PackageError, match="more than the 6.9 days allowed"):
        pp.read(path, max_age_days=6.9, now=NOW)
