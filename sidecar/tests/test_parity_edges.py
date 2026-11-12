"""The parity matrix: an area on a platform it doesn't know or with levels it doesn't have is refused,
and the matrix lists every platform's areas with all five levels."""

import pytest

from fabric_desktop import parity


def test_an_area_on_an_unknown_platform_or_level_is_refused():
    with pytest.raises(ValueError, match="unknown platform 'bigquery'"):
        parity.Area("bigquery", "x", "X", {})
    with pytest.raises(ValueError, match=r"unknown levels \['looks'\]"):
        parity.Area("fabric", "x", "X", {"looks": parity.MISSING})


def test_the_matrix_has_each_platform_with_every_level_of_its_areas():
    out = parity.matrix()
    assert out["levels"] == parity.LEVELS and out["statuses"] == parity.STATUSES
    assert set(out["platforms"]) == set(parity.PLATFORMS)
    for pid, platform in out["platforms"].items():
        assert platform["label"] == parity.PLATFORMS[pid]
        assert len(platform["areas"]) == sum(a.platform == pid for a in parity.AREAS)
        for area in platform["areas"]:
            assert set(area["levels"]) == set(parity.LEVELS)
            assert all(isinstance(cell["tests"], list) for cell in area["levels"].values())
