"""Fuzz a policy package file (central/policy_package.py): torn, not UTF-8, NUL bytes, deep nesting, huge numbers,
fields of the wrong type, missing and extra parts, each as written and with the package's own SHA-256 recomputed
over the damage (what a careless tool or someone hiding an edit would leave). Every case is either refused with
PackageError, by name, or read as a package whose offline check runs; nothing else is raised, and a changed
package is never used as if whole. Seeded, so a failure repeats."""

from __future__ import annotations

import json
import random

import pytest

from fabric_desktop.central import policy_package as pp

from tests.settings_fuzz import ODD_VALUES, mutations
from test_policy_package import mark_pii, package_file, publish, svc  # noqa: F401 - fixtures

CASES = 120
PARTS = ("project", "taken_at", "platform", "chain", "resolved", "policies", "versions", "assignments", "exceptions",
         "format_version")


@pytest.fixture
def good(svc):  # noqa: F811
    mark_pii(svc)
    publish(svc, "sensitive-columns-labelled", "labels")
    svc.governance_check_now()
    return json.loads(package_file(svc).read_text(encoding="utf-8"))


def _use(path):
    """Read, verify and run the offline check, as Check now does: a package or PackageError, nothing else."""
    try:
        package = pp.read(path, max_age_days=10_000)
    except pp.PackageError as exc:
        assert str(exc) and str(path) in str(exc) or "pinned" in str(exc), exc
        return None
    pp.summary(package, path)
    pp.resolve_offline(package)
    return package


def test_any_damaged_package_file_is_refused_by_name_and_never_used(good, tmp_path):
    path = tmp_path / "crm.json"
    canonical = json.dumps(good).encode("utf-8")
    for label, raw in mutations(good, 21, CASES):
        path.write_bytes(raw)
        got = _use(path)
        if got is not None:  # only a package whose hashes all match is used: none of the damage is in force
            assert pp.package_hash(got) == got["sha256"], label
            assert json.dumps(got).encode("utf-8") == canonical or got == good, label


def _junk_inside(rng, value, depth=0):
    """``value`` with one part somewhere inside it swapped for a value of another type."""
    if isinstance(value, dict) and value and depth < 4 and rng.random() < 0.7:
        k = rng.choice(sorted(value))
        return dict(value, **{k: _junk_inside(rng, value[k], depth + 1)})
    if isinstance(value, list) and value and depth < 4 and rng.random() < 0.7:
        i = rng.randrange(len(value))
        return [*value[:i], _junk_inside(rng, value[i], depth + 1), *value[i + 1:]]
    return rng.choice(ODD_VALUES[:10] + ODD_VALUES[11:] + [[{}], {"x": []}, "2020-13-45"])


@pytest.mark.parametrize("seed", [31, 32])
def test_a_package_with_junk_and_a_recomputed_hash_never_raises_anything_else(good, tmp_path, seed):
    rng = random.Random(seed)
    path = tmp_path / "crm.json"
    for case in range(CASES):
        bad = dict(good)
        for part in rng.sample(PARTS, rng.randint(1, 2)):
            if rng.random() < 0.15:
                bad.pop(part, None)
            else:
                bad[part] = _junk_inside(rng, good[part])
        bad["sha256"] = pp.package_hash(bad)
        path.write_text(json.dumps(bad), encoding="utf-8")
        try:
            _use(path)
        except pp.PackageError:  # raised by resolve_offline's checks: still by name
            pass
        except Exception as exc:  # noqa: BLE001 - the point of the test
            pytest.fail(f"seed {seed} case {case}: {type(exc).__name__}: {exc}")
