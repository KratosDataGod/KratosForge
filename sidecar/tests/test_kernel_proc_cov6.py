"""The notebook's own Python process (kernel_proc.py), the paths the other tests leave: proxy addresses with a
user name and password, an external tool's environment, the engine's answers to the kernel's calls, a reader
that sees lines for nobody, requests a guest makes and the errors they get back, a start the process ended,
a poll with nothing kept, the result viewer's requests, a tree that keeps growing, and memory read from /proc.
No process is started: each one here is a stand-in."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from fabric_desktop import kernel_proc, lakehouse
from fabric_desktop.kernel_proc import KernelGone, ProcessKernel


@pytest.mark.parametrize(("proxy", "shown"), [
    ("http://proxy:8080", "http://proxy:8080"),
    ("  http://user:secret@proxy:8080  ", "http://proxy:8080"),  # pragma: allowlist secret (test value)
    ("http://user:secret@proxy:8080/path", "http://proxy:8080/path"),  # pragma: allowlist secret (test value)
    ("http://u:pa/ss@proxy:8080", "http://proxy:8080"),  # a "/" in the password
    ("u:pa/ss@proxy", "proxy"),  # no scheme
    ("http://proxy:8080/path@x", "http://proxy:8080/path@x"),  # the "@" is in the path
    ("http://[::1]:3128/a@b", "http://[::1]:3128/a@b"),
])
def test_a_proxy_address_loses_its_user_name_and_password(proxy, shown):
    assert kernel_proc._without_credentials(proxy) == shown


def test_an_external_tools_kernel_gets_only_the_allowed_variables():
    env = kernel_proc.external_environment({
        "HOME": "/home/me", "lc_time": "en_GB.UTF-8", "SECRET_TOKEN": "x",  # pragma: allowlist secret (test value)
        "https_proxy": "http://me:pw@proxy:3128", "NO_PROXY": "a@b",  # pragma: allowlist secret (test value)
        "PYTHONPATH": "/elsewhere", "PYTHONUNBUFFERED": "0"})
    assert env["HOME"] == "/home/me" and env["lc_time"] == "en_GB.UTF-8"
    assert "SECRET_TOKEN" not in env
    assert env["https_proxy"] == "http://proxy:3128"
    assert env["NO_PROXY"] == "a@b", "only a proxy address loses credentials"
    assert env["PYTHONPATH"] != "/elsewhere" and env["PYTHONUNBUFFERED"] == "1"


def test_schemas_are_asked_of_the_attached_lakehouse_only(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", None)
    with pytest.raises(RuntimeError, match="^No lakehouse is attached. Use Add data items to attach one.$"):
        kernel_proc._attached_schemas()
    monkeypatch.setattr(lakehouse, "_attached", SimpleNamespace(schemas_enabled=lambda: 1))
    assert kernel_proc._attached_schemas() is True


def test_maintenance_is_refused_without_the_engines_guard(monkeypatch):
    monkeypatch.setattr(lakehouse, "maintenance_guard", None)
    assert kernel_proc._maintenance_refusal("VACUUM", "ws", "t") == lakehouse.MAINTENANCE_NEEDS_APP
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: f"{op} {ws} {t} refused")
    assert kernel_proc._maintenance_refusal("VACUUM", "ws", "t") == "VACUUM ws t refused"


def test_a_prod_change_the_person_allowed_for_this_run_goes_ahead(monkeypatch):
    k = ProcessKernel()
    k.notebook_name = "Night"
    seen = []
    monkeypatch.setattr(lakehouse, "prod_guard", lambda *a: seen.append(a) or "Type the workspace's name")
    k.prod_allowed = frozenset({"ws-prod"})
    assert k._prod_refusal("DELETE", "WS-PROD", "Prod", "orders") is None
    assert seen == []
    assert k._prod_refusal("DELETE", "ws-other", "Other", "orders") == "Type the workspace's name"
    assert seen == [("DELETE", "ws-other", "Other", "orders", "Night")]
    monkeypatch.setattr(lakehouse, "prod_guard", None)
    assert k._prod_refusal("DELETE", "ws-other", "Other", "orders") is None


class _Proc:
    """A stand-in for the kernel's Popen: alive until ``code`` is set."""

    def __init__(self, lines=(), code=None, stdin=True):
        self.stdout = io.StringIO("".join(lines))
        self.stdin = io.StringIO() if stdin else None
        self.code, self.pid = code, 4242

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code


def test_the_reader_skips_lines_for_nobody_and_a_replaced_process_doesnt_end_the_cell():
    k = ProcessKernel()
    old = _Proc([json.dumps({"id": "nobody", "result": 1}) + "\n", "not json\n"], code=0)
    current = _Proc()
    k._proc, k._running = current, True
    k._read(old)
    assert k._proc is current and k._running, "the old process ending leaves the new one's cell alone"
    assert old.stdout.closed and old.stdin.closed


def test_closing_the_pipes_of_a_process_without_them_does_nothing():
    k = ProcessKernel()
    proc = _Proc(code=0, stdin=False)
    proc.stdout = None
    k._close_pipes(proc)
    assert proc.stdin is None and not k._send_lock(proc).locked()


def test_memory_counts_only_the_process_when_the_tree_is_anothers(monkeypatch):
    k = ProcessKernel()
    proc = _Proc()
    k._tree = SimpleNamespace(proc=_Proc(), pids=lambda: pytest.fail("not this tree"))
    monkeypatch.setattr(kernel_proc, "memory_used", lambda pid: {7: 100}.get(pid))
    assert k._memory(proc, 7) == 100
    assert k._memory(proc, 8) == 0, "an unknown size counts as nothing"


def test_the_time_limit_note_is_added_once():
    k = ProcessKernel()
    k.limit_hit, k.time_limit = "time", 120
    once = k._limit_note({"status": "ok", "outputs": []})
    assert once["status"] == "error" and [o["ename"] for o in once["outputs"]] == ["TimeLimit"]
    assert "the 2 minute limit" in once["outputs"][0]["evalue"]
    assert k._limit_note(once)["outputs"] == once["outputs"]


def _answering(k, answers):
    """Answer each request from ``answers`` (op -> {"result": ...} or {"error": ...}) as the reader would."""
    sent = []

    def send(proc, message):
        sent.append(message)
        slot = k._waiting[message["id"]]
        slot.update(answers.get(message["op"], {"result": {"op": message["op"]}}))
        slot["done"].set()
    k._send = send
    return sent


def test_a_guests_request_carries_its_notebook_and_errors_come_back_by_type():
    k = ProcessKernel()
    k._proc = _Proc()
    sent = _answering(k, {"gone": {"error": {"type": "KernelGone", "message": "It stopped."}},
                          "bad": {"error": {"type": "ValueError", "message": "No such frame"}},
                          "bare": {"error": {"type": "MemoryError"}}})
    with k.as_guest("Guest book"):
        assert k._request("variables", spawn=False) == {"op": "variables"}
    assert sent[0]["args"] == {"guest": "Guest book"}
    with pytest.raises(KernelGone, match="^It stopped.$"):
        k._request("gone", spawn=False)
    with pytest.raises(RuntimeError, match="^No such frame$"):
        k._request("bad", spawn=False)
    with pytest.raises(RuntimeError, match="^MemoryError$"):
        k._request("bare", spawn=False)
    assert k._waiting == {}


def test_a_live_processes_reads_frames_and_proof_log_are_asked_of_it():
    k = ProcessKernel()
    k._proc = _Proc()
    sent = _answering(k, {})
    assert k.reads() == {"op": "reads"}
    assert k.frame_chart("df", kind="bar") == {"op": "frame_chart"}
    assert k.frame_profile("df") == {"op": "frame_profile"}
    assert k.frame_query_profile("df") == {"op": "frame_query_profile"}
    assert k.proof_log() == {"op": "proof_log"}
    assert [(m["op"], m["args"].get("frame")) for m in sent] == [
        ("reads", None), ("frame_chart", "df"), ("frame_profile", "df"), ("frame_query_profile", "df"),
        ("proof_log", None)]
    assert sent[1]["args"]["kind"] == "bar"


def test_a_stepped_cell_has_no_time_limit_and_a_start_the_process_ended_keeps_its_cell(monkeypatch):
    k = ProcessKernel()
    asked = []

    def request(op, **kw):
        asked.append((op, kw))
        if op == "start":
            k._running = False  # the reader marked the process gone while the start was answered
            return {"status": "ok", "execution_count": 4, "outputs": []}
        return None
    monkeypatch.setattr(k, "_request", request)
    monkeypatch.setattr(lakehouse, "_attached", None)
    snap = k.start("x = 1", step=True)
    assert snap["execution_count"] == 4 and k.count == 4
    assert k._started_at is None, "a stepped cell runs without the time limit"
    assert k._last["status"] == "running", "the cell the reader ended isn't replaced by the start's answer"
    assert [op for op, _ in asked] == ["attach", "start"] and asked[1][1]["step"] is True


def test_a_guest_sees_a_session_without_a_process_idle():
    k = ProcessKernel()
    k._owner, k.count = "Owner", 3
    with k.as_guest("Guest"):
        assert k.poll() == {"status": "idle", "execution_count": 3, "version": 0, "seconds": 0, "outputs": []}


def test_a_poll_without_outputs_and_nothing_kept_keeps_nothing(monkeypatch):
    k = ProcessKernel()
    k._proc, k._running = _Proc(), True
    monkeypatch.setattr(k, "_request", lambda op, **kw: {"status": "running", "version": 2, "outputs": None})
    assert k.poll(2) == {"status": "running", "version": 2, "outputs": None}
    assert k._last is None and k._running


def test_a_tree_that_keeps_growing_is_looked_at_ten_times(monkeypatch):
    signalled, found = [], iter(range(100, 200))
    monkeypatch.setattr(kernel_proc, "_signal", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(kernel_proc, "_descendants", lambda root: [next(found)])
    sent = kernel_proc._signal_tree(1, 19)
    assert sent == signalled == [1, *range(100, 109)]


@pytest.mark.parametrize(("status", "used"), [
    ("Name:\tpython\nVmRSS:\t  2048 kB\n", 2048 * 1024),
    ("Name:\tpython\nState:\tZ (zombie)\n", None),  # an ended process has no resident memory line
    ("VmRSS:\n", None),
    ("VmRSS:\tmany kB\n", None),
])
def test_linux_memory_is_the_resident_size_from_proc(tmp_path, monkeypatch, status, used):
    path = tmp_path / "status"
    path.write_text(status)
    monkeypatch.setattr(kernel_proc, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(kernel_proc, "Path", lambda p: path if p == "/proc/77/status" else pytest.fail(p))
    assert kernel_proc.memory_used(77) == used
