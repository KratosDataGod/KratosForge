"""Run requests from Fabric pipelines (mailbox.py), the paths the main tests don't reach: requests
too big or with fields of the wrong kind, a status file that isn't JSON, a lock holder that can't be
read, and stores that have no control files. Each refusal is recorded with why; nothing runs."""

from __future__ import annotations

import pytest

from fabric_desktop import mailbox
from fabric_desktop.agent import Agent
from test_mailbox import run, setup, status, write_request  # noqa: F401 - setup is a fixture


@pytest.mark.parametrize("body, reason", [
    ({"env": "dev", "submitted_by": "x" * (mailbox.MAX_REQUEST_BYTES + 1)}, "bytes; the most is"),
    ({"env": "dev", "backfill": "partial"}, 'backfill is "" or "full"'),
    ({"env": "dev", "entities": ["bronze.orders"], "cascade": "yes"}, "cascade is true or false"),
])
def test_requests_too_big_or_with_fields_of_the_wrong_kind_are_rejected(setup, body, reason):  # noqa: F811
    agent = Agent(setup)
    write_request(setup, "bad", body, age=120)
    run(agent)
    s = status(setup, "bad")
    assert s["state"] == "rejected" and reason in s["reason"], s
    assert sum(agent.queue.counts().values()) == 0


def test_a_status_file_that_isnt_json_is_read_as_no_status(setup):  # noqa: F811
    agent = Agent(setup)
    folder = setup.project / ".local" / "dev" / "_control" / "status"
    folder.mkdir(parents=True)
    (folder / "garbled.json").write_text("{half a status", encoding="utf-8")
    body, stored = agent.mailbox._read("dev", "status/garbled.json")
    assert body is None and stored is not None


def test_a_lock_holder_that_cant_be_read_names_nobody(tmp_path):
    assert mailbox._holder(tmp_path / "missing.lock") == {}
    (tmp_path / "garbled.lock").write_bytes(b" {not json")
    assert mailbox._holder(tmp_path / "garbled.lock") == {}
    (tmp_path / "list.lock").write_bytes(b" [1, 2]")
    assert mailbox._holder(tmp_path / "list.lock") == {}


def test_a_store_without_control_files_says_so():
    with pytest.raises(TypeError, match="no control files for object"):
        mailbox.control_backend_for(object(), None, lambda: "token")
