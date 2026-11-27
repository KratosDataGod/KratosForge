"""Mutation batch 416: academy/configlab.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import json

from fabric_desktop.academy import configlab

RULES = [{"path": "pool.nodes", "min": 1}]
EX = {"starter": "pool.json", "validation": [{"kind": "config_matches", "rules": RULES}]}


def _json_of_bytes(n: int) -> str:
    return '"' + "x" * (n - 2) + '"'   # a JSON string of exactly n bytes


def test_a_document_of_exactly_the_cap_is_read_and_one_byte_more_isnt():
    doc, problem = configlab.parse(_json_of_bytes(configlab.MAX_BYTES))
    assert problem is None and len(doc) == configlab.MAX_BYTES - 2
    doc, problem = configlab.parse(_json_of_bytes(configlab.MAX_BYTES + 1))
    assert doc is None and problem == "settings.json is over 1 MB; a settings document is much smaller."


def test_the_cap_is_one_mebibyte():
    assert configlab.MAX_BYTES == 1024 * 1024


def test_a_check_with_an_empty_rule_list_needs_rules():
    assert configlab.check_problems({"kind": "config_matches", "rules": []}) == ["check config_matches needs rules"]


def test_a_settings_exercise_cant_run_another_kind_of_check():
    r = configlab.run_check({}, {"kind": "sql_query"})
    assert r == {"kind": "sql_query", "passed": False, "detail": "A settings exercise can't check sql_query."}


def test_an_unreadable_answer_is_named_by_the_exercises_starter():
    out = configlab.grade("{not json", EX)
    assert not out["passed"] and out["results"][0]["detail"].startswith("pool.json isn't valid JSON")


def test_an_exercise_without_a_starter_calls_it_settings_json():
    out = configlab.grade("{not json", {"validation": EX["validation"]})
    assert out["results"][0]["detail"].startswith("settings.json isn't valid JSON")


def _leaves(n: int) -> str:
    return json.dumps({f"k{i}": i for i in range(n)})


def test_outline_shows_200_settings_by_default_and_says_when_there_are_more():
    out = configlab.outline(_leaves(250))
    assert len(out["rows"]) == 200 and out["more"] is True


def test_outline_of_exactly_the_limit_has_no_more():
    out = configlab.outline(_leaves(200))
    assert len(out["rows"]) == 200 and out["more"] is False


def test_a_long_value_is_cut_to_200_characters_and_keeps_its_own_letters():
    out = configlab.outline(json.dumps({"name": "é" * 300}))
    value = out["rows"][0][1]
    assert len(value) == 200 and value.startswith('"éé')
