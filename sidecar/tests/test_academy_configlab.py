"""config_model exercises: a settings document checked as data against the exercise's rules. Nothing runs,
a pass is SIMULATED, rules never reach the screen, and every failure says what is wrong in words."""

from __future__ import annotations

import functools
import json
import shutil

import pytest

from fabric_desktop.academy import configlab, exercises, lab, registry
from fabric_desktop.academy import validators as v
from fabric_desktop.academy_service import AcademyMixin

PACK, EID = "dp-700", "E36"
STARTER = {"workspace": "Sales", "members": [{"name": "ana", "role": "Admin"}, {"name": "ben", "role": "Admin"},
                                             {"name": "cy", "role": "Admin"}], "guest_access": True}
SOLUTION = {"workspace": "Sales", "members": [{"name": "ben", "role": "Contributor"}, {"name": "ana", "role": "Admin"},
                                              {"name": "cy", "role": "Viewer"}]}
WRONG = {"wrong-all-admin.json": STARTER,
         "wrong-guest.json": {**SOLUTION, "guest_access": True},
         "wrong-two-admins.json": {**SOLUTION, "members": [{"name": "ana", "role": "Admin"}, {"name": "ben", "role": "Admin"},
                                                           {"name": "cy", "role": "Viewer"}]}}
VALIDATION = [
    {"kind": "config_matches", "fail": "Give each person the least role their work needs.",
     "rules": [{"path": "members", "where": {"role": "Admin"}, "count": 1, "why": "One admin is enough."},
               {"path": "members", "where": {"name": "cy", "role": "Viewer"}, "count": 1},
               {"path": "members", "where": {"name": "ben", "role": "Contributor"}, "count": 1}]},
    {"kind": "config_matches", "fail": "Guests shouldn't reach this workspace.",
     "rules": [{"path": "guest_access", "absent": True}]},
]
EX = {"id": EID, "starter": "settings.json", "validation": VALIDATION}


def text(doc):
    return json.dumps(doc, indent=2)


# ---- counting list entries, in any order

def test_count_where_ignores_order_and_says_what_it_found():
    rule = {"path": "members", "where": {"role": "Admin"}, "count": 1}
    assert v.config_matches(SOLUTION, [rule])["passed"]
    assert v.config_matches({"members": list(reversed(SOLUTION["members"]))}, [rule])["passed"]
    out = v.config_matches(STARTER, [rule])
    assert not out["passed"] and out["detail"] == 'members has 3 entries with role "Admin"; it should have exactly 1.'
    assert "at least 4" in v.config_matches(SOLUTION, [{"path": "members", "count_min": 4}])["detail"]
    assert "at most 2" in v.config_matches(SOLUTION, [{"path": "members", "count_max": 2}])["detail"]
    assert "should be a list" in v.config_matches({"members": "ana"}, [rule])["detail"]
    assert "isn't set" in v.config_matches({}, [rule])["detail"]


def test_count_where_never_echoes_a_secret_looking_value():
    doc = {"conns": [{"password": "hunter2"}]}
    out = v.config_matches(doc, [{"path": "conns", "where": {"password": "hunter2-is-a-secret"}, "count": 1}])
    assert not out["passed"] and "hunter2" not in out["detail"]


# ---- the adapter

def test_reference_passes_and_wrong_answers_fail(tmp_path):
    for name, doc in {"solution.json": SOLUTION, **WRONG}.items():
        (tmp_path / name).write_text(text(doc), encoding="utf-8")
    result = configlab.self_test(tmp_path, EX)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())
    guest = result["wrong"]["wrong-guest.json"]["results"]
    assert guest[0]["passed"] and not guest[1]["passed"] and "shouldn't be there" in guest[1]["detail"]


@pytest.mark.parametrize(("answer", "said"), [
    ("{", "isn't valid JSON: Expecting property name enclosed in double quotes at line 1, column 2"),
    ("not json", "isn't valid JSON"),
    ("[" * 100_000 + "]" * 100_000, "nested too deeply"),
    (" " * (configlab.MAX_BYTES + 1), "over 1 MB"),
], ids=["unclosed", "not-json", "deep-nesting", "oversized"])   # short ids: Windows caps PYTEST_CURRENT_TEST
def test_a_document_that_cant_be_read_fails_every_check_in_words(answer, said):
    res = configlab.grade(answer, EX)
    assert not res["passed"] and len(res["results"]) == 2
    assert all(said in r["detail"] and not r["passed"] for r in res["results"]), res


def test_a_byte_order_mark_is_fine():
    assert configlab.grade("﻿" + text(SOLUTION), EX)["passed"]


def test_outline_lists_every_setting_for_run():
    got = configlab.outline(text(SOLUTION))
    assert got["columns"] == ["setting", "value"] and not got["more"]
    assert ["members.0.role", '"Contributor"'] in got["rows"] and ["workspace", '"Sales"'] in got["rows"]
    assert configlab.outline(text({"a": list(range(10))}), limit=3) == {
        "columns": ["setting", "value"], "rows": [["a.0", "0"], ["a.1", "1"], ["a.2", "2"]], "more": True}
    with pytest.raises(ValueError, match="isn't valid JSON"):
        configlab.outline("{")


@pytest.mark.parametrize(("check", "said"), [
    ({"kind": "config_matches"}, "needs rules"),
    ({"kind": "config_matches", "rules": [{"equals": 1}]}, "rule 1 needs a path"),
    ({"kind": "config_matches", "rules": [{"path": "a"}]}, "needs one test"),
    ({"kind": "config_matches", "rules": [{"path": "a", "equals": 1, "absent": True}]}, "needs one test"),
    ({"kind": "config_matches", "rules": [{"path": "a", "equals": 1, "colour": "red"}]}, "unknown fields: colour"),
    ({"kind": "config_matches", "rules": [{"path": "a", "where": {"x": 1}, "equals": 1}]}, "where is an object of fields, with a count"),
    ({"kind": "config_matches", "rules": [{"path": "a", "count": -1}]}, "counts are whole numbers"),
    ({"kind": "config_matches", "rules": [{"path": "a", "count": True}]}, "counts are whole numbers"),
    ({"kind": "config_matches", "rules": [{"path": "a", "one_of": "x"}]}, "one_of is a list"),
    ({"kind": "config_matches", "rules": [{"path": "a", "equals": 1, "why": ""}]}, "why is a sentence"),
])
def test_check_problems(check, said):
    assert any(said in p for p in configlab.check_problems(check)), configlab.check_problems(check)


def test_well_formed_checks_have_no_problems():
    assert [p for c in VALIDATION for p in configlab.check_problems(c)] == []
    assert configlab.check_problems({"kind": "config_matches", "rules": [{"path": "n", "min": 1, "max": 4}]}) == []


# ---- in a pack: problems() and self_test()

@pytest.fixture
def config_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / EID, root / PACK / "grading" / EID
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "config_model", "class": "simulated", "engine_label": "A model of workspace roles on this PC",
               "starter": "settings.json", "validation": VALIDATION, "parity": []})
    ex.pop("setup", None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (folder / "settings.json").write_text(text(STARTER), encoding="utf-8")
    (grading / "solution.json").write_text(text(SOLUTION), encoding="utf-8")
    for name, doc in WRONG.items():
        (grading / name).write_text(text(doc), encoding="utf-8")
    return root


def test_a_config_exercise_is_well_formed_and_self_tests(config_pack):
    assert exercises.problems(config_pack) == []
    result = exercises.self_test(PACK, EID, config_pack)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())


@pytest.mark.parametrize(("change", "remove", "message"), [
    ({}, "grading/E36/solution.json", "reference settings document at grading/E36/solution.json"),
    ({}, "grading/E36/wrong-guest.json", None),   # two wrong answers are still there: fine
    ({"class": "local_verified"}, None, "a config_model exercise is SIMULATED"),
    ({"class": "knowledge_only"}, None, "a config_model exercise is SIMULATED"),
    ({"starter": "answer.py"}, None, "starts from a JSON settings document"),
    ({"validation": [{"kind": "result_matches"}]}, None, "config_model can't check result_matches"),
    ({"validation": [{"kind": "config_matches", "rules": [{"path": "a"}]}]}, None, "needs one test"),
    ({"uses": "LAB-04/rebuild"}, None, "only for duckdb_lab exercises"),
])
def test_a_config_exercise_that_cant_be_graded_is_caught(config_pack, change, remove, message):
    path = config_pack / PACK / "exercises" / EID / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    if remove:
        (config_pack / PACK / remove).unlink()
    if change.get("starter") == "answer.py":
        (config_pack / PACK / "exercises" / EID / "answer.py").write_text("x = 1")
    found = exercises.problems(config_pack)
    if message is None:
        assert found == []
    else:
        assert any(message in p for p in found), found


def test_fewer_than_two_wrong_answers_is_caught(config_pack):
    for name in ("wrong-guest.json", "wrong-two-admins.json"):
        (config_pack / PACK / "grading" / EID / name).unlink()
    assert any("at least two planted wrong answers (grading/E36/wrong-*.json)" in p for p in exercises.problems(config_pack))


# ---- through the workspace and the academy_* methods

class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(config_pack, tmp_path, monkeypatch):
    monkeypatch.setattr(exercises, "get", functools.partial(exercises.get, root=config_pack))
    monkeypatch.setattr(exercises, "file_text", functools.partial(exercises.file_text, root=config_pack))
    monkeypatch.setattr(lab, "_exercise_folder", lambda pid, eid: config_pack / pid / "exercises" / eid)
    data = tmp_path / "data"
    data.mkdir()
    return _Svc(data)


def test_start_save_run_check_and_reset(svc):
    st = svc.academy_start(PACK, EID)
    assert st["started"] and json.loads(st["answer"]) == STARTER
    folder = lab.workspace(svc.root, PACK, EID)
    assert sorted(p.name for p in folder.iterdir()) == ["settings.json", "state.json"]
    shown = svc.academy_exercise(PACK, EID)
    assert shown["checks"] == ["config_matches", "config_matches"] and "rules" not in json.dumps(shown)

    got = svc.academy_run(PACK, EID, text(STARTER))
    assert ["guest_access", "true"] in got["rows"] and "ms" in got

    res = svc.academy_validate(PACK, EID)
    assert not res["passed"] and not svc.rows
    assert res["verification"]["label"] == "SIMULATED"
    assert [r.get("message") for r in res["results"]] == ["Give each person the least role their work needs.",
                                                          "Guests shouldn't reach this workspace."]
    assert "One admin is enough." in res["results"][0]["detail"]

    svc.academy_save(PACK, EID, text(SOLUTION))
    res = svc.academy_validate(PACK, EID)
    assert res["passed"], res
    assert svc.rows and svc.rows[0]["detail"]["class"] == "simulated"

    svc.academy_reset(PACK, EID)
    assert json.loads(lab.state(svc.root, PACK, EID)["answer"]) == STARTER


def test_a_supplied_answer_is_checked_without_saving_it(svc):
    svc.academy_start(PACK, EID)
    assert svc.academy_validate(PACK, EID, text(SOLUTION))["passed"]
    assert json.loads(lab.state(svc.root, PACK, EID)["answer"]) == STARTER


def test_run_on_a_broken_document_says_why(svc):
    svc.academy_start(PACK, EID)
    with pytest.raises(ValueError, match="isn't valid JSON"):
        svc.academy_run(PACK, EID, "{")
