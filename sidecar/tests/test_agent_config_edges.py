"""agent.toml: the refusals and default paths the agent tests don't reach. Nothing here starts an agent."""

from types import SimpleNamespace

import pytest

from fabric_desktop import agent_config


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "layers.toml").write_text("", encoding="utf-8")
    return folder


def problems(project, **extra):
    raw = {"agent": {"project": str(project), "environments": ["dev"]}, "auth": {"type": "user"}} | extra
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    return err.value.errors


def test_the_default_paths_follow_the_environment_then_the_platform(monkeypatch):
    monkeypatch.delenv("FABRIC_DESKTOP_HOME", raising=False)
    monkeypatch.delenv("FABRIC_DESKTOP_AGENT_CONFIG", raising=False)
    monkeypatch.setenv("ProgramData", "D:/Data")
    monkeypatch.setattr(agent_config, "sys", SimpleNamespace(platform="win32"))
    assert agent_config.default_home().as_posix().endswith("D:/Data/FabricDesktop/agent")
    assert agent_config.default_config_path().as_posix().endswith("D:/Data/FabricDesktop/agent.toml")
    monkeypatch.setattr(agent_config, "sys", SimpleNamespace(platform="linux"))
    assert agent_config.default_home().as_posix() == "/var/lib/fabric-desktop"
    assert agent_config.default_config_path().as_posix() == "/etc/fabric-desktop/agent.toml"
    monkeypatch.setenv("FABRIC_DESKTOP_AGENT_CONFIG", "here/agent.toml")
    monkeypatch.setenv("FABRIC_DESKTOP_HOME", "here/home")
    assert agent_config.default_config_path().as_posix() == "here/agent.toml"
    assert agent_config.default_home().as_posix() == "here/home"


def test_a_schedule_for_an_unknown_environment_is_refused(project):
    errors = problems(project, schedule=[{"id": "s1", "env": "staging", "every_minutes": 5}])
    assert any("schedule s1: env is one of" in e for e in errors)


def test_a_shared_schedule_limited_to_some_entities_must_list_its_own(project):
    errors = problems(project, schedule=[{"id": "s1", "env": "dev", "every_minutes": 5, "shared": True}],
                      pipeline={"enabled": True, "entities": ["gold.*"]})
    assert any("s1: list its entities; pipeline.entities only takes gold.*" in e for e in errors)


def test_shortcut_health_refuses_a_non_boolean_switch_and_unknown_keys(project):
    errors = problems(project, shortcut_health={"enabled": "yes", "colour": "red"})
    assert "shortcut_health.enabled: true or false" in errors
    assert "shortcut_health: doesn't know colour" in errors


def test_loading_a_missing_or_broken_file_says_which(tmp_path):
    with pytest.raises(FileNotFoundError, match="No agent config at"):
        agent_config.load(tmp_path / "absent.toml")
    broken = tmp_path / "agent.toml"
    broken.write_text("[agent\n", encoding="utf-8")
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.load(broken)
    assert err.value.errors[0].startswith("not valid TOML")


def test_secret_keys_are_named_with_their_path_in_order():
    raw = {"agent": {"token": "x", "list": [{"password": 1}, [{"Secret": None}]]}, "z": {"a": {"api_key": "k"}}}
    tail = ": secrets don't go in agent.toml; put it in an environment variable and give its name as "
    assert agent_config._secret_keys(raw, "") == [
        f"agent.token{tail}token_env", f"agent.list.[0].password{tail}password_env",
        f"agent.list.[1].[0].Secret{tail}Secret_env", f"z.a.api_key{tail}api_key_env"]
    assert agent_config._secret_keys({"a": {"token": 1}, "token": 2}, "") == [f"a.token{tail}token_env", f"token{tail}token_env"]


def test_a_file_with_a_dotted_key_thousands_of_parts_long_is_checked(tmp_path):
    """TOML's dotted keys (a.b.c = 1) make tables as deep as the key is long without any nesting in the
    text; checking them for secrets used to raise RecursionError from load()."""
    path = tmp_path / "agent.toml"
    path.write_text("x." + ".".join(["a"] * 5000) + ".token = 1\n", encoding="utf-8")
    with pytest.raises(agent_config.ConfigError) as caught:
        agent_config.load(path)
    assert any(e.startswith("x.a.a.") and ".token: secrets don't go in agent.toml" in e for e in caught.value.errors)
