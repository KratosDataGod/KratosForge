"""Policy packages (GOV-002): the platform labels, the refusals in take, verify and read, and package ages,
checked on packages built here from the platform's answers, with no platform, Fabric or OneLake call."""

import datetime as dt
import json
import types

import pytest

from fabric_desktop.central import policy_package as pp
from fabric_desktop.central.policy import definition_hash

DEFINITION = {"rule": "columns tagged PII have a label", "severity": "high"}
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def answers(definition=DEFINITION, stored_hash=None):
    resolved = {"chain": [{"scope": "enterprise", "target": ""}, {"scope": "project", "target": "crm"}],
                "policies": [{"id": "labels", "name": "Labels", "version": 1, "hash": definition_hash(definition)}]}
    snapshot = {
        "policies": {"labels": {"id": "labels", "name": "Labels", "status": "published", "current_version": 1}},
        "versions": {"labels@1": {"version": 1, "definition": definition,
                                  "hash": stored_hash or definition_hash(definition)}},
        "assignments": [{"policy_id": "labels", "scope": "enterprise", "target": ""}],
    }
    return resolved, snapshot


def package(**changes):
    resolved, snapshot = answers()
    out = pp.take("crm", {"kind": "local", "name": "platform.db"}, resolved, snapshot, [],
                  taken_at="2026-11-20T10:00:00+00:00")
    out.update(changes)
    out["sha256"] = pp.package_hash(out)
    return out


def write(tmp_path, data):
    path = tmp_path / "crm.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---- which platform

def test_a_fabric_platform_is_named_by_its_function_and_workspace_ids():
    assert pp.platform_label({"kind": "fabric", "functions_id": "f1", "workspace_id": "w1"}) == \
        "UDF_Platform f1 in workspace w1"


def test_a_fabric_platform_without_ids_shows_question_marks():
    assert pp.platform_label({"kind": "fabric"}) == "UDF_Platform ? in workspace ?"


def test_a_local_platform_is_named_by_its_file_or_sqlite():
    assert pp.platform_label({"kind": "local", "name": "p.db"}) == "the platform on this PC (p.db)"
    assert pp.platform_label({}) == "the platform on this PC (SQLite)"


def test_a_fabric_catalog_is_described_by_ids_only():
    catalog = types.SimpleNamespace(kind="fabric", workspace_id="w1", functions_id="f1", token="not-kept")
    assert pp.platform_of(catalog) == {"kind": "fabric", "workspace_id": "w1", "functions_id": "f1"}


def test_a_fabric_catalog_without_ids_gives_empty_ids():
    catalog = types.SimpleNamespace(kind="fabric", workspace_id=None, functions_id=None)
    assert pp.platform_of(catalog) == {"kind": "fabric", "workspace_id": "", "functions_id": ""}


def test_a_local_catalog_is_described_by_its_file_name(tmp_path):
    assert pp.platform_of(types.SimpleNamespace(path=tmp_path / "platform.db")) == \
        {"kind": "local", "name": "platform.db"}
    assert pp.platform_of(object()) == {"kind": "local", "name": ""}


# ---- taking a package

def test_no_package_is_taken_without_the_scopes_the_project_inherits():
    _, snapshot = answers()
    with pytest.raises(pp.PackageError, match="didn't say which scopes"):
        pp.take("crm", {}, {"chain": []}, snapshot, [])


def test_no_package_is_taken_when_a_version_doesnt_match_its_stored_hash():
    resolved, snapshot = answers(stored_hash="0" * 64)
    with pytest.raises(pp.PackageError, match="Version 1 of labels doesn't match its stored SHA-256"):
        pp.take("crm", {}, resolved, snapshot, [])


def test_versions_of_policies_outside_the_chain_are_left_out():
    resolved, snapshot = answers()
    snapshot["versions"]["other@1"] = {"version": 1, "definition": {"x": 1}, "hash": "bad"}
    assert list(pp.take("crm", {}, resolved, snapshot, [])["versions"]) == ["labels@1"]


def test_only_active_unended_exceptions_on_its_policies_are_kept():
    resolved, snapshot = answers()
    exceptions = [{"id": "e1", "policy_id": "labels"}, {"id": "e2", "policy_id": "labels", "active": False},
                  {"id": "e3", "policy_id": "labels", "ended_at": "2026-11-01"}, {"id": "e4", "policy_id": "other"}]
    kept = pp.take("crm", {}, resolved, snapshot, exceptions)["exceptions"]
    assert [e["id"] for e in kept] == ["e1"]


def test_a_package_without_a_time_is_stamped_with_now():
    resolved, snapshot = answers()
    taken = pp.take("crm", {}, resolved, snapshot, [])["taken_at"]
    assert dt.datetime.fromisoformat(taken).tzinfo is not None


# ---- verifying

def test_a_whole_package_verifies():
    p = package()
    assert pp.verify(p) is p


@pytest.mark.parametrize("bad", [None, [], {"format": "something-else"}])
def test_anything_but_a_package_is_refused(bad):
    with pytest.raises(pp.PackageError, match="isn't a KratosForge policy package"):
        pp.verify(bad)


@pytest.mark.parametrize("version", [2, "1", None])
def test_a_newer_or_unreadable_format_is_refused(version):
    with pytest.raises(pp.PackageError, match=r"made by a newer KratosForge \(format"):
        pp.verify(package(format_version=version))


def test_a_package_changed_after_it_was_taken_is_refused():
    p = package()
    p["project"] = "other"
    with pytest.raises(pp.PackageError, match="was changed after it was taken"):
        pp.verify(p)


def test_a_package_without_its_own_hash_says_missing():
    p = package()
    del p["sha256"]
    with pytest.raises(pp.PackageError, match=r"not the missing\.\.\."):
        pp.verify(p)


def test_a_version_whose_definition_was_edited_is_refused_even_with_a_recomputed_package_hash():
    p = package()
    p["versions"]["labels@1"]["definition"] = dict(DEFINITION, severity="low")
    p["sha256"] = pp.package_hash(p)
    with pytest.raises(pp.PackageError, match="version 1 of labels doesn't match its stored SHA-256"):
        pp.verify(p)


def test_a_resolved_policy_whose_version_isnt_in_the_package_is_refused():
    p = package(resolved=[{"id": "labels", "name": "Labels", "version": 2, "sha256": definition_hash(DEFINITION)}])
    with pytest.raises(pp.PackageError, match="labels version 2 isn't in it with the SHA-256"):
        pp.verify(p)


def test_a_resolved_policy_with_another_hash_is_refused():
    p = package(resolved=[{"id": "labels", "name": "Labels", "version": 1, "sha256": "f" * 64}])
    with pytest.raises(pp.PackageError, match="labels version 1 isn't in it with the SHA-256"):
        pp.verify(p)


@pytest.mark.parametrize("part", ["project", "taken_at", "platform", "chain", "policies", "assignments", "exceptions"])
def test_a_package_missing_a_part_is_refused_and_names_it(part):
    p = package()
    del p[part]
    p["sha256"] = pp.package_hash(p)
    with pytest.raises(pp.PackageError, match=rf"is missing a part \(KeyError: '{part}'\)"):
        pp.verify(p)


def test_a_package_whose_versions_arent_a_mapping_is_refused():
    with pytest.raises(pp.PackageError, match=r"is missing a part \(AttributeError"):
        pp.verify(package(versions=[]))


# ---- reading a file

def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(pp.PackageError, match="isn't there"):
        pp.read(tmp_path / "none.json")


def test_a_file_that_isnt_json_is_refused_and_left_as_it_is(tmp_path):
    path = tmp_path / "crm.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(pp.PackageError, match=r"can't be read \(JSONDecodeError"):
        pp.read(path)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_default_wording_names_the_file(tmp_path):
    path = tmp_path / "none.json"
    with pytest.raises(pp.PackageError, match="The policy package .*none.json isn't there"):
        pp.read(path)


def test_a_pinned_hash_is_compared_without_case_or_spaces(tmp_path):
    p = package()
    assert pp.read(write(tmp_path, p), expect_sha256=f"  {p['sha256'].upper()} ")["sha256"] == p["sha256"]


def test_a_package_with_another_hash_than_the_pinned_one_is_refused(tmp_path):
    with pytest.raises(pp.PackageError, match="isn't the pinned package"):
        pp.read(write(tmp_path, package()), expect_sha256="a" * 64)


def test_a_pinned_hash_refuses_a_file_that_isnt_an_object(tmp_path):
    with pytest.raises(pp.PackageError, match="its SHA-256 is missing"):
        pp.read(write(tmp_path, [1, 2]), expect_sha256="a" * 64)


@pytest.mark.parametrize("max_age", [-1, float("nan"), float("inf")])
def test_a_maximum_age_that_isnt_a_number_of_days_is_refused(tmp_path, max_age):
    with pytest.raises(pp.PackageError, match="The maximum age must be a number of days, at least 0"):
        pp.read(write(tmp_path, package()), max_age_days=max_age, now=NOW)


def test_a_package_without_a_readable_time_is_refused_when_its_age_is_checked(tmp_path):
    with pytest.raises(pp.PackageError, match="doesn't say when it was taken"):
        pp.read(write(tmp_path, package(taken_at="yesterday")), max_age_days=1, now=NOW)


def test_a_package_older_than_allowed_is_refused(tmp_path):
    with pytest.raises(pp.PackageError, match="was taken 2 h ago, more than the 0.05 days allowed"):
        pp.read(write(tmp_path, package()), max_age_days=0.05, now=NOW)


def test_a_package_within_the_age_is_read(tmp_path):
    assert pp.read(write(tmp_path, package()), max_age_days=1, now=NOW)["project"] == "crm"
    assert pp.read(write(tmp_path, package()), max_age_days=0, now=dt.datetime(2026, 11, 20, 10, 0,
                                                                               tzinfo=dt.timezone.utc))


# ---- ages

def test_a_time_without_a_zone_is_taken_as_utc():
    assert pp.days_old("2026-11-19T12:00:00", NOW) == 1.0


def test_an_unreadable_time_has_no_age():
    assert pp.days_old("soon", NOW) is None
    assert pp.age("soon", NOW) == "of unknown age"


@pytest.mark.parametrize("taken_at, words", [
    ("2026-11-20T12:30:00+00:00", "0 min"),
    ("2026-11-20T11:01:00+00:00", "59 min"),
    ("2026-11-20T11:00:00+00:00", "1 h"),
    ("2026-11-18T12:01:00+00:00", "47 h"),
    ("2026-11-18T12:00:00+00:00", "2 days"),
])
def test_ages_are_said_in_minutes_hours_or_days(taken_at, words):
    assert pp.age(taken_at, NOW) == words


def test_a_summary_names_the_file_platform_age_and_policies(tmp_path):
    p = package()
    assert pp.summary(p, tmp_path / "crm.json", NOW) == {
        "file": str(tmp_path / "crm.json"), "taken_at": "2026-11-20T10:00:00+00:00", "age": "2 h",
        "platform": "the platform on this PC (platform.db)", "sha256": p["sha256"], "policies": 1}
