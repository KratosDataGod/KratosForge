"""Mutation batch 511, a re-score of agent.py (batches 17 and 163). No test reaches Fabric, OneLake,
Teams or the network."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop import agent as agent_mod
from fabric_desktop import shortcut_watch
from fabric_desktop.agent import Agent, JobLeaseLost
from fabric_desktop.meta.engine import Abandoned

from test_agent import setup  # noqa: F401 - setup is a fixture
from test_shortcut_watch import world  # noqa: F401 - a fixture


def test_a_released_folder_isnt_held_again_when_its_control_files_are_rebuilt(setup):  # noqa: F811
    agent = Agent(setup)
    agent.hold_control_folders()
    assert len(agent._held) == 1
    agent.release_control_folders()
    agent._controls.pop("dev", None)  # as a failed status write drops them, to build them again
    agent._control("dev")
    assert agent._held == {}
    other = Agent(dataclasses.replace(setup, name="etl-02"))
    other.hold_control_folders()  # free for another agent
    assert len(other._held) == 1
    other.release_control_folders()


def test_a_jobs_lease_is_renewed_every_third_of_it(setup):  # noqa: F811
    agent = Agent(dataclasses.replace(setup, job_lease_seconds=300.0))
    waits = []

    class Done:
        def wait(self, seconds):
            waits.append(seconds)
            return True  # the job ended before the first beat
    agent._heartbeat({"id": "j1"}, Done())
    assert waits == [100.0]


@pytest.mark.parametrize("state, alerted", [("queued", []), ("dead", [("j1", "not recorded")])])
def test_a_prod_job_whose_audit_line_cant_be_written_is_alerted_only_when_it_has_no_tries_left(
        setup, monkeypatch, state, alerted):  # noqa: F811
    agent = Agent(setup)
    monkeypatch.setattr(agent_mod, "fails_closed", lambda what, params: True)

    def unwritable(*args):
        raise OSError("audit folder read-only")
    monkeypatch.setattr(agent.audit, "record", unwritable)
    monkeypatch.setattr(agent.queue, "retry_later", lambda *args, **kw: {"state": state, "error": "not recorded"})
    alerts = []
    monkeypatch.setattr(agent, "_alert", lambda job, error, crashed=False: alerts.append((job["id"], error)))
    agent.run_job({"id": "j1", "payload": {"env": "dev", "entities": []}, "attempts": 3, "source": "cli"})
    assert alerts == alerted


@pytest.mark.parametrize("stop, outcome", [(JobLeaseLost("lease ran out"), "lease_lost"),
                                           (Abandoned("taken over"), "handed_over")])
def test_a_job_that_stops_part_way_is_audited_with_the_seconds_it_ran(setup, monkeypatch, stop, outcome):  # noqa: F811
    agent = Agent(setup)

    def run_environment(*args, **kwargs):
        raise stop
    monkeypatch.setattr(agent.runtime, "run_environment", run_environment)
    monkeypatch.setattr(agent.queue, "cancel", lambda *args, **kw: None)
    audited = []
    monkeypatch.setattr(agent, "_audit", lambda job, result, seconds, **kw: audited.append((result, seconds)))
    agent.run_job({"id": "j1", "payload": {"env": "dev", "entities": []}, "attempts": 1, "source": "cli"})
    [(result, seconds)] = audited
    assert result == outcome and 0 <= seconds < 60


def test_an_alert_carries_the_first_1500_characters_of_the_error(setup, monkeypatch):  # noqa: F811
    agent = Agent(setup)
    sent = []
    monkeypatch.setattr(agent, "_notify", lambda title, lines, **about: sent.append(lines))
    agent._alert({"id": "j1", "payload": {"env": "dev"}, "source": "cli"}, "e" * 2000)
    assert sent[0][1] == "e" * 1500


def test_a_shortcut_message_left_out_is_logged_with_its_id_cut_to_200(world, monkeypatch, caplog):  # noqa: F811
    bad = {"kind": "shortcut.broken", "id": "x" * 300, "title": "", "at": "2027-01-01T00:00:00+00:00",
           "to": {"roles": ["platform_admin"]}, "open": {"view": "shortcuts", "tab": "health"}}
    monkeypatch.setattr(shortcut_watch, "events", lambda rows, env, at: [bad])
    with caplog.at_level("WARNING"):
        world["make"]().check_shortcuts()
    [left] = [r for r in caplog.records if r.getMessage() == "a shortcut message was left out"]
    assert left.fields["shortcut"] == "x" * 200
