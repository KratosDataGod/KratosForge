"""The kernel process's protocol (kernel_host.py), driven in this process so coverage sees it: the
engine's tests start it as ``python -m fabric_desktop.kernel_host``, which coverage can't follow.
A fake Kernel stands in for the notebook one; the JSON lines the host writes are what is checked:
answers, errors with their type and message, calls to the engine and their answers, guests, and
main()'s own stdout and stdin handling (with the process's file descriptors faked, never touched)."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import (
    cpu_cap,
    kernel_host,
    lakehouse,
    net_trust,
    notebook,
    proof_runtime,
)


class Wire:
    """A file the host writes JSON lines to; the test takes them one at a time."""

    def __init__(self) -> None:
        self.lines: queue.Queue[dict] = queue.Queue()
        self.partial = ""
        self.flushes = 0

    def write(self, text: str) -> int:
        self.partial += text
        *done, self.partial = self.partial.split("\n")
        for line in done:
            self.lines.put(json.loads(line))
        return len(text)

    def flush(self) -> None:
        self.flushes += 1

    def next(self, timeout: float = 5.0) -> dict:
        return self.lines.get(timeout=timeout)


class Feed:
    """The engine's side of stdin: lines the test puts, until it closes it."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()

    def put(self, message: dict | str) -> None:
        self.q.put(message if isinstance(message, str) else json.dumps(message) + "\n")

    def close(self) -> None:
        self.q.put(None)

    def __iter__(self):
        while (line := self.q.get(timeout=10)) is not None:
            yield line


class FakeKernel:
    """Records what the host asks of it; a few answers call back to the engine."""

    def __init__(self, token=None, load_notebook=None, variable_libraries=None) -> None:
        self.token, self.load_notebook, self.variable_libraries = (
            token,
            load_notebook,
            variable_libraries,
        )
        self.calls: list[tuple] = []
        self.guests: list = []
        self.free = True

    @contextlib.contextmanager
    def as_guest(self, notebook):
        self.guests.append(notebook)
        yield

    def check_free(self) -> None:
        if not self.free:
            raise RuntimeError("A cell is already running.")

    def start(self, code, watches, breakpoints, step):
        self.calls.append(("start", code, watches, breakpoints, step))
        if code == "ask":
            return {
                "token": self.token("pbi"),
                "nb": self.load_notebook("Other"),
                "lib": self.variable_libraries("Vars"),
                "pip": self.engine_call("pip_install", "pandas", "-q"),
            }
        if code == "interrupt me":
            raise KeyboardInterrupt
        return {
            "started": code,
            "name": self.notebook_name,
            "widgets": self.widget_values,
            "resources": self.resources_dir,
            "onelake": self.onelake_writes,
        }

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"op": name, "args": list(args), "kwargs": kwargs}

        return record


@pytest.fixture
def fake_kernel(monkeypatch):
    monkeypatch.setattr(notebook, "Kernel", FakeKernel)


@pytest.fixture
def host(fake_kernel):
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    h = kernel_host.Host(channel)
    return SimpleNamespace(host=h, wire=wire, channel=channel, kernel=h.kernel)


def answer_calls(
    channel: kernel_host.Channel, wire: Wire, answers: dict
) -> threading.Thread:
    """The engine: answers each call the host makes by name, until it has answered them all."""

    def run():
        for _ in range(len(answers)):
            msg = wire.next()
            reply = answers[msg["call"]]
            channel.answer(
                {"id": msg["id"], **(reply(msg["args"]) if callable(reply) else reply)}
            )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---- Channel


def test_send_writes_one_json_line_and_flushes():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    channel.send(
        {"id": "x", "result": Path("a") / "b"}
    )  # not JSON by itself: written as its text
    assert wire.next() == {"id": "x", "result": str(Path("a") / "b")}
    assert wire.flushes == 1 and wire.partial == ""


def test_call_waits_for_the_engines_answer():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    answer_calls(channel, wire, {"token": lambda args: {"result": f"tok-{args[0]}"}})
    assert channel.call("token", "storage") == "tok-storage"
    assert channel.waiting == {}


def test_call_ids_count_up_and_carry_the_args():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    with pytest.raises(TimeoutError):
        channel.call("one", 1, "two", timeout=0.01)
    with pytest.raises(TimeoutError):
        channel.call("two", timeout=0.01)
    assert wire.next() == {"id": "c1", "call": "one", "args": [1, "two"]}
    assert wire.next() == {"id": "c2", "call": "two", "args": []}


def test_call_that_is_never_answered_times_out_and_forgets_its_slot():
    channel = kernel_host.Channel(None, Wire())
    with pytest.raises(
        TimeoutError, match="The app didn't answer load_notebook in time."
    ):
        channel.call("load_notebook", "Nb", timeout=0.01)
    assert channel.waiting == {}
    channel.answer(
        {"id": "c1", "result": "late"}
    )  # a late answer finds no one waiting, and is dropped
    assert channel.waiting == {}


def test_call_answered_with_an_error_raises_its_message():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    answer_calls(
        channel,
        wire,
        {"token": {"error": {"type": "PermissionError", "message": "Sign in again."}}},
    )
    with pytest.raises(RuntimeError, match="^Sign in again.$"):
        channel.call("token", "pbi")


def test_call_answered_with_an_error_without_a_message():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    answer_calls(channel, wire, {"token": {"error": {}}})
    with pytest.raises(RuntimeError, match="The app couldn't answer."):
        channel.call("token", "pbi")


def test_answer_without_a_result_is_none_and_extra_keys_are_ignored():
    wire = Wire()
    channel = kernel_host.Channel(None, wire)
    seen: dict = {}

    def run():
        msg = wire.next()
        channel.answer({"id": msg["id"], "junk": 1})
        seen.update(channel.waiting)

    threading.Thread(target=run, daemon=True).start()
    assert channel.call("schemas_enabled") is None
    assert seen == {}


# ---- Host: what each request does


def test_host_wires_the_kernel_to_the_engine(host):
    answers = {
        "token": lambda a: {"result": f"T:{a[0]}"},
        "load_notebook": lambda a: {"result": {"cells": [a[0]]}},
        "variable_libraries": lambda a: {"result": {"lib": a[0]}},
        "pip_install": lambda a: {"result": a},
    }
    answer_calls(host.channel, host.wire, answers)
    result = host.host.start("ask")
    assert result == {
        "token": "T:pbi",
        "nb": {"cells": ["Other"]},
        "lib": {"lib": "Vars"},
        "pip": ["pandas", "-q"],
    }


def test_start_sets_the_notebooks_name_widgets_and_folders(host):
    widgets = {"w": "1"}
    result = host.host.start(
        "x = 1",
        notebook_name="Nb",
        watches=["x"],
        widget_values=widgets,
        resources_dir="/r",
        onelake_writes="onelake_first",
        breakpoints=[3],
        step=True,
    )
    assert result == {
        "started": "x = 1",
        "name": "Nb",
        "widgets": {"w": "1"},
        "resources": "/r",
        "onelake": "onelake_first",
    }
    assert (
        host.kernel.widget_values is not widgets
    )  # a copy: the request's dict isn't kept
    assert host.kernel.calls == [("start", "x = 1", ["x"], [3], True)]


def test_start_defaults(host):
    assert host.host.start("y") == {
        "started": "y",
        "name": "",
        "widgets": {},
        "resources": "",
        "onelake": "local_first",
    }
    assert host.kernel.calls == [("start", "y", None, None, False)]


def test_start_on_a_busy_kernel_keeps_the_running_cells_name(host):
    host.host.start("a", notebook_name="First")
    host.kernel.free = False
    with pytest.raises(RuntimeError, match="already running"):
        host.host.start("b", notebook_name="Second")
    assert host.kernel.notebook_name == "First"


@pytest.mark.parametrize(
    ("op", "args", "expected"),
    [
        ("debug", {"action": "eval", "expression": "x"}, ("eval", "x")),
        ("debug", {"action": "continue"}, ("continue", None)),
        ("poll", {}, (-1, 0.0)),
        ("poll", {"version": 4, "wait": 0.5}, (4, 0.5)),
        ("interrupt", {}, ()),
        ("variables", {}, ()),
        ("complete", {"code": "pri"}, ("pri", None)),
        ("complete", {"code": "pri", "cursor": 2}, ("pri", 2)),
        ("inspect", {"code": "len("}, ("len(", None, False)),
        ("inspect", {"code": "len(", "cursor": 3, "call": True}, ("len(", 3, True)),
        ("variable_detail", {"name": "df"}, ("df",)),
        ("reads", {}, ()),
        ("forget_guest", {"notebook": "Guest"}, ("Guest",)),
    ],
)
def test_positional_requests_reach_the_kernel(host, op, args, expected):
    result = getattr(host.host, op)(**args)
    assert result == {"op": op, "args": list(expected), "kwargs": {}}
    assert host.kernel.calls == [(op, expected, {})]


@pytest.mark.parametrize(
    "op",
    [
        "frame_page",
        "frame_chart",
        "frame_profile",
        "frame_wrangle",
        "frame_query_profile",
        "ipywidget_event",
        "frame_download",
    ],
)
def test_keyword_requests_reach_the_kernel(host, op):
    result = getattr(host.host, op)(name="df", offset=10)
    assert result == {"op": op, "args": [], "kwargs": {"name": "df", "offset": 10}}


def test_ping_names_the_process():
    host = kernel_host.Host.__new__(kernel_host.Host)
    assert host.ping() == {"pid": os.getpid(), "python": sys.version.split()[0]}


# ---- Host: the lakehouse hooks


class FakeLakehouse:
    Attached = lakehouse.Attached

    def __init__(self) -> None:
        self.attached: list = []
        self.proof = None

    def attach(self, context) -> None:
        self.attached.append(context)


@pytest.fixture
def lh(host):
    fake = FakeLakehouse()
    host.host.lakehouse = fake
    calls: list = []
    host.channel.call = lambda name, *args: (
        calls.append((name, *args)) or f"{name}:{','.join(map(str, args))}"
    )
    return SimpleNamespace(lh=fake, calls=calls)


def test_attach_none_detaches_and_sets_the_guards(host, lh):
    assert host.host.attach(None) is True
    assert lh.lh.attached == [None]
    assert lh.lh.write_guard is None
    assert lh.lh.in_kernel_process is True
    assert (
        lh.lh.maintenance_guard("OPTIMIZE", "ws", "t")
        == "maintenance_guard:OPTIMIZE,ws,t"
    )
    assert (
        lh.lh.prod_guard("MERGE", "ws", "Prod WS", "t", "Nb")
        == "prod_guard:MERGE,ws,Prod WS,t"
    )  # not the notebook
    assert lh.calls == [
        ("maintenance_guard", "OPTIMIZE", "ws", "t"),
        ("prod_guard", "MERGE", "ws", "Prod WS", "t"),
    ]


def test_attach_asks_the_engine_once_whether_schemas_are_enabled(host, lh, tmp_path):
    context = {
        "workspace_id": "ws",
        "lakehouse_id": "lh",
        "name": "LH",
        "work_dir": str(tmp_path),
        "history_file": str(tmp_path / "h.json"),
    }
    host.channel.call = lambda name, *args: (
        lh.calls.append((name, *args)) or (0 if name == "schemas_enabled" else "tok")
    )
    assert host.host.attach(context, guard=True) is True
    (att,) = lh.lh.attached
    assert (att.workspace_id, att.lakehouse_id, att.name, att.workspace_name) == (
        "ws",
        "lh",
        "LH",
        "",
    )
    assert att.work_dir == tmp_path and att.history_file == tmp_path / "h.json"
    assert att.schemas_enabled() is False and att.schemas_enabled() is False
    assert lh.calls == [("schemas_enabled",)]
    assert att.storage_token() == "tok"
    assert lh.calls[-1] == ("token", "storage")
    assert lh.lh.write_guard("Nb", "dbo", "sales") == "tok"
    assert lh.calls[-1] == ("write_guard", "Nb", "dbo", "sales")


def test_attach_with_known_schemas_never_asks(host, lh, tmp_path):
    context = {
        "workspace_id": "ws",
        "lakehouse_id": "lh",
        "name": "LH",
        "work_dir": str(tmp_path),
        "workspace_name": "Sales",
        "schemas_enabled": 1,
        "history_file": "",
    }
    host.host.attach(context)
    (att,) = lh.lh.attached
    assert att.schemas_enabled() is True
    assert att.history_file is None and att.workspace_name == "Sales"
    assert lh.calls == []


# ---- Host: proof runs


class FakeProof:
    def __init__(self, config, side) -> None:
        self.config, self.side = config, side
        self.log = {"proof": "p1", "side": side, "reads": {"t": 2}}
        self.restored = 0

    def bind(self, root, options):
        self.root, self.options = root, options
        return self

    def restore(self) -> None:
        self.restored += 1

    def finish(self, modules):
        return {**self.log, "modules": modules}


def test_proof_binds_finishes_and_unbinds(host, lh, monkeypatch):
    monkeypatch.setattr(proof_runtime, "ProofRuntime", FakeProof)
    assert host.host.proof_log() == {}
    assert host.host.bind_proof({"id": "p1"}, "candidate", "/root") is True
    proof = host.host.proof
    assert lh.lh.proof is proof and proof.root == "/root" and proof.side == "candidate"
    assert proof.options() == "proof_options:"
    log = host.host.proof_log()
    assert (
        log == {"proof": "p1", "side": "candidate", "reads": {"t": 2}}
        and log is not proof.log
    )
    assert host.host.finish_proof(["m"]) == {
        "proof": "p1",
        "side": "candidate",
        "reads": {"t": 2},
        "modules": ["m"],
    }
    assert host.host.proof is None
    assert host.host.unbind_proof() is True  # nothing bound: nothing to restore
    assert lh.lh.proof is None


def test_unbind_proof_restores_a_bound_one(host, lh, monkeypatch):
    monkeypatch.setattr(proof_runtime, "ProofRuntime", FakeProof)
    host.host.bind_proof({}, "baseline", "/r")
    proof = host.host.proof
    assert host.host.unbind_proof() is True
    assert proof.restored == 1 and lh.lh.proof is None


# ---- Host.handle: every request gets one answer


def test_handle_answers_with_the_result(host):
    host.host.handle({"id": 7, "op": "poll", "args": {"version": 2}})
    assert host.wire.next() == {
        "id": 7,
        "result": {"op": "poll", "args": [2, 0.0], "kwargs": {}},
    }
    assert host.kernel.guests == [None]


def test_handle_without_args_and_with_a_guest(host):
    host.host.handle({"id": "a", "op": "variables"})
    host.host.handle({"id": "b", "op": "variables", "args": {"guest": "Guest nb"}})
    assert host.wire.next()["id"] == "a" and host.wire.next()["id"] == "b"
    assert host.kernel.guests == [None, "Guest nb"]


def test_handle_ping(host):
    host.host.handle({"id": "p", "op": "ping"})
    assert host.wire.next() == {
        "id": "p",
        "result": {"pid": os.getpid(), "python": sys.version.split()[0]},
    }


@pytest.mark.parametrize("op", ["nope", None, "handle", "__init__"])
def test_handle_refuses_an_unknown_request(host, op):
    host.host.handle({"id": 1, "op": op})
    assert host.wire.next() == {
        "id": 1,
        "error": {"type": "ValueError", "message": f"Unknown request {op!r}"},
    }
    assert host.kernel.calls == []


def test_handle_sends_the_kernels_error(host, capsys):
    host.kernel.free = False
    host.host.handle({"id": 2, "op": "start", "args": {"code": "x"}})
    assert host.wire.next() == {
        "id": 2,
        "error": {"type": "RuntimeError", "message": "A cell is already running."},
    }
    assert (
        capsys.readouterr().err == ""
    )  # an ordinary error is the answer, not a log line


def test_handle_sends_a_bad_argument_as_a_type_error(host):
    host.host.handle({"id": 3, "op": "variable_detail", "args": {"nope": 1}})
    msg = host.wire.next()
    assert (
        msg["id"] == 3
        and msg["error"]["type"] == "TypeError"
        and "nope" in msg["error"]["message"]
    )


def test_handle_answers_and_logs_a_base_exception(host, capsys):
    host.host.handle({"id": 4, "op": "start", "args": {"code": "interrupt me"}})
    assert host.wire.next() == {
        "id": 4,
        "error": {"type": "KeyboardInterrupt", "message": ""},
    }
    err = capsys.readouterr().err
    assert "Traceback" in err and "KeyboardInterrupt" in err


# ---- main(): the process's own pipes, faked


class FakeOS:
    """kernel_host's os: the descriptor calls are recorded, and the protocol's pipes are the test's."""

    pathsep = os.pathsep
    devnull = os.devnull
    O_RDONLY = os.O_RDONLY
    environ = os.environ
    getpid = staticmethod(os.getpid)

    def __init__(self, feed: Feed, wire: Wire, stdout: Wire) -> None:
        self.feed, self.wire, self.stdout = feed, wire, stdout
        self.ops: list[tuple] = []

    def dup(self, fd):
        self.ops.append(("dup", fd))
        return 100 + fd

    def dup2(self, a, b):
        self.ops.append(("dup2", a, b))

    def open(self, path, flags):
        self.ops.append(("open", path, flags))
        return 55

    def fdopen(self, fd, mode, **kwargs):
        self.ops.append(("fdopen", fd, mode, kwargs))
        return {100: self.feed, 101: self.wire, 1: self.stdout}[fd]

    def _exit(self, code):
        raise SystemExit(code)


@pytest.fixture
def process(monkeypatch, fake_kernel):
    """main() in a thread of its own, with the process's stdin, stdout and sys.path put back after."""
    feed, wire, stdout = Feed(), Wire(), Wire()
    fake_os = FakeOS(feed, wire, stdout)
    monkeypatch.setattr(kernel_host, "os", fake_os)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stdin", sys.stdin)
    monkeypatch.setattr(sys, "path", list(sys.path))
    trusted: list = []
    monkeypatch.setattr(net_trust, "use_system_store", lambda: trusted.append("trust"))
    monkeypatch.setattr(cpu_cap, "from_env", lambda: trusted.append("cap"))
    exits: list = []
    seen: dict = {}

    def run():
        try:
            kernel_host.main()
        except SystemExit as exc:
            exits.append(exc.code)
        finally:
            seen["stdout"], seen["stdin"] = sys.stdout, sys.stdin

    thread = threading.Thread(target=run, daemon=True)
    state = SimpleNamespace(
        feed=feed,
        wire=wire,
        stdout=stdout,
        os=fake_os,
        trusted=trusted,
        exits=exits,
        seen=seen,
        thread=thread,
    )
    yield state
    feed.close()
    thread.join(5)
    if seen.get("stdin") is not None and seen["stdin"] is not sys.__stdin__:
        seen["stdin"].close()


def test_main_says_ready_answers_requests_and_ends_when_stdin_closes(
    process, monkeypatch, tmp_path
):
    lib = str(tmp_path / "env-lib")
    monkeypatch.setenv(
        "FABRIC_DESKTOP_LIBRARY_PATHS", os.pathsep.join([lib, "", sys.path[0], lib])
    )
    process.thread.start()
    assert process.wire.next() == {
        "id": "ready",
        "result": {"pid": os.getpid(), "python": sys.version.split()[0]},
    }
    assert process.trusted == ["trust", "cap"]
    assert (
        sys.path.count(lib) == 1 and sys.path[-1] == lib
    )  # after the engine's own, once
    # stdout is the stderr descriptor now; stdin reads nothing
    assert process.os.ops[:6] == [
        ("dup", 0),
        ("fdopen", 100, "r", {"encoding": "utf-8"}),
        ("dup", 1),
        ("fdopen", 101, "w", {"encoding": "utf-8"}),
        ("dup2", 2, 1),
        ("open", os.devnull, os.O_RDONLY),
    ]
    assert ("dup2", 55, 0) in process.os.ops
    assert (
        "fdopen",
        1,
        "w",
        {"encoding": "utf-8", "errors": "replace", "buffering": 1},
    ) in process.os.ops

    process.feed.put({"id": 1, "op": "start", "args": {"code": "ask"}})
    call = process.wire.next()
    assert call == {"id": "c1", "call": "token", "args": ["pbi"]}
    process.feed.put({"id": "c1", "result": "TOKEN"})
    for name in ("load_notebook", "variable_libraries", "pip_install"):
        call = process.wire.next()
        assert call["call"] == name
        process.feed.put({"id": call["id"], "result": name})
    assert process.wire.next() == {
        "id": 1,
        "result": {
            "token": "TOKEN",
            "nb": "load_notebook",
            "lib": "variable_libraries",
            "pip": "pip_install",
        },
    }
    process.feed.put({"id": 2, "op": "bogus"})
    assert process.wire.next() == {
        "id": 2,
        "error": {"type": "ValueError", "message": "Unknown request 'bogus'"},
    }
    process.feed.close()
    process.thread.join(5)
    assert process.exits == [0]
    assert (
        process.seen["stdout"] is process.stdout
    )  # print() in a cell goes to stderr's descriptor
    assert process.seen["stdin"].read() == ""


def test_main_keeps_a_library_folder_already_on_the_path(process, monkeypatch):
    first = sys.path[0]
    monkeypatch.setenv("FABRIC_DESKTOP_LIBRARY_PATHS", first)
    before = list(sys.path)
    process.thread.start()
    assert process.wire.next()["id"] == "ready"
    assert sys.path == before
    process.feed.close()
    process.thread.join(5)
    assert process.exits == [0]


def test_main_answers_a_cancel_while_a_request_waits_on_the_engine(
    process, monkeypatch
):
    monkeypatch.delenv("FABRIC_DESKTOP_LIBRARY_PATHS", raising=False)
    process.thread.start()
    assert process.wire.next()["id"] == "ready"
    process.feed.put({"id": "run", "op": "start", "args": {"code": "ask"}})
    assert (
        process.wire.next()["call"] == "token"
    )  # the cell's request is waiting on the engine
    process.feed.put(
        {"id": "stop", "op": "interrupt"}
    )  # its own thread: answered meanwhile
    assert process.wire.next() == {
        "id": "stop",
        "result": {"op": "interrupt", "args": [], "kwargs": {}},
    }
    process.feed.put(
        {"id": "c1", "error": {"type": "Exception", "message": "Signed out."}}
    )
    assert process.wire.next() == {
        "id": "run",
        "error": {"type": "RuntimeError", "message": "Signed out."},
    }
    process.feed.close()
    process.thread.join(5)
    assert process.exits == [0]
