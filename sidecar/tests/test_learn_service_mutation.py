"""Mutation batch 122: learn_service.py (docs/quality/mutation-testing.md). The survivors were a module's
hint counts, size and datasets, what saving an answer returns, when a check is recorded in Profile, the
first hint level, the decision patterns, which rules count as notebook rules when dismissing, and what
reaches a dismissal and Try the fix."""

from __future__ import annotations

from fabric_desktop import learn_service
from fabric_desktop.learn import content, patterns
from fabric_desktop.learn_service import LearnMixin


class _Svc(LearnMixin):
    def __init__(self, root):
        self.root = root
        self.observed, self.kinds = [], []

    def _progress_observe_lab(self, result, module):
        self.observed.append(module["id"])
        return ["first-lab"]

    def governance_practice_settings(self, kind):
        self.kinds.append(kind)
        return {"settings": {}}


def test_a_module_shows_its_hint_counts_size_and_datasets(tmp_path):
    svc = _Svc(tmp_path)
    lm = svc.learn_module("LM-01")
    assert [e["hints"] for e in lm["exercises"]] == [len(e.get("hints") or []) for e in content.get("LM-01")["exercises"]]
    assert lm["exercises"][0]["hints"] == 3
    assert (lm["size"], lm["datasets"]) == ("S", [])  # none given
    assert (svc.learn_module("LAB-02")["size"], svc.learn_module("LAB-02")["datasets"]) == ("L", ["shop"])


def test_saving_an_answer_says_it_was_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(learn_service.labs, "save_answer", lambda *a: None)
    assert _Svc(tmp_path).learn_lab_save("LM-01", "e1", "select 1") == {"saved": True}


def test_only_a_passed_check_is_recorded_in_profile(tmp_path, monkeypatch):
    svc = _Svc(tmp_path)
    for passed, earned in ((False, []), (True, ["first-lab"])):
        monkeypatch.setattr(learn_service.labs, "check", lambda *a, p=passed: {"passed": p})
        assert svc.learn_lab_check("LM-01", "e1", "x")["earned"] == earned
    assert svc.observed == ["LM-01"]


def test_the_first_hint_is_level_one(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(learn_service.labs, "hint", lambda m, e, level: asked.append(level) or {})
    _Svc(tmp_path).learn_lab_hint("LM-01", "e1")
    assert asked == [1]


def test_the_decision_patterns_are_the_shipped_ones(tmp_path):
    got = _Svc(tmp_path).learn_patterns()
    assert got and got == patterns.load()


def test_a_dismissal_checks_notebook_or_sql_policy_and_keeps_where_and_line(tmp_path, monkeypatch):
    added = []
    monkeypatch.setattr(learn_service.dismissals, "add", lambda root, *a: added.append(a) or [])
    svc = _Svc(tmp_path)
    svc.learn_dismiss("NB001", "file", "nb.ipynb", "3")
    svc.learn_dismiss("SQL001", "file")
    assert svc.kinds == ["notebook", "sql"]
    assert added == [("NB001", "file", "nb.ipynb", "3"), ("SQL001", "file", "", "")]


def test_try_the_fix_hands_the_findings_on_and_returns_the_proposal(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(learn_service.fixes, "propose", lambda q, t, f, c: asked.append(f) or {"fixes": ["x"]})
    svc = _Svc(tmp_path)
    assert svc.learn_fixes("select 1", findings=[{"rule_id": "R"}]) == {"fixes": ["x"]}
    assert svc.learn_fixes("select 1") == {"fixes": ["x"]}
    assert asked == [[{"rule_id": "R"}], []]
