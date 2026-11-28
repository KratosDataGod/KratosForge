"""L3 builds its queries from the sides L2 read (perf follow-up of OSSIE-012b): each side is read and converted
once, and the report is the same as when L3 read both sides again, and the same on every run."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

import ossie_corpus as oc
import ossie_large as ol
from fabric_desktop import semantic_parity as sp
from fabric_desktop import semantic_parity_l3 as l3
from test_semantic_parity_l2 import _model, _ossie

KINDS = {"view": "semantic_view", "ossie": "ossie", "tmsl": "tmsl", "tmdl": "tmdl"}


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _pairs() -> dict[str, tuple[Any, Any, str | None, str | None]]:
    out: dict[str, tuple[Any, Any, str | None, str | None]] = {}
    for e in oc.manifest():
        value = oc.load(e)
        out[e["path"].rsplit("/", 1)[-1]] = (value, value, KINDS[e["kind"]], KINDS[e["kind"]])
    rich = {e["path"]: oc.load(e) for e in oc.manifest() if "rich" in e["path"]}
    out["tmsl_vs_ossie"] = (rich["tmsl/02_rich.bim"], rich["ossie/v02_03_rich.yaml"], "tmsl", "ossie")
    out["view_vs_tmsl"] = (rich["tmsl/02_rich.bim"], oc.load(oc.entry("view/sales_view.yaml")), "tmsl", "semantic_view")
    out["large"] = (ol.bim(2, 6, 8), ol.ossie(2, 6, 8), None, None)
    bim = _model("SUM(Sales[amount])")
    out["sql_vs_dax"] = (bim, _ossie(bim, "SUM(amount)"), None, None)
    out["sql_differs"] = (_ossie(bim, "SUM(amount)"), _ossie(bim, "MAX(amount)"), None, None)
    out["unreadable"] = ("{", bim, "ossie", None)
    return out


PAIRS = _pairs()


def _json(report: dict[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, default=repr)


def _read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """L3 as it was: L2's report, and both sides read again."""
    real = sp._parity_l2_sides

    def fresh(left: Any, right: Any, left_kind: str | None = None,
              right_kind: str | None = None) -> tuple[dict[str, Any], list[Any]]:
        report, _ = real(left, right, left_kind, right_kind)
        sides = [sp._Side("left"), sp._Side("right")]
        if not report["error"]:
            for side, value, kind in ((sides[0], left, left_kind), (sides[1], right, right_kind)):
                sp._read(side, value, kind)
        return report, sides

    monkeypatch.setattr(sp, "_parity_l2_sides", fresh)


@pytest.mark.parametrize("name", list(PAIRS))
def test_the_report_is_the_same_as_when_l3_read_both_sides_again(name, monkeypatch):
    left, right, lk, rk = PAIRS[name]
    reused = l3.parity_l3(left, right, left_kind=lk, right_kind=rk)
    with monkeypatch.context() as m:
        _read_again(m)
        again = l3.parity_l3(left, right, left_kind=lk, right_kind=rk)
    assert _json(reused) == _json(again)


@pytest.mark.parametrize("name", ["tmsl_vs_ossie", "view_vs_tmsl", "large", "sql_vs_dax", "sql_differs"])
def test_the_report_is_the_same_on_every_run(name):
    left, right, lk, rk = PAIRS[name]
    first = _json(l3.parity_l3(left, right, left_kind=lk, right_kind=rk))
    assert all(_json(l3.parity_l3(left, right, left_kind=lk, right_kind=rk)) == first for _ in range(2))


def test_some_measures_ran_so_the_comparison_means_something():
    left, right, lk, rk = PAIRS["large"]
    report = l3.parity_l3(left, right, left_kind=lk, right_kind=rk)
    assert any(b["ran"] for o in report["objects"] for b in (o.get("behavior") or {}).values())


def test_each_side_is_read_and_converted_once(monkeypatch):
    left, right, _, _ = PAIRS["large"]
    reads: list[str] = []
    real = sp._read

    def counted(side: Any, value: Any, kind: str | None) -> None:
        reads.append(side.which)
        real(side, value, kind)

    monkeypatch.setattr(sp, "_read", counted)
    l3.parity_l3(left, right)
    assert reads == ["left", "right"]
