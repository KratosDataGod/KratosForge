"""Ranks: what the mutation run (batch 124, docs/quality/mutation-testing.md) showed no test pinned.

The ladder's numbers, the top rank having no next one, what the evidence counts, the time span it covers,
the plural in a requirement's text and the exact point at which a requirement can be earned today."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import progress, ranks


def lesson(mid, kind="module", exercises=1):
    return {"id": mid, "kind": kind, "exercises": [{"id": f"x{n}"} for n in range(exercises)]}


def facts(**kw):
    base = {"finished": set(), "exercises": 0, "fixes": 0, "notebooks": 0, "achievements": set(), "days": 0}
    return {**base, **kw}


def test_the_ladder_needs_what_the_design_says():
    needs = {r["id"]: r["needs"] for r in ranks.RANKS}
    assert needs["master-forger"][0] == {"paths": 3}
    assert needs["pack-runner"] == [{"exercises": 25}, {"labs": 4}]
    assert needs["trailfinder"][1] == {"fixes": 3}
    assert needs["pack-leader"] == [{"paths": 5}, {"labs": 8}, {"fixes": 10}]
    assert needs["cyclops-smith"][1] == {"notebooks": 15}
    assert needs["legend-of-the-forge"][2] == {"days": 90}
    assert ranks.standing(progress.empty(), [])["ladder_version"] == 1 == ranks.LADDER_VERSION


def test_the_top_rank_has_no_next_one(monkeypatch):
    every = {m for v in ranks.PATHS.values() for m in v["modules"]} | {"LAB-12"}
    monkeypatch.setattr(ranks, "facts", lambda p: facts(finished=every, exercises=999, fixes=999, notebooks=999,
                                                       achievements=set(progress.ACHIEVEMENTS), days=999))
    s = ranks.standing(progress.empty(), [lesson(m) for m in every])
    assert (s["rank_id"], s["number"], s["of"], s["next"]) == ("legend-of-the-forge", 13, 13, None)
    assert all(r["held"] for r in s["ladder"])


def test_one_rank_below_the_top_names_the_top_as_next(monkeypatch):
    every = {m for v in ranks.PATHS.values() for m in v["modules"]}
    monkeypatch.setattr(ranks, "facts", lambda p: facts(finished=every, exercises=999, fixes=999, notebooks=999,
                                                       achievements=set(progress.ACHIEVEMENTS), days=999))
    s = ranks.standing(progress.empty(), [lesson(m) for m in every])
    assert (s["number"], s["next"]["id"]) == (12, "legend-of-the-forge")


def test_facts_count_each_kind_of_evidence_once(monkeypatch):
    rows = [{"type": "lab.pass", "ref": {"id": "e1"}, "id": "r1", "at": "2026-01-01T00:00:00+00:00"},
            {"type": "lab.pass", "ref": {"id": "e2"}, "id": "r2", "at": "not a time"},
            {"type": "lab.pass", "ref": {"id": "e2"}, "id": "r3", "at": None},
            {"type": "fix.checked", "ref": {"id": "e9"}, "id": "f1", "at": "2026-03-02T00:00:00+00:00"},
            {"type": "fix.checked", "id": "f2"},
            {"type": "fix.checked", "id": "f2"},
            {"type": "validate.pass", "id": "v1", "artifact": {"name": "nb"}},
            {"type": "module.complete", "ref": {"id": "LM-01"}, "id": "m1"}]
    fake = SimpleNamespace(included=lambda p: rows, progression=lambda p: {"achievements": [{"id": "guardian"}]},
                           ACHIEVEMENTS=progress.ACHIEVEMENTS)
    monkeypatch.setattr(ranks, "progress", fake)
    f = ranks.facts({})
    assert (f["exercises"], f["fixes"], f["notebooks"], f["days"]) == (2, 2, 1, 60)
    assert f["finished"] == {"LM-01"} and f["achievements"] == {"guardian"}


def test_no_dated_evidence_is_zero_days(monkeypatch):
    fake = SimpleNamespace(included=lambda p: [{"type": "x", "id": "a", "at": "bad"}],
                           progression=lambda p: {"achievements": []}, ACHIEVEMENTS=progress.ACHIEVEMENTS)
    monkeypatch.setattr(ranks, "progress", fake)
    assert ranks.facts({})["days"] == 0
    assert ranks.facts(progress.empty())["days"] == 0


def test_parse_reads_a_time_and_nothing_else():
    assert ranks._parse("2026-01-01T00:00:00+00:00").year == 2026
    assert ranks._parse(None) is None and ranks._parse("") is None and ranks._parse("soon") is None


def test_requirement_texts_and_names():
    shipped = {"LAB-02"}
    available = {"exercises": 0, "labs": 0}
    assert ranks._check({"modules": ["LAB-02"]}, facts(), shipped, available)["text"] == "Finish LAB-02"
    assert ranks._names(["A", "B", "C"]) == "A, B and C"
    assert ranks._check({"paths": 1}, facts(), shipped, available)["text"] == "Finish 1 learning path"
    assert ranks._check({"paths": 2}, facts(), shipped, available)["text"] == "Finish 2 learning paths"
    with pytest.raises(ValueError, match="unknown rank requirement"):
        ranks._check({"stars": 1}, facts(), shipped, available)


@pytest.mark.parametrize("have, soon", [(4, False), (3, True)])
def test_labs_and_exercises_are_earnable_once_exactly_enough_have_shipped(have, soon):
    shipped: set[str] = set()
    f = facts()
    assert ranks._check({"labs": 4}, f, shipped, {"labs": have, "exercises": 0})["coming_soon"] is soon
    assert ranks._check({"exercises": 4}, f, shipped, {"labs": 0, "exercises": have})["coming_soon"] is soon


@pytest.mark.parametrize("labs, soon", [(4, False), (3, True)])
def test_only_labs_count_as_shipped_labs(labs, soon):
    live = [lesson(f"LAB-{n}", kind="lab", exercises=10) for n in range(labs)] + [lesson("LM-01", exercises=10)]
    rank = next(r for r in ranks.standing(progress.empty(), live)["ladder"] if r["id"] == "pack-runner")
    assert rank["checks"][1]["coming_soon"] is soon
    assert rank["checks"][0]["coming_soon"] is False
