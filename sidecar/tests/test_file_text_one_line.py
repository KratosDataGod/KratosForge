"""A name or value read from someone's file goes into a problem message as one short line, so it
can't start a line of its own in a problem list."""

import json

import pytest

from fabric_desktop import agent_config, app_settings
from fabric_desktop.central import core, policy
from fabric_desktop.git import capacity_files

FORGED = "x\n- forged problem\x1b[0m" + "y" * 500


def one_line_each(problems, most=400):
    for p in problems:
        assert "\n" not in p and "\x1b" not in p and "forged problem" in p and len(p) < most, p


def test_a_capacity_name_is_one_line(tmp_path):
    name = json.dumps(FORGED)  # a quoted TOML key holds the line break
    (tmp_path / capacity_files.FILE).write_text(f"[capacities.{name}]\nsku = 'F2'\n", encoding="utf-8")
    problems = capacity_files.read(tmp_path)["problems"]
    assert problems
    one_line_each(problems)


def test_a_policy_parameter_name_is_one_line():
    with pytest.raises(core.PlatformError) as e:
        policy.clean_definition({"name": "p", "category": next(iter(policy.POLICY_CATEGORIES)),
                                 "params": {FORGED: {"no": "value"}}})
    one_line_each([str(e.value)])


def test_an_unknown_policy_setting_is_one_line(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({FORGED: True}), encoding="utf-8")
    problems = [p for p in app_settings.policy_problems() if "forged" in p]
    assert problems and all("y" * 100 not in p for p in problems)  # the name is cut; the settings list follows
    one_line_each(problems, most=3000)


def test_agent_config_names_and_paths_are_one_line(tmp_path):
    raw = {"agent": {"project": FORGED, "environments": ["dev"]},
           "auth": {"type": "service_principal", "tenant_id": "t", "client_id": "c", "certificate_path": FORGED},
           "incidents": {"route": [{"name": FORGED, "webhook_env": "W"}]},
           "schedule": [{"id": FORGED, "env": "dev"}],
           "pipeline": {"environments": [FORGED]}}
    with pytest.raises(agent_config.ConfigError) as e:
        agent_config.parse(raw)
    forged = [p for p in e.value.errors if "forged" in p]
    assert len(forged) >= 5, e.value.errors
    one_line_each(forged)


def test_a_teams_error_is_one_line_without_a_token():
    from fabric_desktop import dataops_service
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"

    class Resp:
        def json(self):
            return {"error": {"message": f"Bad card\nBearer {token}"}}

    assert dataops_service._teams_error(Resp()) == ": Bad card Bearer [removed]"
