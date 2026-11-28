"""agent.toml edges found by running every mutant of agent_config.py (mutation batch 323,
docs/quality/mutation-testing.md): the defaults of a config built in code, sections that can't be
changed once read, lists written as something else, the service principal's two ids, the owner,
shortcut checks with only their own webhook, shared schedules against pipeline.entities, paths the
system refuses to look up, and TOML nested too deeply to read."""

import dataclasses
from pathlib import Path

import pytest

from fabric_desktop import agent_config


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FABRIC_DESKTOP_HOME", str(tmp_path / "agent-home"))


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "layers.toml").write_text("", encoding="utf-8")
    return folder


def config(project, agent=None, **extra) -> agent_config.AgentConfig:
    raw = {
        "agent": {"project": str(project), "environments": ["dev"], "name": "agent-1"}
        | (agent or {}),
        "auth": {"type": "user"},
    } | extra
    return agent_config.parse(raw)


def errors(project, agent=None, **extra) -> list[str]:
    with pytest.raises(agent_config.ConfigError) as err:
        config(project, agent, **extra)
    return err.value.errors


def test_a_config_built_in_code_runs_one_job_and_queues_up_to_1000():
    cfg = agent_config.AgentConfig(
        Path("agent.toml"), "a", Path("."), Path("."), ["dev"], {"type": "user"}
    )
    assert cfg.concurrency == 1 and cfg.max_queued == 1000
    assert agent_config.Schedule("s", "dev", [], {}).shared is False


@pytest.mark.parametrize(
    "section",
    [
        agent_config.PipelineConfig(),
        agent_config.IncidentsConfig(),
        agent_config.ShortcutHealthConfig(),
    ],
)
def test_the_optional_sections_cant_be_changed_once_read(section):
    with pytest.raises(dataclasses.FrozenInstanceError):
        section.enabled = True  # type: ignore[misc]


def test_a_list_written_as_a_string_is_named_not_a_crash(project):
    assert errors(project, pipeline={"environments": "dev"}) == [
        'pipeline.environments: a list, like ["environments"]'
    ]
    route = {"name": "sales", "webhook_env": "SALES_HOOK", "teams": "Sales"}
    assert errors(project, incidents={"route": [route]}) == [
        'incidents.route sales.teams: a list, like ["teams"]'
    ]


def test_schedules_written_as_something_else_are_named(project):
    assert errors(project, schedule=5) == [
        "schedule: each schedule is a [[schedule]] table"
    ]
    assert errors(
        project, schedule=[5, {"id": "bad id!", "env": "dev", "daily": ["02:00"]}]
    ) == [
        "schedule: each schedule is a [[schedule]] table",
        "schedule bad id!: id is letters, digits, . _ or -",
    ]


def test_a_service_principal_needs_both_its_ids(project, tmp_path):
    cert = tmp_path / "agent.pem"
    cert.write_text("", encoding="utf-8")
    for ids in ({"tenant_id": "t"}, {"client_id": "c"}):
        raw = {
            "agent": {
                "project": str(project),
                "environments": ["dev"],
                "name": "agent-1",
            },
            "auth": {"type": "service_principal", "certificate_path": str(cert)} | ids,
        }
        with pytest.raises(agent_config.ConfigError) as err:
            agent_config.parse(raw)
        assert err.value.errors == [
            "auth: a service principal needs tenant_id and client_id"
        ]


def test_the_owner_is_read(project):
    assert config(project, {"owner": "  Sales data team "}).owner == "Sales data team"


def test_shortcut_checks_with_their_own_webhook_and_no_alerts_webhook_are_on(project):
    cfg = config(
        project, shortcut_health={"enabled": True, "webhook_env": "SHORTCUTS_HOOK"}
    )
    assert (
        cfg.shortcut_health.enabled is True
        and cfg.shortcut_health.webhook_env == "SHORTCUTS_HOOK"
    )


def test_a_shared_schedule_whose_entities_the_pipeline_takes_is_read(project):
    pipeline = {"enabled": True, "entities": ["silver.*"]}
    shared = {"id": "s1", "env": "dev", "daily": ["02:00"], "shared": True}
    cfg = config(
        project, pipeline=pipeline, schedule=[shared | {"entities": ["silver.a"]}]
    )
    assert cfg.schedules[0].entities == ["silver.a"]
    assert errors(
        project, pipeline=pipeline, schedule=[shared | {"entities": ["gold.b"]}]
    ) == ["schedule s1: gold.b isn't in pipeline.entities"]


def test_a_route_name_in_an_error_is_cut_to_80_characters(project):
    route = {"name": "x" * 100, "webhook_env": "HOOK"}
    assert errors(project, incidents={"route": [route]}) == [
        f'incidents.route {"x" * 79}…: name is letters, digits, . _ or - (and not "default")'
    ]


def test_a_project_path_the_system_cant_look_up_isnt_a_project(project):
    long = "p" * 5000
    assert errors(project, {"project": long}) == [
        f"agent.project: {'p' * 199}… isn't a metadata project (no layers.toml)"
    ]


def test_toml_nested_too_deeply_is_a_config_error(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text("a = " + "[" * 5000 + "]" * 5000, encoding="utf-8")
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.load(path)
    assert err.value.errors == ["not valid TOML: it is nested too deeply to read"]
