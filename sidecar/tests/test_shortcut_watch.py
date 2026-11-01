"""PB-053: the agent checks shortcut health on a schedule and posts each break to Teams once."""

import datetime as dt
import json
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop import agent as agent_mod
from fabric_desktop import agent_config, agent_status, shortcut_watch
from fabric_desktop.agent import Agent
from fabric_desktop.meta.templates import init_project

T0 = 1_800_000_000.0
HOOK = "https://example.invalid/webhook/shortcuts"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def config(tmp_path, block: str, name: str = "etl-01", alerts: str = ""):
    project = tmp_path / "proj"
    if not project.exists():
        init_project(project, "3", "Chris", True)
    path = tmp_path / f"{name}.toml"
    path.write_text(f"""
[agent]
name = "{name}"
home = {json.dumps(str(tmp_path / "home"))}
project = {json.dumps(str(project))}
environments = ["test", "prod"]

[auth]
type = "user"

[alerts]
{alerts}

{block}
""", encoding="utf-8")
    return agent_config.load(path)


ON = """
[shortcut_health]
enabled = true
environments = ["prod"]
webhook_env = "SHORTCUTS_HOOK"
"""


# ---- agent.toml

def test_off_unless_turned_on_and_needs_a_channel(tmp_path, monkeypatch):
    assert config(tmp_path, "").shortcut_health.enabled is False
    cfg = config(tmp_path, ON)
    assert cfg.shortcut_health.enabled and cfg.shortcut_health.minutes == 60 and cfg.shortcut_health.environments == ["prod"]
    monkeypatch.setenv("SHORTCUTS_HOOK", HOOK)
    assert cfg.shortcut_webhook == HOOK
    # alerts.teams_webhook_env is the channel when the section names none; environments default to the agent's
    monkeypatch.setenv("ALERTS_HOOK", "https://example.invalid/alerts")
    cfg = config(tmp_path, "[shortcut_health]\nenabled = true", alerts='teams_webhook_env = "ALERTS_HOOK"')
    assert cfg.shortcut_webhook == "https://example.invalid/alerts" and cfg.shortcut_health.environments == ["test", "prod"]
    with pytest.raises(agent_config.ConfigError) as err:
        config(tmp_path, '[shortcut_health]\nenabled = true\nminutes = 5\nenvironments = ["dev"]\nurl = "https://x"')
    text = str(err.value)
    assert "so breaks go to a Teams channel" in text and "shortcut_health.minutes" in text
    assert "dev isn't in agent.environments" in text and "webhook_env" in text
    # a string isn't read letter by letter as environments "p", "r", "o", "d"
    with pytest.raises(agent_config.ConfigError, match="shortcut_health.environments: a list"):
        config(tmp_path, '[shortcut_health]\nenabled = true\nenvironments = "prod"\nwebhook_env = "X"')


# ---- the check itself: reads only

class FakeFabric:
    """Only listing calls: any other call fails the test."""

    def __init__(self, shortcuts, fail_workspaces=()):
        self.shortcuts, self.fail, self.calls = shortcuts, set(fail_workspaces), []

    def list_lakehouses(self, ws):
        self.calls.append(("lakehouses", ws))
        if ws in self.fail:
            raise RuntimeError("capacity paused")
        return [{"id": f"lh-{ws}", "displayName": f"LH_{ws}"}]

    def list_shortcuts(self, ws, lh):
        self.calls.append(("shortcuts", ws, lh))
        return self.shortcuts.get(lh, [])


class FakeFiles:
    def __init__(self, status):
        self.status, self.calls = status, []

    def probe(self, ws, lh, directory):
        self.calls.append((ws, lh, directory))
        return self.status.get(directory, 200), "" if self.status.get(directory, 200) < 400 else "PathNotFound"


def onelake_shortcut(name, path="Tables/dbo"):
    return {"path": path, "name": name, "target": {"oneLake": {"workspaceId": "w2", "itemId": "i2",
                                                               "path": f"Tables/{name}"}}}


def test_scan_lists_and_probes_only(tmp_path):
    fabric = FakeFabric({"lh-ws1": [onelake_shortcut("orders"), onelake_shortcut("customers")]}, fail_workspaces=["ws2"])
    files = FakeFiles({"Tables/dbo/orders": 404})
    rows, problems = shortcut_watch.scan(["ws1", "ws2"], fabric, fabric, files)
    by = {r["name"]: r for r in rows}
    assert by["orders"]["health"] == "broken" and "isn't there any more" in by["orders"]["health_detail"]
    assert by["customers"]["health"] == "healthy" and by["orders"]["id"] == "lh-ws1|Tables/dbo/orders"
    assert len(problems) == 1 and "ws2" in problems[0] and "capacity paused" in problems[0]
    # the folder they sit in is listed once, then each shortcut once
    assert sorted(c[2] for c in files.calls) == ["Tables/dbo", "Tables/dbo/customers", "Tables/dbo/orders"]
    events = shortcut_watch.events(rows, "prod", "2027-01-01T00:00:00+00:00")
    assert [(e["kind"], e["env"]) for e in events] == [("shortcut.broken", "prod"), ("shortcut.fixed", "prod")]


def test_env_workspaces_take_each_designation():
    cfg = {"workspace_id": "a", "workspaces": {"gold": {"id": "b"}, "silver": {"id": "a"}, "x": {}}}
    assert shortcut_watch.env_workspaces(cfg) == ["a", "b"]
    assert shortcut_watch.env_workspaces({"target": "local"}) == []


# ---- the agent: on a schedule, once per break

class FakeCred:
    def fabric_token(self):
        return "fabric"

    def storage_token(self):
        return "storage"

    def _token(self, scopes):
        return "onelake"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An agent checking Prod's one lakehouse, with Teams, OneLake and the status records faked."""
    monkeypatch.setenv("SHORTCUTS_HOOK", HOOK)
    state = {"status": {}, "records": [], "posts": [], "scans": 0, "read_fails": False}

    class Fabric:
        def __init__(self, token):
            assert token() in ("fabric", "onelake")

        def list_lakehouses(self, ws):
            state["scans"] += 1
            return [{"id": "lh1", "displayName": "LH_Gold"}]

        def list_shortcuts(self, ws, lh):
            return [onelake_shortcut("orders")]

    class Files:
        def __init__(self, token):
            assert token() == "storage"

        def probe(self, ws, lh, directory):
            status = state["status"].get(directory, 200)
            return status, "" if status < 400 else "PathNotFound"

    def records(backend):
        if state["read_fails"]:
            raise OSError("LH_Meta unreachable")
        return state["records"]

    monkeypatch.setattr(agent_mod, "FabricClient", Fabric)
    monkeypatch.setattr(agent_mod, "FileStore", Files)
    monkeypatch.setattr(agent_mod, "read_status", records)
    monkeypatch.setattr(agent_mod, "post_teams", lambda url, title, lines: state["posts"].append((url, title, lines)))

    def make(name="etl-01", clock=None):
        a = Agent(config(tmp_path, ON, name=name), clock=clock or Clock())
        a.runtime._credential = FakeCred()
        monkeypatch.setattr(a.runtime, "_project", lambda: SimpleNamespace(
            environments={"prod": {"workspace_id": "ws-prod"}, "test": {"workspace_id": "ws-test"}}))
        monkeypatch.setattr(a, "_control", lambda env: object())
        monkeypatch.setattr(a, "publish_status", lambda state="running": None)
        return a

    state["make"] = make
    return state


def titles(world):
    return [t for _, t, _ in world["posts"]]


def step(agent):
    agent.step()
    if agent._shortcut_thread is not None:
        agent._shortcut_thread.join(timeout=30)


def test_the_agent_runs_the_check_on_its_schedule(world):
    clock = Clock()
    agent = world["make"](clock=clock)
    step(agent)
    assert world["scans"] == 1  # at start
    clock.t += 59 * 60
    step(agent)
    assert world["scans"] == 1  # not yet: every 60 minutes
    clock.t += 60
    step(agent)
    assert world["scans"] == 2


def test_a_slow_check_runs_beside_the_loop(world, monkeypatch):
    clock = Clock()
    agent = world["make"](clock=clock)
    started, release = threading.Event(), threading.Event()
    real = shortcut_watch.scan

    def slow(*args):
        started.set()
        assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(shortcut_watch, "scan", slow)
    agent.step()  # returns while the check waits
    assert started.wait(30) and agent._shortcut_thread.is_alive()
    clock.t += 3600
    agent.step()  # due again, but the last check hasn't finished: not a second one
    assert agent.start_shortcut_check() is None
    release.set()
    agent._shortcut_thread.join(timeout=30)
    assert world["scans"] == 1


def test_a_bad_message_doesnt_drop_the_others(world, monkeypatch, caplog):
    good = {"kind": "shortcut.broken", "id": "lh1|Tables/dbo/orders", "title": "orders in LH_Gold",
            "at": "2027-01-01T00:00:00+00:00", "to": {"roles": ["platform_admin"]},
            "open": {"view": "shortcuts", "tab": "health"}}
    monkeypatch.setattr(shortcut_watch, "events", lambda rows, env, at: [good | {"title": ""}, good])
    with caplog.at_level("WARNING"):
        world["make"]().check_shortcuts()
    assert titles(world) == ["Shortcut broken: orders in LH_Gold"]
    assert "a shortcut message was left out" in caplog.text


def test_a_break_posts_once_and_a_new_break_posts_again(world):
    clock = Clock()
    agent = world["make"](clock=clock)
    world["status"]["Tables/dbo/orders"] = 404
    agent.check_shortcuts()
    agent.check_shortcuts()
    assert titles(world) == ["Shortcut broken: orders in LH_Gold"]
    url, _, lines = world["posts"][0]
    assert url == HOOK and "Prod" in lines and any("isn't there any more" in l for l in lines)
    assert lines[-1] == "Open it in KratosForge: Shortcuts > Health."

    # still broken after a restart of the agent: the ledger in its home remembers
    world["make"](clock=clock).check_shortcuts()
    assert len(world["posts"]) == 1

    world["status"]["Tables/dbo/orders"] = 200  # fixed: posted once, and the break is closed
    agent.check_shortcuts()
    agent.check_shortcuts()
    assert titles(world)[1:] == ["Shortcut fixed: orders in LH_Gold"]

    world["status"]["Tables/dbo/orders"] = 404  # broken again: a new break, posted again
    agent.check_shortcuts()
    agent.check_shortcuts()
    assert titles(world).count("Shortcut broken: orders in LH_Gold") == 2 and len(world["posts"]) == 3


def test_unreachable_says_nothing_and_a_healthy_shortcut_posts_no_fix(world):
    agent = world["make"]()
    world["status"]["Tables/dbo/orders"] = 403  # no access to the OneLake target: can't tell
    agent.check_shortcuts()
    world["status"]["Tables/dbo/orders"] = 200
    agent.check_shortcuts()
    assert world["posts"] == []


@pytest.mark.parametrize("busy", [429, 503])
def test_a_busy_onelake_posts_nothing_and_doesnt_close_a_break(world, busy):
    agent = world["make"]()
    world["status"]["Tables/dbo/orders"] = busy  # OneLake still busy after the probe's own retries
    agent.check_shortcuts()
    assert world["posts"] == []

    world["status"]["Tables/dbo/orders"] = 404
    agent.check_shortcuts()
    world["status"]["Tables/dbo/orders"] = busy  # busy while broken: no "fixed", no second "broken"
    agent.check_shortcuts()
    assert titles(world) == ["Shortcut broken: orders in LH_Gold"]


def test_teams_down_keeps_the_break_for_the_next_check(world, monkeypatch):
    agent = world["make"]()
    world["status"]["Tables/dbo/orders"] = 404

    def down(url, title, lines):
        raise ConnectionError(f"can't reach {url}")

    monkeypatch.setattr(agent_mod, "post_teams", down)
    agent.check_shortcuts()
    monkeypatch.setattr(agent_mod, "post_teams", lambda url, title, lines: world["posts"].append((url, title, lines)))
    agent.check_shortcuts()
    assert titles(world) == ["Delayed: Shortcut broken: orders in LH_Gold"]


def _record(name, enabled=True, stale_in=300.0):
    stale = dt.datetime.fromtimestamp(T0 + stale_in, dt.UTC).isoformat()
    return {"kind": "agent_status", "agent": name, "state": "running", "stale_after": stale,
            "shortcut_health": {"enabled": enabled, "minutes": 60}}


def test_one_agent_posts_when_several_check_the_environment(world):
    world["status"]["Tables/dbo/orders"] = 404
    world["records"] = [_record("aaa-01"), _record("etl-01")]
    out = world["make"]("etl-01").check_shortcuts()
    assert out["prod"] == {"skipped": "another agent checks them"} and world["scans"] == 0 and world["posts"] == []
    # the first by name posts; an agent that doesn't check, one that sorts later, or one offline doesn't stop it
    world["records"] = [_record("aaa-01", enabled=False), _record("zzz-01"), _record("aaa-02", stale_in=-1)]
    assert world["make"]("etl-01").check_shortcuts()["prod"] == {"checked": 1, "broken": 1}
    assert len(world["posts"]) == 1


def test_unreadable_status_records_still_check(world):
    world["status"]["Tables/dbo/orders"] = 404
    world["read_fails"] = True
    assert world["make"]().check_shortcuts()["prod"]["broken"] == 1 and len(world["posts"]) == 1


def test_no_webhook_set_checks_nothing(world, monkeypatch):
    monkeypatch.delenv("SHORTCUTS_HOOK")
    assert world["make"]().check_shortcuts() == {} and world["scans"] == 0


def test_the_status_record_says_whether_it_checks(tmp_path):
    cfg = config(tmp_path, ON)
    assert agent_status.shortcut_health_summary(cfg, "prod") == {"enabled": True, "minutes": 60.0}
    assert agent_status.shortcut_health_summary(cfg, "test")["enabled"] is False
    assert agent_status.shortcut_health_summary(config(tmp_path, ""), "prod")["enabled"] is False


def test_two_threads_asking_for_a_control_backend_build_it_once(monkeypatch):
    import threading as th
    import time
    from fabric_desktop import agent as agent_mod

    built = []

    class Runtime:
        def control_backend(self, env):
            built.append(env)
            time.sleep(0.05)  # widen the window a check-then-set race would need
            return object()

    a = agent_mod.Agent.__new__(agent_mod.Agent)
    a._controls, a._controls_lock, a.runtime = {}, th.Lock(), Runtime()
    got = []
    threads = [th.Thread(target=lambda: got.append(a._control("prod"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert built == ["prod"] and len({id(x) for x in got}) == 1
