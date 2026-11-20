"""agent_config.py at its exact edges (mutation batch 164, a boundary re-score of batch 24,
docs/quality/mutation-testing.md): a schedule every 1 minute and every MAX_EVERY_MINUTES is read, and a
shared schedule every 1 minute is read."""

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


def config(project, **extra):
    raw = {"agent": {"project": str(project), "environments": ["dev"], "name": "agent-1"},
           "auth": {"type": "user"}} | extra
    return agent_config.parse(raw)


def _every(minutes, **more):
    return {"id": "s1", "env": "dev", "entities": ["silver.orders"], "every_minutes": minutes} | more


@pytest.mark.parametrize("minutes", [1, agent_config.MAX_EVERY_MINUTES])
def test_every_1_minute_and_the_longest_interval_are_read(project, minutes):
    assert config(project, schedule=[_every(minutes)]).schedules[0].when["every_minutes"] == minutes


@pytest.mark.parametrize("minutes", [0, agent_config.MAX_EVERY_MINUTES + 1])
def test_0_minutes_and_one_more_than_the_longest_are_refused(project, minutes):
    with pytest.raises(agent_config.ConfigError, match="every_minutes is a whole number from 1 to"):
        config(project, schedule=[_every(minutes)])


def test_a_shared_schedule_every_1_minute_is_read(project):
    cfg = config(project, pipeline={"enabled": True}, schedule=[_every(1, shared=True)])
    assert cfg.schedules[0].shared is True
