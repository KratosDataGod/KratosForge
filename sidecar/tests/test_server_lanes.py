"""HD-041 (SH-11): a long call doesn't hold up the quick ones, and every reply carries its request's id."""

import io
import json
import re
import threading
import time
from pathlib import Path

import pytest

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
    writers = {"new_notebook", "save_notebook", "rename_notebook", "notebook_delete", "publish_notebook"}
    assert writers <= server.METHODS and not writers & server.QUICK


def test_concurrent_saves_of_one_notebook_never_share_a_temp_file(tmp_path):
    import threading

    from fabric_desktop.notebook import NotebookStore
    store = NotebookStore(tmp_path)
    store.save("nb", [{"type": "code", "source": "0"}], None)
    errors: list[Exception] = []

    def save(i: int) -> None:
        try:
            for _ in range(20):
                store.save("nb", [{"type": "code", "source": str(i)}], None)
        except Exception as exc:  # noqa: BLE001 - collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and not list(tmp_path.rglob("*.tmp"))


def test_a_save_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    # Windows refuses a replace while a reader or scanner briefly has the notebook open.
    from fabric_desktop import notebook
    store = notebook.NotebookStore(tmp_path)
    store.save("nb", [{"type": "code", "source": "0"}], None)
    real, calls = notebook.os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(notebook.os, "replace", flaky)
    monkeypatch.setattr(notebook.time, "sleep", lambda _s: None)
    store.save("nb", [{"type": "code", "source": "1"}], None)
    assert len(calls) == 3 and store.load("nb")["cells"][0]["source"] == "1"
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_save_that_stays_refused_reports_it_and_leaves_no_temp_file(tmp_path, monkeypatch):
    import pytest

    from fabric_desktop import notebook
    store = notebook.NotebookStore(tmp_path)

    def refused(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(notebook.os, "replace", refused)
    monkeypatch.setattr(notebook.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        store.save("nb", [{"type": "code", "source": "1"}], None)
    assert not list(tmp_path.rglob("*.tmp"))


class _Lines:
    """The shell's side of the pipes: requests sent one at a time, replies read by id as they come."""

    def __init__(self) -> None:
        import queue
        self.sent: queue.Queue = queue.Queue()
        self.replies: dict[int, dict] = {}
        self.arrived = threading.Condition()

    def __iter__(self):
        while (line := self.sent.get()) is not None:
            yield line

    def write(self, text: str) -> None:
        for line in text.splitlines():
            reply = json.loads(line)
            with self.arrived:
                self.replies[reply["id"]] = reply
                self.arrived.notify_all()

    def flush(self) -> None:
        pass

    def send(self, rid: int, method: str, **params) -> None:
        self.sent.put(json.dumps({"id": rid, "method": method, "params": params}) + "\n")

    def reply(self, rid: int, timeout: float) -> dict:
        with self.arrived:
            assert self.arrived.wait_for(lambda: rid in self.replies, timeout), f"no reply to {rid} in {timeout} s"
            return self.replies[rid]


class _KernelEngine:
    """Stands in for Service's kernel calls, on a real kernel."""

    def __init__(self, kernel) -> None:
        self.kernel = kernel

    def start_cell(self, code, breakpoints=None):
        return self.kernel.start(code, breakpoints=breakpoints)

    def poll_cell(self, version=-1, wait=0.0):
        return self.kernel.poll(version, wait)

    def interrupt_cell(self):
        return self.kernel.interrupt()

    def debug_cell(self, action, expression=None):
        return self.kernel.debug(action, expression)


@pytest.mark.parametrize("kind", ["engine", "process"])
def test_stop_reaches_a_cell_while_an_expression_it_evaluates_hangs(kind):
    # debug_cell eval waits up to debugger.EVAL_SECONDS for its answer: Stop and the cell's
    # output must not wait behind it.
    from fabric_desktop import debugger
    from fabric_desktop.kernel_proc import ProcessKernel
    from fabric_desktop.notebook import Kernel
    kernel = Kernel() if kind == "engine" else ProcessKernel()
    pipe = _Lines()
    engine = threading.Thread(target=server.serve, args=(_KernelEngine(kernel), pipe, pipe), daemon=True)
    engine.start()
    try:
        pipe.send(1, "start_cell", code="a = 1\nb = 2", breakpoints=[2])
        assert "result" in pipe.reply(1, 30), pipe.replies[1]
        deadline, rid = time.monotonic() + 30, 100
        pipe.send(rid, "poll_cell", wait=0.3)
        while not (snap := pipe.reply(rid, 30)["result"]).get("paused"):
            assert snap["status"] == "running" and time.monotonic() < deadline, snap
            rid += 1
            pipe.send(rid, "poll_cell", wait=0.3)
        pipe.send(2, "debug_cell", action="eval", expression="sum(1 for _ in iter(int, 1))")
        time.sleep(0.3)  # the eval is waiting for its answer
        sent = time.monotonic()
        pipe.send(3, "poll_cell")
        pipe.send(4, "interrupt_cell")
        assert "result" in pipe.reply(3, 2), pipe.replies[3]
        assert pipe.reply(4, 2)["result"]["interrupted"] is True
        assert time.monotonic() - sent < 2
        deadline = time.monotonic() + 15
        while (snap := kernel.poll(-1, 0.3))["status"] == "running":
            assert time.monotonic() < deadline, "the cell still ran after Stop"
        assert snap["status"] == "cancelled" and not snap.get("paused"), snap
        assert pipe.reply(2, debugger.EVAL_SECONDS + 5)["result"] == {"pending": True}
    finally:
        pipe.sent.put(None)
        engine.join(debugger.EVAL_SECONDS + 10)
        if kind == "process":
            kernel.close()
    assert not engine.is_alive()
