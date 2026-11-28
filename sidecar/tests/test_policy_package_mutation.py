"""Policy packages: what the first mutation run of central/policy_package.py left alive (batch 346)."""

from __future__ import annotations

import pytest

from fabric_desktop.central import policy_package as pp
from fabric_desktop.central.policy import definition_hash

DEFINITION = {"rule": "columns tagged PII have a label", "severity": "high"}


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def package() -> dict:
    resolved = {"chain": [{"scope": "enterprise", "target": ""}, {"scope": "project", "target": "crm"}],
                "policies": [{"id": "labels", "name": "Labels", "version": 1, "hash": definition_hash(DEFINITION)}]}
    snapshot = {
        "policies": {"labels": {"id": "labels", "name": "Labels", "status": "published", "current_version": 1}},
        "versions": {"labels@1": {"version": 1, "definition": DEFINITION, "hash": definition_hash(DEFINITION)}},
        "assignments": [{"policy_id": "labels", "scope": "enterprise", "target": ""}],
    }
    return pp.take("crm", {"kind": "local", "name": "platform.db"}, resolved, snapshot, [],
                   taken_at="2026-11-20T10:00:00+00:00")


def test_a_package_is_format_1_and_format_1_is_read():
    p = package()
    assert p["format_version"] == 1
    assert pp.verify(p) is p


def test_a_package_missing_parts_names_every_one():
    p = package()
    del p["project"], p["taken_at"]
    p["sha256"] = pp.package_hash(p)
    with pytest.raises(pp.PackageError, match=r"missing a part \(KeyError: 'project, taken_at'\)"):
        pp.verify(p)


def answers(definition: dict, settings: dict | None = None, extra_policy: bool = False):
    h = definition_hash(definition)
    resolved = {"chain": [{"scope": "enterprise", "target": ""}, {"scope": "project", "target": "crm"}],
                "policies": [{"id": "labels", "name": "Labels", "version": 1, "hash": h}]
                + ([{"id": "names", "name": "Names", "version": 1, "hash": h}] if extra_policy else [])}
    policies = {"labels": {"id": "labels", "name": "Labels", "status": "published", "current_version": 1}}
    versions = {"labels@1": {"version": 1, "definition": definition, "hash": h}}
    if extra_policy:  # resolved, but with no assignment on the chain in the snapshot
        policies["names"] = {"id": "names", "name": "Names", "status": "published", "current_version": 1}
        versions["names@1"] = {"version": 1, "definition": definition, "hash": h}
    assignment = {"policy_id": "labels", "scope": "enterprise", "target": ""}
    if settings is not None:
        assignment["settings"] = settings
    return resolved, {"policies": policies, "versions": versions, "assignments": [assignment]}


def take(*args, **kwargs) -> dict:
    resolved, snapshot = answers(*args, **kwargs)
    return pp.take("crm", {"kind": "local"}, resolved, snapshot, [], taken_at="2026-11-20T10:00:00+00:00")


def test_an_assignments_settings_are_kept_and_missing_ones_are_empty():
    assert take(DEFINITION, settings={"min": 2})["assignments"][0]["settings"] == {"min": 2}
    assert take(DEFINITION)["assignments"][0]["settings"] == {}


def test_a_resolved_policy_is_kept_even_without_an_assignment_on_the_chain():
    p = take(DEFINITION, extra_policy=True)
    assert set(p["policies"]) == {"labels", "names"} and "names@1" in p["versions"]


def test_resolving_offline_filters_by_the_context_given():
    from fabric_desktop.central import policy

    d = policy.clean_definition({"name": "Labels", "category": next(iter(policy.POLICY_CATEGORIES)),
                                 "applies_to": {"environments": ["prod"]}})
    p = pp.verify(take(d))
    dev = pp.resolve_offline(p, context={"environment": "dev"}, today="2026-11-20")
    assert dev["policies"] == [] and dev["inactive"][0]["reason"] == "applies to environment prod only"
    assert [x["id"] for x in pp.resolve_offline(p, today="2026-11-20")["policies"]] == ["labels"]


def test_the_package_hash_ignores_key_order_and_writes_text_as_ascii():
    import hashlib

    assert pp.package_hash({"a": 1, "b": "é"}) == pp.package_hash({"b": "é", "a": 1})
    assert pp.package_hash({"b": "é", "sha256": "x"}) == hashlib.sha256(b'{"b":"\\u00e9"}').hexdigest()


def test_a_changed_package_shows_the_first_12_characters_of_both_hashes():
    p = package()
    stored = p["sha256"]
    p["project"] = "other"
    actual = pp.package_hash(p)
    with pytest.raises(pp.PackageError) as e:
        pp.verify(p)
    assert f"its SHA-256 is {actual[:12]}..., not the {stored[:12]}... it was taken with" in str(e.value)
    p["sha256"] = ""
    with pytest.raises(pp.PackageError, match=r"not the missing\.\.\. it was taken with"):
        pp.verify(p)
