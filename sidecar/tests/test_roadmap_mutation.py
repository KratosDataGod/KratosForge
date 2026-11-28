"""Mutation batch 64: roadmap.py (see docs/quality/mutation-testing.md).

Pins what the mutation run showed no test held: the roadmap's version and limits, how a hand-edited
goals list is trimmed, the refusals for an empty goal and an unknown one, the lab and exercise counts
passed to each check, Learn links only to shipped lessons with their titles, and at most three
suggestions, never one the person already chose."""

from __future__ import annotations

import pytest

from fabric_desktop import ranks, roadmap


def facts(finished=(), notebooks=0):
    return {"finished": set(finished), "exercises": 0, "fixes": 0, "notebooks": notebooks,
            "achievements": set(), "days": 0}


def live(drop=()):
    ids = {i for m in roadmap.MILESTONES.values() for i in m["learn"]}
    ids |= {i for m in roadmap.MILESTONES.values() for need in m["needs"] for i in need.get("modules", [])}
    return [{"id": i, "title": f"Title of {i}", "kind": "lab" if i.startswith("LAB-") else "module"}
            for i in sorted(ids - set(drop))]


def profile(goals=()):
    return {"goals": list(goals)}


@pytest.fixture
def given(monkeypatch):
    def set_facts(f):
        monkeypatch.setattr(ranks, "facts", lambda p: f)
    return set_facts


def test_the_roadmap_reports_its_version_and_goal_limit(given):
    given(facts())
    plan = roadmap.roadmap(profile(), live())
    assert plan["version"] == 1 and plan["max_goals"] == 12
    assert roadmap.MAX_GOALS == 12 and roadmap.MAX_GOAL_TEXT == 300


def test_milestones_ask_for_five_notebooks_and_three_fixes():
    assert roadmap.MILESTONES["de-many-notebooks"]["needs"] == ({"notebooks": 5},)
    assert roadmap.MILESTONES["perf-fixes"]["needs"] == ({"fixes": 3},)


def test_a_goal_in_own_words_is_cut_to_three_hundred_characters_and_gets_a_short_id():
    p = profile()
    goal = roadmap.add_goal(p, text="a" * 400)
    assert goal["text"] == "a" * 300
    assert goal["id"].startswith("g-") and len(goal["id"]) == 14


@pytest.mark.parametrize("text", [None, "", "   \n\t "])
def test_an_empty_goal_is_refused(text):
    p = profile()
    with pytest.raises(ValueError, match="Write the goal first"):
        roadmap.add_goal(p, text=text)
    assert p["goals"] == []


def test_the_thirteenth_goal_is_refused_after_twelve():
    p = profile()
    for i in range(12):
        roadmap.add_goal(p, text=f"goal {i}")
    with pytest.raises(ValueError, match="up to 12 goals"):
        roadmap.add_goal(p, text="one more")


def test_marking_an_unknown_goal_is_refused():
    with pytest.raises(LookupError, match="isn't in your profile"):
        roadmap.mark_goal(profile(), "g-nope", True)


def test_an_own_goal_can_be_marked_done_and_then_not_done():
    p = profile()
    goal = roadmap.add_goal(p, text="Mine")
    roadmap.mark_goal(p, goal["id"], True)
    assert goal["done_at"]
    roadmap.mark_goal(p, goal["id"], False)
    assert goal["done_at"] is None


def test_cleaning_keeps_done_at_for_own_goals_only_and_cuts_long_fields():
    raw = [{"id": "g" * 100, "milestone": None, "text": "Mine", "set_at": "s" * 50, "done_at": "d" * 50},
           {"id": "g-2", "milestone": None, "text": "Not done", "done_at": ""},
           {"id": "m-sql-tuning", "milestone": "sql-tuning", "done_at": "2026-01-01"},
           {"id": "g-3", "text": "t" * 400}]
    out = roadmap.clean_goals(raw)
    assert out[0] == {"id": "g" * 80, "milestone": None, "text": "Mine", "set_at": "s" * 40, "done_at": "d" * 40}
    assert out[1]["done_at"] is None
    assert out[2]["done_at"] is None and out[2]["text"] == ""
    assert out[3]["text"] == "t" * 300


def test_cleaning_keeps_at_most_twelve_goals():
    raw = [{"id": f"g-{i}", "milestone": None, "text": "x"} for i in range(15)]
    assert [g["id"] for g in roadmap.clean_goals(raw)] == [f"g-{i}" for i in range(12)]


def test_learn_links_name_shipped_lessons_by_title_and_skip_the_rest(given):
    given(facts())
    plan = roadmap.roadmap(profile(), live(drop=("LM-04",)))
    rows = {m["id"]: m for t in plan["tracks"] for m in t["milestones"]}
    assert rows["sql-foundations"]["learn"] == [{"id": "LM-03", "title": "Title of LM-03"},
                                                {"id": "LM-15", "title": "Title of LM-15"}]
    assert rows["sql-foundations"]["coming_soon"] is True and rows["sm-keys"]["coming_soon"] is False


def test_a_lesson_with_no_title_is_linked_with_an_empty_title(given):
    given(facts())
    modules = [{**m, "title": None} if m["id"] == "LM-05" else m for m in live()]
    plan = roadmap.roadmap(profile(), modules)
    keys = next(m for t in plan["tracks"] for m in t["milestones"] if m["id"] == "sm-keys")
    assert keys["learn"] == [{"id": "LM-05", "title": ""}]


def test_at_most_three_suggestions_each_the_next_step_in_a_track_already_started(given):
    given(facts(finished=("LM-03", "LM-04", "LM-15", "LM-05", "LM-12", "LM-11"), notebooks=1))
    plan = roadmap.roadmap(profile(), live())
    assert [s["id"] for s in plan["suggestions"]] == ["de-reliable-loads", "sql-warehouse", "sm-star"]
    assert plan["suggestions"][0] == {"id": "de-reliable-loads", "title": "Loads that are safe to run again",
                                      "track": "Data Engineering"}


def test_a_next_step_already_chosen_as_a_goal_is_not_suggested(given):
    given(facts(notebooks=1))
    p = profile()
    roadmap.add_goal(p, milestone="de-reliable-loads")
    assert roadmap.roadmap(p, live())["suggestions"] == []


def _one_track(monkeypatch, need):
    track = {"id": "t", "title": "T", "milestones": (
        {"id": "x", "title": "X", "skills": (), "activities": "a", "needs": (need,), "achievements": (), "learn": ()},)}
    monkeypatch.setattr(roadmap, "TRACKS", (track,))


def test_lab_needs_are_counted_against_the_shipped_labs(monkeypatch, given):
    given(facts())
    _one_track(monkeypatch, {"labs": 2})
    two_labs = [{"id": "LAB-01", "kind": "lab"}, {"id": "LAB-02", "kind": "lab"}, {"id": "LM-01", "kind": "module"}]
    assert roadmap.roadmap(profile(), two_labs)["tracks"][0]["milestones"][0]["coming_soon"] is False
    assert roadmap.roadmap(profile(), two_labs[1:])["tracks"][0]["milestones"][0]["coming_soon"] is True


def test_exercise_needs_are_counted_across_the_shipped_lessons(monkeypatch, given):
    given(facts())
    _one_track(monkeypatch, {"exercises": 3})
    modules = [{"id": "LM-01", "exercises": [1, 2]}, {"id": "LM-02", "exercises": [3]}, {"id": "LM-03", "exercises": None}]
    assert roadmap.roadmap(profile(), modules)["tracks"][0]["milestones"][0]["coming_soon"] is False
    assert roadmap.roadmap(profile(), modules[:1] + modules[2:])["tracks"][0]["milestones"][0]["coming_soon"] is True
