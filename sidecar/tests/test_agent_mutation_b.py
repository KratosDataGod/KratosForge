"""Mutation batch 511, a re-score of agent.py (batches 17 and 163). No test reaches Fabric, OneLake,
Teams or the network."""

from __future__ import annotations

import dataclasses

from fabric_desktop.agent import Agent

from test_agent import setup  # noqa: F401 - setup is a fixture


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
