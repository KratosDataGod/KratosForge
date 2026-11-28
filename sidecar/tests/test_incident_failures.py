"""The incident list when things go wrong: a document that can't be read, a write that keeps losing
the race, a store that fails mid-write. Each case leaves the stored list exactly as it was, says why,
and never stops the agent's other work; the next evaluation brings the list up to date once."""

import logging

import pytest

from fabric_desktop.leases import LocalBackend
from fabric_desktop.workloads import incidents as incidents_mod
from fabric_desktop.workloads.incidents import KEY, Conflict, IncidentLog

from test_agent import Clock
from test_incidents import agent_setup, failed, run  # noqa: F401 - agent_setup is a fixture


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(tmp_path / "control")


def _stored(backend):
    got = backend.read(KEY)
    return got.data if got is not None else None


@pytest.mark.parametrize("data", [b"{not json", b"", b"[1, 2]", b'{"incidents": "none"}'])
def test_an_unreadable_list_is_never_written_over_and_says_how_to_recover(backend, data):
    backend.create(KEY, data)
    log = IncidentLog(backend, "prod")
    for change in (lambda: log.sync(failed(), evaluated=True, by="agent a"),
                   lambda: log.report("Revenue looks doubled", "dana"),
                   log.list):
        with pytest.raises(ValueError, match="can't be read") as err:
            change()
        assert "restore it from a backup" in str(err.value)
    assert _stored(backend) == data


def test_a_write_that_keeps_losing_the_race_gives_up_and_changes_nothing(backend, monkeypatch):
    log = IncidentLog(backend, "prod")
    log.sync(failed("silver.orders", "r1"), evaluated=True, by="agent a")
    before = _stored(backend)
    tries = []

    def lose(key, data, etag):
        tries.append(etag)
        return None  # another writer changed it first, every time

    monkeypatch.setattr(backend, "replace", lose)
    monkeypatch.setattr(incidents_mod.time, "sleep", lambda s: None)
    with pytest.raises(Conflict, match="kept changing"):
        log.sync(failed("silver.customers", "r2"), evaluated=True, by="agent a")
    assert len(tries) == incidents_mod.ATTEMPTS
    assert _stored(backend) == before


def test_a_store_that_fails_mid_write_leaves_the_list_as_it_was(backend, monkeypatch):
    log = IncidentLog(backend, "prod")
    log.sync(failed("silver.orders", "r1"), evaluated=True, by="agent a")
    before = _stored(backend)

    def down(key, data, etag):
        raise OSError("the network went away")

    monkeypatch.setattr(backend, "replace", down)
    with pytest.raises(OSError):
        log.sync(failed("silver.customers", "r2"), evaluated=True, by="agent a")
    assert _stored(backend) == before
    [inc] = IncidentLog(backend, "prod").active()  # still the one incident, readable
    assert inc["workload"].endswith(":silver.orders")


def test_a_store_that_cant_be_read_is_reported_and_nothing_is_written(backend, monkeypatch):
    log = IncidentLog(backend, "prod")

    def unreachable(key):
        raise ConnectionError("OneLake isn't answering")

    monkeypatch.setattr(backend, "read", unreachable)
    monkeypatch.setattr(backend, "create", lambda *a: pytest.fail("nothing may be written"))
    with pytest.raises(ConnectionError):
        log.sync(failed(), evaluated=True, by="agent a")


def test_the_agent_carries_on_when_the_list_cant_be_written_and_opens_the_incident_once_later(
        agent_setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    from fabric_desktop.agent import Agent
    agent = Agent(agent_setup, clock=clock)
    name = sorted(agent.runtime.entity_names())[0]
    monkeypatch.setattr(agent.runtime, "meta_runs", lambda env, limit: [run(name, "failed", "r1", message="boom")])
    real = IncidentLog._change
    broken = {"on": True}

    def flaky(self, fn):
        if broken["on"]:
            raise OSError("the network went away")
        return real(self, fn)

    monkeypatch.setattr(IncidentLog, "_change", flaky)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        assert agent.evaluate_incidents() == {}  # no crash; the environment is skipped this time
    assert "couldn't update the incident list" in caplog.text
    assert "dev" not in agent._controls  # opened afresh next time
    broken["on"] = False
    first = agent.evaluate_incidents()
    [c] = first["dev"]
    assert c["change"] == "opened"
    assert agent.evaluate_incidents() == {"dev": []}  # seen again: nothing new
    assert len(IncidentLog(agent._control("dev"), "dev").active()) == 1
