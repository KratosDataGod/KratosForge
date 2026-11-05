"""GD-09: a governance policy sets a team's default guidance level (learn/team.py). It reaches the Best
practices panels through governance_practice_settings, it's never a rule to check, and a project
assignment can change it like any parameter."""

from __future__ import annotations

from fabric_desktop.learn import team


def _names(pol):
    return team.GUIDANCE_RULE in str(pol.get("rule") or "")


def _pol(name, **params):
    return {"name": name, "rule": team.GUIDANCE_RULE, "params": {k: {"value": v} for k, v in params.items()}}


def test_the_subjects_own_level_wins_over_the_policys_level():
    pols = [_pol("Team", level="professional", sql="guided")]
    assert team.team_level(pols, "sql", _names) == {"level": "guided", "policy": "Team", "problems": []}
    assert team.team_level(pols, "notebook", _names)["level"] == "professional"


def test_more_guidance_wins_between_two_policies_and_others_are_ignored():
    pols = [_pol("Lean", level="expert"), _pol("Onboarding", level="Guided"),
            {"name": "Rules", "rule": "SQ002", "params": {"level": {"value": "expert"}}}]
    assert team.team_level(pols, "sql", _names) == {"level": "guided", "policy": "Onboarding", "problems": []}
    assert team.team_level([], "sql", _names) == {"level": None, "policy": "", "problems": []}


def test_a_value_that_isnt_a_level_is_ignored_and_said():
    got = team.team_level([_pol("Typo", notebooks="beginner", level="expert")], "notebook", _names)
    assert got["level"] == "expert" and "Typo sets notebooks to 'beginner'" in got["problems"][0]


def _service(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    return svc


def test_the_panels_get_the_teams_level_and_check_now_has_nothing_to_check_for_it(tmp_path):
    svc = _service(tmp_path)
    svc.governance_create({"id": "guide", "name": "New starters", "category": "quality", "rule": "learn.guidance, NB003",
                           "params": {"level": {"value": "guided"}, "sql": {"value": "professional"}}})
    svc.governance_publish("guide")
    svc.governance_assign("guide", "enterprise")
    nb = svc.governance_practice_settings("notebook")
    assert nb["guidance"] == {"level": "guided", "policy": "New starters"} and nb["note"] == ""
    assert nb["settings"] == {"NB003": {"enabled": True, "severity": "Warning"}}, "the rule it names still works"
    assert svc.governance_practice_settings("sql")["guidance"] == {"level": "professional", "policy": "New starters"}
    [pol] = svc.governance_check_now()["policies"]
    assert pol["rules"] == ["learn.guidance", "NB003"] and pol["missing"] == [], "the level isn't a missing rule"


def test_a_project_assignment_changes_the_teams_level(tmp_path):
    svc = _service(tmp_path)
    svc.governance_create({"id": "guide", "name": "Guidance", "category": "quality", "rule": "learn.guidance",
                           "params": {"level": {"value": "expert"}}})
    svc.governance_publish("guide")
    svc.governance_assign("guide", "enterprise")
    assert svc.governance_practice_settings("sql")["guidance"]["level"] == "expert"
    svc.governance_assign("guide", "enterprise", settings={"params": {"level": "guided"}})
    assert svc.governance_practice_settings("sql")["guidance"]["level"] == "guided"


def test_a_policy_that_only_sets_the_level_isnt_shown_as_passing(tmp_path):
    svc = _service(tmp_path)
    svc.governance_create({"id": "g", "name": "G", "category": "quality", "rule": "learn.guidance",
                           "params": {"level": {"value": "guided"}}})
    svc.governance_publish("g")
    svc.governance_assign("g", "enterprise")
    [pol] = svc.governance_check_now()["policies"]
    assert pol["status"] == "not_checked" and "learning guidance level" in pol["note"]
