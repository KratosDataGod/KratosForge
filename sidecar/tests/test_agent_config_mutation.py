"""Mutation batch 24: agent.toml defaults, the shortcut webhook and incident routes, as agent_config.py reads them."""

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


def config(project, **extra) -> agent_config.AgentConfig:
    raw = {"agent": {"project": str(project), "environments": ["dev"], "name": "agent-1"},
           "auth": {"type": "user"}} | extra
    return agent_config.parse(raw)


def errors(project, **extra) -> list[str]:
    with pytest.raises(agent_config.ConfigError) as err:
        config(project, **extra)
    return err.value.errors


def test_the_optional_sections_are_off_until_turned_on():
    pipe = agent_config.PipelineConfig()
    assert (pipe.enabled, pipe.allow_backfill, pipe.take_over, pipe.require_run) == (False, False, False, False)
    assert pipe.claim == "always" and pipe.poll_seconds == 30.0 and pipe.retention_days == 14.0
    inc = agent_config.IncidentsConfig()
    assert inc.enabled is False and inc.digest_over == 5 and inc.seconds == 300.0
    assert inc.quiet_except == ("critical",) and inc.routes == ()
    sh = agent_config.ShortcutHealthConfig()
    assert sh.enabled is False and sh.minutes == 60.0 and sh.webhook_env == ""


def test_an_agent_built_without_settings_retries_three_times(tmp_path):
    cfg = agent_config.AgentConfig(path=tmp_path / "agent.toml", name="a", home=tmp_path, project=tmp_path,
                                   environments=["dev"], auth={"type": "user"})
    assert cfg.max_attempts == 3 and cfg.backoff_seconds == 60.0 and cfg.max_backoff_seconds == 1800.0
    assert cfg.pipeline.enabled is False and cfg.incidents.enabled is False and cfg.shortcut_health.enabled is False


def test_a_minimal_file_reads_with_every_default(project):
    cfg = config(project)
    assert cfg.max_attempts == 3 and cfg.incidents.digest_over == 5
    assert cfg.pipeline.enabled is False and cfg.pipeline.take_over is False and cfg.pipeline.require_run is False
    assert cfg.incidents.routes == ()


def test_shortcut_breaks_go_to_their_own_webhook_else_the_alerts_one(project, monkeypatch):
    monkeypatch.setenv("SHORTCUT_HOOK_FOR_TEST", "shortcut-target")
    monkeypatch.setenv("ALERTS_HOOK_FOR_TEST", "alerts-target")
    own = config(project, alerts={"teams_webhook_env": "ALERTS_HOOK_FOR_TEST"},
                 shortcut_health={"enabled": True, "webhook_env": "SHORTCUT_HOOK_FOR_TEST"})
    assert own.shortcut_webhook == "shortcut-target"
    fallback = config(project, alerts={"teams_webhook_env": "ALERTS_HOOK_FOR_TEST"})
    assert fallback.shortcut_webhook == "alerts-target"
    assert config(project).shortcut_webhook == ""


def test_routes_are_read_with_their_filters(project):
    cfg = config(project, incidents={"route": [{"name": "sales", "webhook_env": "SALES_HOOK", "teams": ["Sales data"]},
                                                {"name": "ops", "webhook_env": "OPS_HOOK"}]})
    assert [r["name"] for r in cfg.incidents.routes] == ["sales", "ops"]
    assert cfg.incidents.routes[0] == {"name": "sales", "webhook_env": "SALES_HOOK", "teams": ("Sales data",),
                                       "owners": (), "criticality": (), "kinds": (), "envs": ()}


@pytest.mark.parametrize("route, problem", [
    ({"name": "a", "url": "x"}, "incidents.route a: give the webhook as webhook_env, the name of an environment variable"),
    ({"name": "a", "webhook": "x"}, "incidents.route a: give the webhook as webhook_env, the name of an environment variable"),
    ({"name": "a"}, "incidents.route a: webhook_env is required"),
    ({"name": "a", "webhook_env": ""}, "incidents.route a: webhook_env is required"),
    ({"name": "a", "webhook_env": "A", "colour": "red"}, "incidents.route a: doesn't know colour"),
])
def test_a_route_that_breaks_a_rule_is_one_problem(project, route, problem):
    assert errors(project, incidents={"route": [route]}) == [problem]


def test_escalation_names_a_route_that_is_read(project):
    cfg = config(project, incidents={"escalate_after_minutes": 30, "escalate_to": "ops",
                                     "route": [{"name": "ops", "webhook_env": "OPS_HOOK"}]})
    assert cfg.incidents.escalate_to == "ops" and cfg.incidents.escalate_after_minutes == 30.0


def test_the_path_is_kept(project):
    assert config(project).path == Path("agent.toml")


NUMBERS = [  # table, key, default, lowest allowed, where parse puts it
    ("agent", "concurrency", 1, 1, lambda c: c.concurrency),
    ("agent", "poll_seconds", 5, 0.1, lambda c: c.poll_seconds),
    ("agent", "job_lease_seconds", 300, 30, lambda c: c.job_lease_seconds),
    ("agent", "table_lease_seconds", 120, 15, lambda c: c.table_lease_seconds),
    ("agent", "status_seconds", 60, 5, lambda c: c.status_seconds),
    ("agent", "queue_retention_days", 30, 1, lambda c: c.queue_retention_days),
    ("agent", "max_queued", 1000, 1, lambda c: c.max_queued),
    ("retry", "max_attempts", 3, 1, lambda c: c.max_attempts),
    ("retry", "backoff_seconds", 60, 0, lambda c: c.backoff_seconds),
    ("retry", "max_backoff_seconds", 1800, 0, lambda c: c.max_backoff_seconds),
    ("pipeline", "poll_seconds", 30, 1, lambda c: c.pipeline.poll_seconds),
    ("pipeline", "retention_days", 14, 1, lambda c: c.pipeline.retention_days),
    ("incidents", "seconds", 300, 30, lambda c: c.incidents.seconds),
    ("incidents", "digest_over", 5, 1, lambda c: c.incidents.digest_over),
    ("incidents", "escalate_after_minutes", 0, 0, lambda c: c.incidents.escalate_after_minutes),
    ("shortcut_health", "minutes", 60, 15, lambda c: c.shortcut_health.minutes),
]


def _with(project, table: str, values: dict):
    if table == "agent":
        return {"agent": {"project": str(project), "environments": ["dev"], "name": "agent-1"} | values}
    return {table: values}


@pytest.mark.parametrize("table, key, default, low, read", NUMBERS, ids=[f"{t}.{k}" for t, k, *_ in NUMBERS])
def test_each_number_has_its_default_and_takes_its_lowest_value(project, table, key, default, low, read):
    assert read(config(project)) == default
    assert read(config(project, **_with(project, table, {key: low}))) == low


@pytest.mark.parametrize("table, key, default, low, read", NUMBERS, ids=[f"{t}.{k}" for t, k, *_ in NUMBERS])
def test_each_number_below_its_lowest_is_one_problem(project, table, key, default, low, read):
    below = low - 1 if low >= 1 else low / 2 if low else -1
    assert errors(project, **_with(project, table, {key: below})) == [f"{table}.{key}: a number of at least {low:g}"]


def test_a_number_that_isnt_one_is_a_problem_not_a_crash(project):
    assert errors(project, **_with(project, "agent", {"concurrency": "two", "max_queued": True})) == [
        "agent.concurrency: a number of at least 1", "agent.max_queued: a number of at least 1"]


def test_the_problem_count_is_said_in_words():
    assert str(agent_config.ConfigError("agent.toml", ["a"])) == "agent.toml has 1 problem:\n- a"
    assert str(agent_config.ConfigError("agent.toml", ["a", "b"])) == "agent.toml has 2 problems:\n- a\n- b"


def test_a_read_config_cant_be_changed(project):
    cfg = config(project, schedule=[{"id": "s1", "env": "dev", "daily": ["02:00"]}])
    with pytest.raises(AttributeError):
        cfg.name = "other"
    with pytest.raises(AttributeError):
        cfg.schedules[0].env = "prod"


@pytest.mark.parametrize("key", ["enabled", "allow_backfill", "take_over", "require_run"])
def test_pipeline_switches_are_on_only_when_written_true(project, key):
    on = config(project, pipeline={key: True})
    assert getattr(on.pipeline, key) is True
    assert getattr(config(project, pipeline={key: False}).pipeline, key) is False


def test_incidents_are_on_only_when_written_true(project):
    assert config(project, incidents={"enabled": True}).incidents.enabled is True
    assert config(project, incidents={"enabled": False}).incidents.enabled is False


def test_a_route_that_isnt_a_table_is_named_by_its_place(project):
    assert errors(project, incidents={"route": ["sales"]}) == [
        "incidents.route 1: write it as [[incidents.route]] with a name and webhook_env"]


def test_a_route_without_a_name_or_named_default_is_refused(project):
    assert errors(project, incidents={"route": [{"webhook_env": "A"}]}) == [
        'incidents.route 1: name is letters, digits, . _ or - (and not "default")']
    assert errors(project, incidents={"route": [{"name": "default", "webhook_env": "A"}]}) == [
        'incidents.route default: name is letters, digits, . _ or - (and not "default")']


def test_a_schedule_without_an_id_is_named_by_its_place(project):
    assert errors(project, schedule=[{"env": "dev", "daily": ["02:00"]}]) == [
        "schedule 1: id is letters, digits, . _ or -"]


def test_a_long_schedule_id_is_cut_to_80_characters_in_the_message(project):
    sid = "s" * 100
    assert errors(project, schedule=[{"id": sid, "env": "dev", "daily": ["02:00"]}]) == [
        f"schedule {'s' * 79}…: id is letters, digits, . _ or -"]


def test_a_long_pipeline_environment_is_cut_to_80_characters_in_the_message(project):
    env = "e" * 100
    assert errors(project, pipeline={"environments": [env]}) == [
        f"pipeline.environments: {'e' * 79}… isn't in agent.environments"]


SHARED = {"id": "s1", "env": "dev", "daily": ["02:00"], "shared": True}


def test_a_shared_schedule_reads_when_the_mailbox_takes_its_environment(project):
    cfg = config(project, pipeline={"enabled": True}, schedule=[SHARED])
    assert cfg.schedules[0].shared is True


def test_a_shared_schedule_needs_the_pipeline_on(project):
    assert errors(project, schedule=[SHARED]) == [
        "schedule s1: a shared schedule goes through the dev mailbox; enable [pipeline] for dev"]


def test_a_shared_backfill_needs_allow_backfill(project):
    backfill = dict(SHARED, backfill="full")
    assert errors(project, pipeline={"enabled": True}, schedule=[backfill]) == [
        "schedule s1: a shared backfill needs pipeline.allow_backfill = true"]
    cfg = config(project, pipeline={"enabled": True, "allow_backfill": True}, schedule=[backfill])
    assert cfg.schedules[0].backfill == "full"


def test_a_shared_schedule_id_holds_40_characters(project):
    ok = dict(SHARED, id="s" * 40)
    assert config(project, pipeline={"enabled": True}, schedule=[ok]).schedules[0].id == "s" * 40
    long = dict(SHARED, id="s" * 41)
    assert errors(project, pipeline={"enabled": True}, schedule=[long]) == [
        f"schedule {'s' * 41}: a shared schedule's id is at most 40 characters"]


def test_a_service_principal_with_a_secret_variable_reads(project):
    auth = {"type": "service_principal", "tenant_id": "t", "client_id": "c", "client_secret_env": "SP_SECRET_VAR"}
    assert config(project, auth=auth).auth["client_secret_env"] == "SP_SECRET_VAR"  # pragma: allowlist secret (an environment variable name, not a value)


def test_a_service_principal_needs_a_certificate_or_a_secret_variable(project):
    auth = {"type": "service_principal", "tenant_id": "t", "client_id": "c"}
    assert errors(project, auth=auth) == ["auth: give certificate_path (preferred) or client_secret_env"]


def test_a_missing_certificate_is_named_cut_to_200_characters(project, tmp_path):
    cert = str(tmp_path / ("c" * 240))
    auth = {"type": "service_principal", "tenant_id": "t", "client_id": "c", "certificate_path": cert}
    assert errors(project, auth=auth) == [f"auth.certificate_path: {cert[:199]}… doesn't exist"]


def test_a_project_path_the_system_refuses_is_not_a_project(tmp_path):
    bad = str(tmp_path / "bad\x00name")
    raw = {"agent": {"project": bad, "environments": ["dev"], "name": "agent-1"}, "auth": {"type": "user"}}
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    assert len(err.value.errors) == 1
    assert err.value.errors[0].startswith("agent.project: ") and err.value.errors[0].endswith(
        " isn't a metadata project (no layers.toml)")


def test_a_folder_that_isnt_a_project_is_named_cut_to_200_characters(tmp_path):
    folder = str(tmp_path / ("p" * 240))
    raw = {"agent": {"project": folder, "environments": ["dev"], "name": "agent-1"}, "auth": {"type": "user"}}
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    assert err.value.errors == [f"agent.project: {folder[:199]}… isn't a metadata project (no layers.toml)"]
