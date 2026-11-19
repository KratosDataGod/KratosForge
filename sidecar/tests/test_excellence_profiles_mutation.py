"""Kill tests for excellence/profiles.py (docs/quality/mutation-testing.md, batch 116).

The severity weights, a profile's fingerprint, a profile can't be changed once made, a weight of 0,
the coverage thresholds at their edges, and a stored profile's overrides and disabled controls.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from fabric_desktop.excellence import profiles as pf


def test_the_severity_weights():
    assert pf.SEVERITY_WEIGHTS == {"critical": 10, "high": 5, "medium": 3, "low": 1, "informational": 0}
    assert pf.get().severity_weights == pf.SEVERITY_WEIGHTS


def test_a_fingerprint_is_16_hex_digits_and_follows_the_profile():
    base = pf.get()
    fp = base.fingerprint()
    assert re.fullmatch(r"[0-9a-f]{16}", fp)
    assert dataclasses.replace(base, version="9.9.9").fingerprint() != fp
    assert pf.from_dict(base.as_dict()).fingerprint() == fp


def test_a_profile_cannot_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        pf.get().title = "x"  # type: ignore[misc]


def _with(**over):
    return dataclasses.replace(pf.get(), **over)


def test_a_dimension_may_weigh_nothing():
    w = dict(pf.get().weights)
    w["devops"], w["local_development"] = 0, w["local_development"] + w["devops"]
    assert pf.problems(_with(weights=w)) == []
    w["devops"], w["local_development"] = 0.5, w["local_development"] - 0.5
    assert pf.problems(_with(weights=w)) == []


def test_coverage_thresholds_at_their_edges():
    bad = "coverage thresholds are 0 < provisional <= rated <= 1"
    assert bad in pf.problems(_with(provisional_coverage=0, rated_coverage=0.5))
    assert pf.problems(_with(provisional_coverage=0.6, rated_coverage=0.6)) == []
    assert pf.problems(_with(provisional_coverage=0.5, rated_coverage=1)) == []
    assert bad in pf.problems(_with(provisional_coverage=0.5, rated_coverage=1.01))


def test_a_stored_profile_keeps_its_overrides_and_disabled_controls():
    d = _with(severity_overrides={"FEA-SEC-001": "low"}, disabled={"FEA-SEC-002": "not used here"}).as_dict()
    back = pf.from_dict(d)
    assert back.severity_overrides == {"FEA-SEC-001": "low"}
    assert back.disabled == {"FEA-SEC-002": "not used here"}
