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


# ---- team notes (the rest of GD-09: team guidance packages)

KNOWN = frozenset({"SQ002", "NB003", "DP-01"})


def _noted(name, notes, **extra):
    return {"name": name, "rule": team.GUIDANCE_RULE, "owner": "Data platform", "version": 3, "hash": "a" * 64,
            "params": {"notes": {"value": notes}}, **extra}


def test_a_note_names_its_policy_owner_version_and_hash():
    got = team.team_notes([_noted("Sales team", {"sq002": "  Our facts are partitioned by order_date.  "})], _names, KNOWN)
    assert got == {"notes": {"SQ002": [{"text": "Our facts are partitioned by order_date.", "policy": "Sales team",
                                        "owner": "Data platform", "version": 3, "sha256": "a" * 64}]}, "problems": []}


def test_every_policys_note_is_kept_in_name_order_and_others_are_ignored():
    pols = [_noted("Zeta", {"SQ002": "z"}), _noted("Alpha", {"SQ002": "a", "DP-01": "d"}),
            {"name": "Rules", "rule": "SQ002", "params": {"notes": {"value": {"SQ002": "not a guidance policy"}}}}]
    got = team.team_notes(pols, _names, KNOWN)["notes"]
    assert [n["text"] for n in got["SQ002"]] == ["a", "z"] and [n["text"] for n in got["DP-01"]] == ["d"]


def test_a_note_that_cant_be_shown_is_ignored_and_said():
    got = team.team_notes([_noted("Typo", {"SQ999": "x", "NB003": 42, "SQ002": "   "}),
                           _noted("List", ["SQ002"]), _noted("Long", {"NB003": "x" * (team.NOTE_MAX + 50)})], _names, KNOWN)
    assert len(got["notes"]["NB003"][0]["text"]) == team.NOTE_MAX and set(got["notes"]) == {"NB003"}
    said = " | ".join(got["problems"])
    for part in ("'SQ999', which isn't a rule", "note for NB003 isn't text", "note for SQ002 isn't text",
                 "List sets notes to something that isn't", "cut to 2000"):
        assert part in said, part


def test_a_policy_with_too_many_notes_uses_the_first_ones():
    many = {f"SQ{i:03d}": "x" for i in range(team.NOTES_MAX + 5)}
    got = team.team_notes([_noted("Big", many)], _names, frozenset(many))
    assert sum(len(v) for v in got["notes"].values()) == team.NOTES_MAX and "only the first" in got["problems"][0]


def _notes_policy(svc, notes):
    svc.governance_create({"id": "guide", "name": "New starters", "category": "quality", "rule": "learn.guidance",
                           "owner": "Data platform", "params": {"level": {"value": "guided"}, "notes": {"value": notes}}})
    svc.governance_publish("guide")
    svc.governance_assign("guide", "enterprise")


def test_the_open_projects_policies_give_the_notes_and_check_now_ignores_them(tmp_path):
    svc = _service(tmp_path)
    assert svc.learn_team_notes()["notes"] == {}
    _notes_policy(svc, {"SQ002": "We filter on order_date_key; see the wiki page Partitions.", "DP-02": "We use DuckDB."})
    got = svc.learn_team_notes()
    assert got["note"] == "" and got["problems"] == [] and got["package"] is None
    [note] = got["notes"]["SQ002"]
    assert note["policy"] == "New starters" and note["owner"] == "Data platform" and note["version"] == 1
    assert len(note["sha256"]) == 64 and got["notes"]["DP-02"][0]["text"] == "We use DuckDB."
    [pol] = svc.governance_check_now()["policies"]
    assert pol["status"] == "not_checked" and pol["missing"] == [], "notes are never a rule to check"


def test_without_the_platform_the_notes_come_from_the_verified_package(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    _notes_policy(svc, {"NB003": "Use the shared helper in utils."})
    svc.governance_package_export()
    hashed = svc.learn_team_notes()["notes"]["NB003"][0]["sha256"]

    def unreachable(*a, **k):
        raise ConnectionError("the platform can't be reached")

    monkeypatch.setattr(type(svc), "governance_resolve", unreachable)
    got = svc.learn_team_notes()
    assert got["notes"]["NB003"][0]["text"] == "Use the shared helper in utils."
    assert got["notes"]["NB003"][0]["sha256"] == hashed and got["package"]["policies"] == 1
    # A package changed after it was taken is never used.
    path = svc._gc_package_path("crm")
    path.write_text(path.read_text().replace("Use the shared helper", "Paste the key"))
    got = svc.learn_team_notes()
    assert got["notes"] == {} and "changed after it was taken" in got["note"]


def test_team_notes_is_a_read_only_engine_method():
    from fabric_desktop import audit, server
    from fabric_desktop.learn_service import METHODS

    assert "learn_team_notes" in METHODS <= server.METHODS and not audit.audited("learn_team_notes")
