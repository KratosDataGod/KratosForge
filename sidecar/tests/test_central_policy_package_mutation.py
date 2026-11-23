"""Mutation batch 369: central/policy_package.py's taking, checking, reading and ageing of packages."""

import datetime as dt
import hashlib
import json
from types import SimpleNamespace

import pytest

from fabric_desktop.central import policy_package as pp
from fabric_desktop.central.policy import definition_hash

DEF = {"id": "p1", "name": "Owner required", "rule": "entity_owner"}
H = definition_hash(DEF)
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)
CHAIN = [{"scope": "enterprise", "target": "*"}]


def resolved(**kw):
    return {"chain": CHAIN, "policies": [{"id": "p1", "name": "Owner required", "version": 1, "hash": H}], **kw}


def snapshot(**kw):
    return {"policies": {"p1": {"id": "p1", "name": "Owner required", "status": "published", "current_version": 1},
                         "p2": {"id": "p2", "name": "Other", "status": "published", "current_version": 1}},
            "versions": {"p1@1": {"version": 1, "definition": DEF, "hash": H}},
            "assignments": [{"policy_id": "p1", "scope": "enterprise", "target": "*"}], **kw}


def package(exceptions=(), **kw):
    return pp.take("crm", {"kind": "local", "name": "platform.sqlite"}, resolved(**kw), snapshot(), list(exceptions),
                   taken_at="2026-11-21T10:00:00+00:00")


def rehash(p):
    p["sha256"] = pp.package_hash(p)
    return p


def test_a_package_is_format_version_1_and_verifies():
    p = package()
    assert p["format_version"] == 1 == pp.FORMAT_VERSION and pp.verify(p) is p


def test_the_hash_is_over_sorted_keys_and_ascii_json():
    data = {"b": 1, "a": "é", "sha256": "ignored"}
    assert pp.package_hash(data) == hashlib.sha256(b'{"a":"\\u00e9","b":1}').hexdigest()


@pytest.mark.parametrize("platform, label", [
    ({"kind": "fabric", "functions_id": "f1", "workspace_id": "w1"}, "UDF_Platform f1 in workspace w1"),
    ({"kind": "fabric"}, "UDF_Platform ? in workspace ?"),
    ({"kind": "local", "name": "p.sqlite"}, "the platform on this PC (p.sqlite)"),
    ({}, "the platform on this PC (SQLite)")])
def test_the_platform_is_named_by_its_ids_or_question_marks(platform, label):
    assert pp.platform_label(platform) == label


def test_which_platform_a_catalog_talks_to():
    assert pp.platform_of(SimpleNamespace(kind="fabric", workspace_id="w1", functions_id="f1")) == \
        {"kind": "fabric", "workspace_id": "w1", "functions_id": "f1"}
    assert pp.platform_of(SimpleNamespace(kind="fabric", workspace_id=None, functions_id=None)) == \
        {"kind": "fabric", "workspace_id": "", "functions_id": ""}
    assert pp.platform_of(SimpleNamespace(kind="local", path="/x/platform.sqlite")) == {"kind": "local", "name": "platform.sqlite"}
    assert pp.platform_of(SimpleNamespace(kind="local", path=None)) == {"kind": "local", "name": ""}


def test_no_chain_takes_no_package():
    with pytest.raises(pp.PackageError, match="which scopes"):
        pp.take("crm", {}, {"policies": []}, snapshot(), [])


def test_a_resolve_without_policies_or_inactive_still_packages_the_assigned_ones():
    p = pp.take("crm", {}, {"chain": CHAIN}, snapshot(), [], taken_at="2026-11-21T10:00:00+00:00")
    assert list(p["policies"]) == ["p1"] and p["resolved"] == []
    p2 = pp.take("crm", {}, {"chain": CHAIN, "inactive": [{"id": "p2"}]}, snapshot(), [], taken_at="t")
    assert list(p2["policies"]) == ["p1", "p2"]


def test_a_version_that_doesnt_match_names_its_policy():
    snap = snapshot(versions={"p1@1": {"version": 1, "definition": DEF, "hash": "0" * 64}})
    with pytest.raises(pp.PackageError, match="^Version 1 of p1 doesn't match"):
        pp.take("crm", {}, resolved(), snap, [])


def test_only_active_exceptions_that_havent_ended_are_kept():
    p = package([{"id": "e1", "policy_id": "p1"}, {"id": "e2", "policy_id": "p1", "active": False},
                 {"id": "e3", "policy_id": "p1", "ended_at": "2026-11-01"}, {"id": "e4", "policy_id": "p9"}])
    assert [e["id"] for e in p["exceptions"]] == ["e1"]


@pytest.mark.parametrize("version", ["1", None, 2])
def test_a_format_version_that_isnt_a_whole_number_up_to_ours_is_refused(version):
    with pytest.raises(pp.PackageError, match="newer KratosForge"):
        pp.verify(rehash(dict(package(), format_version=version)))


def test_a_changed_package_shows_the_start_of_both_hashes():
    p = package()
    stored = p["sha256"]
    p["project"] = "ledger"
    with pytest.raises(pp.PackageError) as e:
        pp.verify(p)
    assert f"is {pp.package_hash(p)[:12]}..., not the {stored[:12]}..." in str(e.value)
    p["sha256"] = ""
    with pytest.raises(pp.PackageError, match="not the missing\\.\\.\\."):
        pp.verify(p)


def test_a_resolved_policy_whose_version_isnt_in_it_is_refused():
    p = package()
    p["resolved"][0]["version"] = 2
    with pytest.raises(pp.PackageError, match="p1 version 2 isn't in it"):
        pp.verify(rehash(p))


@pytest.mark.parametrize("part", ["project", "taken_at", "exceptions"])
def test_a_missing_part_is_refused_by_name(part):
    p = package()
    del p[part]
    with pytest.raises(pp.PackageError, match=f"missing a part \\(KeyError: '{part}'\\)"):
        pp.verify(rehash(p))


def _write(tmp_path, p, name="crm.json"):
    path = tmp_path / name
    path.write_text(json.dumps(p), encoding="utf-8")
    return path


def test_reading_names_the_file_and_one_that_isnt_there(tmp_path):
    with pytest.raises(pp.PackageError, match=f"^The policy package {tmp_path / 'nope.json'} isn't there"):
        pp.read(tmp_path / "nope.json")
    with pytest.raises(pp.PackageError, match="^Mine isn't there"):
        pp.read(tmp_path / "nope.json", where="Mine")


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_a_maximum_age_must_be_a_number_of_days_at_least_0(tmp_path, bad):
    with pytest.raises(pp.PackageError, match="maximum age must be a number"):
        pp.read(_write(tmp_path, package()), max_age_days=bad, now=NOW)


def test_an_age_limit_of_exactly_the_packages_age_still_reads(tmp_path):
    path = _write(tmp_path, package())
    assert pp.read(path, max_age_days=2 / 24, now=NOW)["project"] == "crm"
    with pytest.raises(pp.PackageError, match="was taken 2 h ago, more than the 0.08 days"):
        pp.read(path, max_age_days=0.08, now=NOW)
    fresh = _write(tmp_path, rehash(dict(package(), taken_at=NOW.isoformat())), "now.json")
    assert pp.read(fresh, max_age_days=0, now=NOW)["project"] == "crm"


def test_a_package_whose_time_cant_be_read_has_no_age(tmp_path):
    path = _write(tmp_path, rehash(dict(package(), taken_at="yesterday")))
    with pytest.raises(pp.PackageError, match="doesn't say when it was taken"):
        pp.read(path, max_age_days=5, now=NOW)


def test_a_time_without_a_zone_is_utc():
    assert pp.days_old("2026-11-21T00:00:00", NOW) == 0.5
    assert pp.days_old("2026-11-21T00:00:00+06:00", NOW) == 0.75


@pytest.mark.parametrize("minutes, words", [(0, "0 min"), (59, "59 min"), (60, "1 h"), (61, "1 h"),
                                            (47 * 60 + 59, "47 h"), (48 * 60, "2 days"), (72 * 60, "3 days")])
def test_age_in_words_at_each_edge(minutes, words):
    assert pp.age((NOW - dt.timedelta(minutes=minutes)).isoformat(), NOW) == words


def test_offline_resolution_passes_the_context_on_and_none_as_empty(monkeypatch):
    seen = []
    monkeypatch.setattr(pp, "resolve", lambda snap, chain, local, context, today, exceptions: seen.append(context) or {})
    p = package()
    assert pp.resolve_offline(p, context={"environment": "prod"}) == {"project": "crm"}
    pp.resolve_offline(p)
    assert seen == [{"environment": "prod"}, {}]
