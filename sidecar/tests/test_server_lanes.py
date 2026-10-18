"""HD-041 (SH-11): a long call doesn't hold up the quick ones, and every reply carries its request's id."""

import io
import json
import re
import threading
import time
from pathlib import Path

from fabric_desktop import server


class Engine:
    """Stands in for Service: meta_run is long, poll_cell and app_settings are quick."""

    def __init__(self) -> None:
        self.quick_answered = threading.Event()
        self.order: list[str] = []

    def meta_run(self, names=None, env="dev", **_):
        # Waits for the quick call: one pipe that runs a call at a time never gets to it.
        saw_quick = self.quick_answered.wait(timeout=5)
        self.order.append(f"meta_run:{env}")
        return {"saw_quick": saw_quick, "env": env}

    def history(self, limit=50):
        self.order.append(f"history:{limit}")
        return [limit]

    def poll_cell(self, version=-1, wait=0.0):
        self.quick_answered.set()
        return {"version": version}

    def app_settings(self):
        return {"ok": True}


def _serve(requests: list[dict], engine: Engine) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    started = time.monotonic()
    server.serve(engine, stdin=stdin, stdout=stdout)
    assert time.monotonic() - started < 10
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_a_quick_call_is_answered_while_a_long_call_runs():
    engine = Engine()
    replies = _serve([
        {"id": 1, "method": "meta_run", "params": {"env": "dev"}},
        {"id": 2, "method": "poll_cell", "params": {"version": 7}},
    ], engine)
    assert [r["id"] for r in replies] == [2, 1]  # the quick reply came first, out of order
    by_id = {r["id"]: r for r in replies}
    assert by_id[2]["result"] == {"version": 7}
    assert by_id[1]["result"] == {"saw_quick": True, "env": "dev"}


def test_every_reply_carries_its_own_request_id():
    engine = Engine()
    engine.quick_answered.set()
    requests = []
    for i in range(1, 41):
        if i % 3 == 0:
            requests.append({"id": i, "method": "history", "params": {"limit": i}})
        elif i % 3 == 1:
            requests.append({"id": i, "method": "poll_cell", "params": {"version": i}})
        else:
            requests.append({"id": i, "method": "app_settings", "params": {}})
    replies = _serve(requests, engine)
    assert sorted(r["id"] for r in replies) == list(range(1, 41))
    for reply in replies:
        i = reply["id"]
        if i % 3 == 0:
            assert reply["result"] == [i]
        elif i % 3 == 1:
            assert reply["result"] == {"version": i}
        else:
            assert reply["result"] == {"ok": True}


def test_calls_that_are_not_quick_still_run_one_at_a_time_in_order():
    engine = Engine()
    engine.quick_answered.set()
    replies = _serve([
        {"id": 1, "method": "meta_run", "params": {"env": "test"}},
        {"id": 2, "method": "history", "params": {"limit": 3}},
        {"id": 3, "method": "meta_run", "params": {"env": "prod"}},
    ], engine)
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert engine.order == ["meta_run:test", "history:3", "meta_run:prod"]


def test_errors_and_bad_lines_are_still_answered():
    replies = _serve([
        {"id": 5, "method": "not_a_method", "params": {}},
        {"id": 6, "method": "poll_cell", "params": {"nope": 1}},
    ], Engine())
    by_id = {r["id"]: r for r in replies}
    assert "unknown method" in by_id[5]["error"]
    assert "TypeError" in by_id[6]["error"]
    stdout = io.StringIO()
    server.serve(Engine(), stdin=io.StringIO("not json\n"), stdout=stdout)
    reply = json.loads(stdout.getvalue())
    assert reply["id"] is None and "invalid JSON" in reply["error"]


def test_quick_calls_match_the_shell_time_limit_list():
    watchdog = (Path(__file__).parents[2] / "app" / "src-tauri" / "src" / "watchdog.rs").read_text(encoding="utf-8")
    block = re.search(r"pub const QUICK: &\[&str\] = &\[(.*?)\];", watchdog, re.S)
    assert block, "QUICK not found in watchdog.rs"
    assert set(re.findall(r'"([a-z_]+)"', block.group(1))) == server.QUICK
    assert server.QUICK <= server.METHODS


def test_notebook_writes_stay_in_order_with_publish():
    # Publish saves the cells it was given before uploading; if a later autosave could overtake a
    # publish queued behind a long call, the publish would write the older cells over it.
    writers = {"new_notebook", "save_notebook", "rename_notebook", "delete_notebook", "publish_notebook"}
    assert writers <= server.METHODS and not writers & server.QUICK
