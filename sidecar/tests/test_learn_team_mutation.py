"""Mutation batch 45: learn/team.py's tie-break between policies and the note limits, at their edges."""

from __future__ import annotations

from fabric_desktop.learn import team


def _names(pol):
    return team.GUIDANCE_RULE in str(pol.get("rule") or "")


def _pol(name, **params):
    return {"name": name, "rule": team.GUIDANCE_RULE, "params": {k: {"value": v} for k, v in params.items()}}


def _plain(name, notes):
    return {"name": name, "rule": team.GUIDANCE_RULE, "params": {"notes": notes}, "owner": "o", "version": 1}


def test_two_policies_at_the_same_level_keep_the_first():
    pols = [_pol("First", level="professional"), _pol("Second", level="professional")]
    assert team.team_level(pols, "sql", _names)["policy"] == "First"


def test_exactly_the_note_limit_is_kept_with_no_problem_and_one_more_is_cut():
    known = {f"R{i}" for i in range(team.NOTES_MAX + 1)}
    full = {f"R{i}": "t" for i in range(team.NOTES_MAX)}
    got = team.team_notes([_plain("P", full)], _names, known)
    assert got["problems"] == [] and len(got["notes"]) == team.NOTES_MAX
    over = {f"R{i}": "t" for i in range(team.NOTES_MAX + 1)}
    got = team.team_notes([_plain("P", over)], _names, known)
    assert len(got["notes"]) == team.NOTES_MAX
    assert got["problems"] == [f"P has {team.NOTES_MAX + 1} notes; only the first {team.NOTES_MAX} are used"]
    assert team.NOTES_MAX == 200 and team.NOTE_MAX == 2000


def test_a_note_of_exactly_the_length_limit_is_not_cut():
    body = "x" * team.NOTE_MAX
    got = team.team_notes([_plain("P", {"SQ002": body})], _names, {"SQ002"})
    assert got["problems"] == [] and got["notes"]["SQ002"][0]["text"] == body
    got = team.team_notes([_plain("P", {"SQ002": body + "y"})], _names, {"SQ002"})
    assert got["problems"] == ["P's note for SQ002 is cut to 2000 characters"]
    assert got["notes"]["SQ002"][0]["text"] == body


def test_an_unknown_ids_name_is_cut_to_forty_characters_in_the_problem():
    key = "K" * 50
    got = team.team_notes([_plain("P", {key: "t"})], _names, {"SQ002"})
    assert got["problems"] == [(f"P has a note for {'K' * 40!r}, which isn't a rule or decision pattern "
                               "with a guide, so it's ignored")]


def test_notes_given_as_text_that_mentions_value_are_a_problem_not_a_crash():
    got = team.team_notes([_plain("P", "value: use partitions")], _names, {"SQ002"})
    assert got["notes"] == {}
    assert got["problems"] == ["P sets notes to something that isn't a set of notes by rule id, so it's ignored"]


def test_notes_for_one_id_keep_policy_name_order_not_id_order():
    pols = [dict(_plain("Beta", {"SQ002": "b"}), id="1"), dict(_plain("Alpha", {"SQ002": "a"}), id="2")]
    got = team.team_notes(pols, _names, {"SQ002"})
    assert [n["policy"] for n in got["notes"]["SQ002"]] == ["Alpha", "Beta"]


def test_two_policies_with_one_name_keep_id_order():
    pols = [dict(_plain("Same", {"SQ002": "second"}), id="b"), dict(_plain("Same", {"SQ002": "first"}), id="a")]
    got = team.team_notes(pols, _names, {"SQ002"})
    assert [n["text"] for n in got["notes"]["SQ002"]] == ["first", "second"]
