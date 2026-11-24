"""Policy packages (central/policy_package.py), the paths the other tests leave: a Fabric platform's label and
ids, a platform that names no scopes, a package from a newer app, a version or a resolved policy that doesn't
match, a part missing, a file that isn't there, a maximum age that isn't a number of days, and a package that
doesn't say when it was taken. Built from plain answers, as the platform gives them; no platform is called."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from fabric_desktop.central import policy_package as pp
from fabric_desktop.central.policy import definition_hash

DEFINITION = {"rule": "no PII without a label", "severity": "high"}
CHAIN = [{"scope": "enterprise", "target": ""}]
NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)


def _package(**changes):
    resolved = {"chain": CHAIN, "policies": [{"id": "p1", "name": "Labels", "version": 2,
                                              "hash": definition_hash(DEFINITION)}]}
    snapshot = {"policies": {"p1": {"id": "p1", "name": "Labels", "status": "published", "current_version": 2}},
                "versions": {"p1@2": {"version": 2, "definition": DEFINITION, "hash": definition_hash(DEFINITION)}},
                "assignments": [{"policy_id": "p1", "scope": "enterprise", "target": ""}]}
    out = pp.take("crm", {"kind": "local", "name": "platform.db"}, resolved, snapshot, [],
                  taken_at="2026-11-20T10:00:00+00:00")
    out.update(changes)
    out["sha256"] = pp.package_hash(out)
    return out


def test_a_fabric_platform_is_named_by_its_ids_and_a_missing_one_shows_as_a_question_mark():
    catalog = SimpleNamespace(kind="fabric", workspace_id="ws1", functions_id=None)
    platform = pp.platform_of(catalog)
    assert platform == {"kind": "fabric", "workspace_id": "ws1", "functions_id": ""}
    assert pp.platform_label(platform) == "UDF_Platform ? in workspace ws1"
    assert pp.platform_label({"kind": "fabric", "workspace_id": "w", "functions_id": "f"}) == "UDF_Platform f in workspace w"
    assert pp.platform_of(SimpleNamespace(path=None)) == {"kind": "local", "name": ""}
    assert pp.platform_label({"kind": "local"}) == "the platform on this PC (SQLite)"


def test_a_platform_that_names_no_scopes_gives_no_package():
    with pytest.raises(pp.PackageError, match="^The platform didn't say which scopes the project inherits from"):
        pp.take("crm", {"kind": "local"}, {"chain": []}, {}, [])


def test_a_package_from_a_newer_app_is_refused():
    with pytest.raises(pp.PackageError, match=r"was made by a newer KratosForge \(format 2\)"):
        pp.verify(_package(format_version=2))
    with pytest.raises(pp.PackageError, match=r"\(format '1'\)"):
        pp.verify(_package(format_version="1"))


def test_a_version_whose_definition_was_edited_with_the_package_hash_recomputed_is_refused():
    package = _package()
    package["versions"]["p1@2"]["definition"] = dict(DEFINITION, severity="low")
    package["sha256"] = pp.package_hash(package)
    with pytest.raises(pp.PackageError, match="^Pkg: version 2 of p1 doesn't match its stored SHA-256. It isn't used."):
        pp.verify(package, "Pkg")


def test_a_resolved_policy_not_in_the_package_as_resolved_is_refused():
    package = _package(resolved=[{"id": "p1", "name": "Labels", "version": 3, "sha256": "x"}])
    with pytest.raises(pp.PackageError, match="^Pkg: p1 version 3 isn't in it with the SHA-256 it was resolved with"):
        pp.verify(package, "Pkg")
    package = _package(resolved=[{"id": "p1", "name": "Labels", "version": 2, "sha256": "other"}])
    with pytest.raises(pp.PackageError, match="p1 version 2 isn't in it"):
        pp.verify(package, "Pkg")


def test_a_package_missing_parts_names_them():
    package = _package()
    for part in ("exceptions", "assignments"):
        del package[part]
    package["sha256"] = pp.package_hash(package)
    with pytest.raises(pp.PackageError, match=r"^Pkg is missing a part \(KeyError: 'assignments, exceptions'\)"):
        pp.verify(package, "Pkg")


def test_a_package_file_that_isnt_there_is_said_so(tmp_path):
    with pytest.raises(pp.PackageError, match=r"^The policy package .*gone\.json isn't there\.$"):
        pp.read(tmp_path / "gone.json")


@pytest.mark.parametrize("days", [-1, float("nan"), float("inf")])
def test_a_maximum_age_that_isnt_a_number_of_days_is_refused(tmp_path, days):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(_package()))
    with pytest.raises(pp.PackageError, match="^The maximum age must be a number of days, at least 0, not "):
        pp.read(path, max_age_days=days, now=NOW)


def test_a_package_that_doesnt_say_when_it_was_taken_has_no_age_to_check(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(_package(taken_at="last week")))
    with pytest.raises(pp.PackageError, match="doesn't say when it was taken, so its age can't be checked"):
        pp.read(path, where="Pkg", max_age_days=7, now=NOW)
    assert pp.read(path, where="Pkg")["taken_at"] == "last week", "without a maximum age it is used"
    assert pp.days_old("last week") is None and pp.age("last week") == "of unknown age"


def test_a_package_within_its_maximum_age_is_used_and_a_naive_time_counts_as_utc(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(_package(taken_at="2026-11-20T10:00:00")))
    assert pp.read(path, max_age_days=0.1, now=NOW)["project"] == "crm"
    assert pp.age("2026-11-20T10:00:00", NOW) == "2 h"
    with pytest.raises(pp.PackageError, match="was taken 2 h ago, more than the 0.05 days allowed"):
        pp.read(path, max_age_days=0.05, now=NOW)
