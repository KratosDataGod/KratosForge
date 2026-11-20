"""The parity program's plan per area (parity.PLANS): every area has one, it never claims more than the
cells show, its backlog items exist in the backlog, and docs/parity.md shows the same table."""

import re
from pathlib import Path

import pytest

from fabric_desktop import parity

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "parity.md"
BACKLOG = ROOT / "docs" / "product" / "platform-parity-backlog.md"
BEGIN, END = "<!-- BEGIN GENERATED: parity-plan -->\n", "<!-- END GENERATED: parity-plan -->"
_DONE = ("partial", "built", "verified")


def test_every_area_has_exactly_one_plan():
    assert set(parity.PLANS) == {(a.platform, a.id) for a in parity.AREAS}


def test_the_docs_plan_table_matches_the_code():
    text = DOC.read_text(encoding="utf-8")
    assert text[text.index(BEGIN) + len(BEGIN):text.index(END)] == parity.plan_table()


def test_every_backlog_item_a_plan_names_is_in_the_backlog():
    headings = set(re.findall(r"^### ((?:SF|DBX|FAB|PAR)-\d{3})\b", BACKLOG.read_text(encoding="utf-8"), re.M))
    named = {i for p in parity.PLANS.values() for i in p.items}
    assert named and named <= headings, sorted(named - headings)


@pytest.mark.parametrize("key", sorted(parity.PLANS), ids=lambda k: f"{k[0]}-{k[1]}")
def test_a_plans_mode_never_says_more_than_its_cells(key):
    area = next(a for a in parity.AREAS if (a.platform, a.id) == key)
    cloud = area.cell("cloud_api").status in _DONE
    local = area.cell("local").status in _DONE
    mode = parity.PLANS[key].mode
    expected = {(True, True): {"hybrid"}, (True, False): {"cloud"}, (False, True): {"local", "simulated"},
                (False, False): {"none"}}[(cloud, local)]
    assert mode in expected


def test_snowflake_on_this_pc_is_only_ever_simulated():
    modes = {a.id: parity.PLANS[(a.platform, a.id)].mode for a in parity.AREAS if a.platform == "snowflake"}
    assert "local" not in modes.values() and "hybrid" not in modes.values()
    assert modes["worksheets"] == "simulated"


@pytest.mark.parametrize("kwargs, message", [
    (dict(family="Z", priority="P0", mode="none"), "unknown family"),
    (dict(family="A", priority="P9", mode="none"), "unknown priority"),
    (dict(family="A", priority="P0", mode="remote"), "unknown mode"),
    (dict(family="A", priority="P0", mode="none", items=("SF-1",)), "look like SF-001"),
    (dict(family="A", priority="P0", mode="none", reviewed="19 Nov"), "reviewed is a date"),
])
def test_a_plan_refuses_values_it_doesnt_know(kwargs, message):
    with pytest.raises(ValueError, match=message):
        parity.Plan(**kwargs)


def test_the_matrix_carries_each_areas_plan():
    out = parity.matrix()
    assert out["families"] == parity.FAMILIES and out["modes"] == parity.MODES
    area = next(a for a in out["platforms"]["snowflake"]["areas"] if a["id"] == "connect")
    assert area["plan"]["items"] == ["SF-001", "SF-002", "PAR-003"] and area["plan"]["priority"] == "P0"
