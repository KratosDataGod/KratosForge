"""Large models (OSSIE-012b, scenario 18): the generated models of ``ossie_large`` through conversion, the fidelity
report, and L1, L2 and L3 parity, with the work counted rather than timed.

A busy Windows runner misses wall-clock budgets that pass here, so no test times anything. Each test runs a
stage on a model and on one four times as big along one axis (tables, columns per table, or measures per table),
counts the work, and checks it grows no faster than the model, with a little room for fixed costs. A quadratic
step (each object against every other) would grow with the square of the model and fail. The sizes stay small
so the file runs in a few seconds; ``tools/ossie_large_timing.py`` times the big ones.
"""

from __future__ import annotations

import collections
import importlib.util
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ossie_large as ol
from fabric_desktop import model_ossie as mo
from fabric_desktop import semantic_parity as sp
from fabric_desktop import semantic_parity_l3 as l3

BASE = (2, 4, 8)  # tables, columns per table, measures per table
AXES = {"tables": 0, "columns": 1, "measures": 2}
GROWTH = 4
ROOM = 1.25  # work may grow at most this much faster than the model's fastest-growing kind of object


@pytest.fixture(autouse=True)
def _quiet() -> Any:
    logging.getLogger("ossie_microsoft").addHandler(logging.NullHandler())  # its warnings are in the report
    yield


def _sizes(axis: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    big = list(BASE)
    big[AXES[axis]] *= GROWTH
    return BASE, (big[0], big[1], big[2])


def _growth(small: tuple[int, int, int], big: tuple[int, int, int]) -> float:
    """How much the fastest-growing of tables, columns and measures grew: work linear in any of them grows no
    faster. (Relationships grow with the tables, one fewer, so they are left out.)"""
    a, b = ol.counts(*small), ol.counts(*big)
    return max(b[k] / a[k] for k in ("tables", "columns", "measures") if a[k])


class _Counted(dict):  # type: ignore[type-arg]
    """A dict that counts every lookup, so a scan hidden in a lookup loop shows."""

    looked = [0]

    def __contains__(self, key: object) -> bool:
        _Counted.looked[0] += 1
        return super().__contains__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        _Counted.looked[0] += 1
        return super().get(key, default)


class _Work:
    """Counters on the hot helpers: the fidelity report's object table (adds, finds, id and alias lookups), the
    converter calls, L1's pairing and claiming, and L2's expression reading."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.n: collections.Counter[str] = collections.Counter()
        for module, name in ((mo, "_convert"), (sp, "_pair"), (sp, "_claim"), (sp, "_side_form"), (sp, "_dax_tokens"),
                             (sp, "_sql_form")):
            monkeypatch.setattr(module, name, self._wrap(name, getattr(module, name)))
        work = self

        class Objects(mo._Objects):
            def __init__(self) -> None:
                super().__init__()
                self.ids = _Counted()
                self.aliases = _Counted()

            def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                work.n["add"] += 1
                return super().add(*args, **kwargs)

            def find(self, where: str) -> dict[str, Any] | None:
                work.n["find"] += 1
                return super().find(where)

        monkeypatch.setattr(mo, "_Objects", Objects)

    def _wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def counted(*args: Any, **kwargs: Any) -> Any:
            self.n[name] += 1
            return fn(*args, **kwargs)
        return counted

    def run(self, fn: Callable[[], Any]) -> collections.Counter[str]:
        self.n.clear()
        _Counted.looked[0] = 0
        fn()
        out = collections.Counter(self.n)
        out["lookups"] = _Counted.looked[0]
        return out


def _calls(fn: Callable[[], Any]) -> int:
    """Function calls made while ``fn`` runs, Python's and C's (the converter's, the reader's, YAML's, each dict
    lookup by method: everything but operators)."""
    n = [0]

    def profile(frame: Any, event: str, arg: Any) -> None:
        if event in ("call", "c_call"):
            n[0] += 1
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        fn()
    finally:
        sys.setprofile(previous)
    return n[0]


def _fidelity_from_ossie(text: str) -> dict[str, Any]:
    doc = mo.parse(text)
    bim, report = mo.to_tmsl(doc)
    return mo.safe_fidelity(doc, bim, report)


STAGES: dict[str, Callable[[dict[str, Any], str], Any]] = {
    "import": lambda bim, text: _fidelity_from_ossie(text),
    "export": lambda bim, text: mo.export(bim, validate=True),
    "l1": lambda bim, text: sp.parity_l1(bim, text),
    "l2": lambda bim, text: sp.parity_l2(bim, text),
}


def _assert_linear(small: float, big: float, model_growth: float, what: str) -> None:
    assert big <= ROOM * model_growth * max(small, 1), f"{what}: {small} -> {big} on a model {model_growth:.2f}x as big"


def test_a_generated_model_is_the_same_on_every_run_and_both_formats_say_the_same():
    size = (3, 6, 9)
    assert ol.bim(*size) == ol.bim(*size) and ol.ossie(*size) == ol.ossie(*size)
    assert ol.bim(*size, seed=13) != ol.bim(*size)
    report = sp.parity_l1(ol.bim(*size), ol.ossie(*size))
    assert report["summary"] == sp.COMPATIBLE, report["counts"]
    kinds = collections.Counter(o["kind"] for o in report["objects"])
    want = ol.counts(*size)
    assert (kinds["column"] - 2, kinds["measure"], kinds["relationship"], kinds["role"]) == (
        want["columns"], want["measures"], want["relationships"], want["roles"])  # less the calculation group's two
    assert kinds["calculation_group"] == 1
    # L2 reads every measure but the CALCULATE ones, which are outside its subset
    l2 = sp.parity_l2(ol.bim(*size), ol.ossie(*size))
    outside = [o for o in l2["objects"] if o["result"] != sp.COMPATIBLE]
    assert len(outside) == size[0] * (size[2] // 8)
    assert all(o["kind"] == "measure" and "CALCULATE" in " ".join(o["notes"]) for o in outside)
    out = mo.export(ol.bim(*size), validate=True)
    assert out["fidelity"]["validation"]["passed"] is True
    assert out["counts"]["metrics"] == want["measures"] and out["counts"]["fields"] == want["columns"]
    flagged = out["fidelity"]["flagged"]
    assert [r["object"] for r in flagged["roles"]] == [f"role '{ol.ROLE}'"]
    assert [g["object"] for g in flagged["calculation_groups"]] == [f"table '{ol.CALC_GROUP}'"]


def test_a_generated_model_has_at_least_one_table_and_two_columns():
    with pytest.raises(ValueError, match="at least one table"):
        ol.spec(0, 4, 4)


@pytest.mark.parametrize("stage", sorted(STAGES))
@pytest.mark.parametrize("axis", sorted(AXES))
def test_the_hot_helpers_work_grows_with_the_model(monkeypatch, axis, stage):
    """Each object is added once, each report row finds its object with a few lookups, each pair is made once,
    each expression is read once a side, and the converter runs a fixed number of times, whatever the size."""
    work = _Work(monkeypatch)
    small_size, big_size = _sizes(axis)
    growth = _growth(small_size, big_size)
    small = work.run(lambda: STAGES[stage](ol.bim(*small_size), ol.ossie(*small_size)))
    big = work.run(lambda: STAGES[stage](ol.bim(*big_size), ol.ossie(*big_size)))
    assert big["_convert"] == small["_convert"] > 0  # conversions per run don't depend on size
    for n in (small, big):  # each add and find costs a few lookups, never a scan of the objects so far
        assert n["lookups"] <= 3 * (n["add"] + n["find"]), dict(n)
        assert n["_claim"] <= 2 * n["_pair"], dict(n)  # each left object claims once by key, a relationship once by name
    for name in sorted(set(small) | set(big)):
        if name != "_convert":
            _assert_linear(small[name], big[name], growth, name)
    if stage in ("l1", "l2"):
        assert big["_pair"] > small["_pair"] > 0
    if stage == "l2":
        assert big["_side_form"] >= small["_side_form"] > 0


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_every_call_grows_with_the_number_of_tables(stage):
    """All Python calls, the converter's and the libraries' included, so a quadratic step outside the counted
    helpers shows too."""
    small_size, big_size = _sizes("tables")
    growth = _growth(small_size, big_size)
    small_model = (ol.bim(*small_size), ol.ossie(*small_size))
    big_model = (ol.bim(*big_size), ol.ossie(*big_size))
    STAGES[stage](*small_model)  # warm up: first-use imports and caches aren't the model's work
    small = _calls(lambda: STAGES[stage](*small_model))
    big = _calls(lambda: STAGES[stage](*big_model))
    _assert_linear(small, big, growth, f"{stage} calls")


def test_l3_runs_at_most_its_cap_of_measures_however_many_there_are(monkeypatch):
    """L3's DuckDB work is bounded: past ``MAX_MEASURES_RUN`` measures on a side, the rest aren't run and say so."""
    monkeypatch.setattr(l3, "MAX_MEASURES_RUN", 5)
    ran: list[int] = []
    real = l3._run_side

    def run_side(which: str, run: Any, queries: dict[str, str]) -> Any:
        ran.append(len(queries))
        return real(which, run, queries)
    monkeypatch.setattr(l3, "_run_side", run_side)
    size = (2, 4, 4)  # eight measures a side that L3 can run
    report = l3.parity_l3(ol.ossie(*size), ol.ossie(*size))
    assert ran == [5, 5]
    capped = [o for o in report["objects"] if o["kind"] == "measure"
              and "past 5 measures" in o.get("behavior", {}).get("left", {}).get("reason", "")]
    assert capped and all(o["result"] != sp.VERIFIED for o in capped)
    assert sum(o["result"] == sp.VERIFIED for o in report["objects"]) <= 5


def test_the_timing_tool_prints_each_stage_counts_and_versions_as_json(capsys):
    """The tool runs every stage once on a tiny model; its seconds are reported, never checked."""
    path = Path(__file__).resolve().parents[1] / "tools" / "ossie_large_timing.py"
    spec = importlib.util.spec_from_file_location("ossie_large_timing", path)
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.main(["--size", "2x3x8"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["versions"]) >= {"python", "duckdb"} and out["seed"] == ol.SEED
    (run,) = out["runs"]
    assert set(run["seconds"]) == set(tool.STAGES)
    assert run["objects"] == ol.counts(2, 3, 8) and run["results"]["round_trip"] is True
    assert run["results"]["l1"]["summary"] == sp.COMPATIBLE
    with pytest.raises(SystemExit):
        tool.main(["--size", "2x3"])
