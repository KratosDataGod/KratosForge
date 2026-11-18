"""The separate-runtime worker (engines/runtime_worker.py), driven in this process so coverage sees
it: test_runtimes.py starts it as ``python -I runtime_worker.py``, which coverage can't follow. A
fake ``databricks.connect`` stands in for the real client; what is checked is the JSON the worker
writes: started (and its secrets gone), results with their outputs, errors, interrupt and quit."""

from __future__ import annotations

import ctypes
import importlib.metadata
import io
import json
import os
import queue
import sys
import threading
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

from fabric_desktop.engines import runtime_worker


class Wire:
    def __init__(self) -> None:
        self.lines: queue.Queue[dict] = queue.Queue()
        self.partial = ""

    def write(self, text: str) -> int:
        self.partial += text
        *done, self.partial = self.partial.split("\n")
        for line in done:
            self.lines.put(json.loads(line))
        return len(text)

    def flush(self) -> None:
        pass

    def next(self, timeout: float = 5.0) -> dict:
        return self.lines.get(timeout=timeout)

    def empty(self) -> bool:
        return self.lines.empty()


class FakeFrame:
    """Enough of a Spark DataFrame for the preview: limit(n).toPandas() and a schema."""

    schema = "a INT, b STRING"

    def __init__(self, pdf: pd.DataFrame) -> None:
        self.pdf = pdf
        self.limits: list[int] = []

    def limit(self, n: int) -> FakeFrame:
        self.limits.append(n)
        return FakeFrame(self.pdf.head(n))

    def toPandas(self) -> pd.DataFrame:  # noqa: N802 - Spark's name
        return self.pdf


class FakeSpark:
    def __init__(self) -> None:
        self.interrupts = 0
        self.running = threading.Event()
        self.stopped = threading.Event()
        self.fail = False

    def interruptAll(self) -> list:  # noqa: N802 - Spark's name
        self.interrupts += 1
        if self.fail:
            raise ConnectionError("The cluster is gone.")
        self.stopped.set()
        return []

    def long_job(self) -> str:
        self.running.set()
        if not self.stopped.wait(5):
            return "never stopped"
        raise RuntimeError("Job cancelled")


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(runtime_worker, "_OUT", w)
    return w


@pytest.fixture
def spark(monkeypatch):
    """A fake databricks-connect install whose session is this FakeSpark."""
    session = FakeSpark()
    databricks = ModuleType("databricks")
    connect = ModuleType("databricks.connect")
    connect.__version__ = "15.4.2"
    connect.DatabricksSession = SimpleNamespace(
        builder=SimpleNamespace(getOrCreate=lambda: session)
    )
    databricks.connect = connect
    monkeypatch.setitem(sys.modules, "databricks", databricks)
    monkeypatch.setitem(sys.modules, "databricks.connect", connect)
    monkeypatch.setattr(sys, "stdin", sys.stdin)
    return session


@pytest.fixture
def worker(wire, spark):
    w = runtime_worker.Worker()
    w.start()
    assert wire.next()["op"] == "started"
    return w


# ---- small parts


def test_send_writes_one_json_line(wire):
    class Stamp:
        def __str__(self) -> str:
            return "12:00"

    runtime_worker.send(
        {"op": "x", "when": Stamp()}
    )  # not JSON by itself: sent as its text
    assert wire.next() == {"op": "x", "when": "12:00"}


def test_preview_takes_only_the_first_rows_and_nulls_become_none():
    frame = FakeFrame(pd.DataFrame({"a": [1, 2, None], 3: ["x", None, "z"]}))
    assert runtime_worker._is_frame(frame)
    assert runtime_worker._preview(frame, 2) == {
        "type": "frame",
        "columns": ["a", "3"],
        "rows": [[1.0, "x"], [2.0, None]],
    }
    assert frame.limits == [2]


@pytest.mark.parametrize(
    "value", [pd.DataFrame({"a": [1]}), 3, "text", SimpleNamespace(limit=1, toPandas=1)]
)
def test_is_frame_wants_limit_topandas_and_schema(value):
    assert runtime_worker._is_frame(value) is False


def test_version_of_an_installed_distribution_and_of_a_missing_one():
    assert runtime_worker._version("pytest") == importlib.metadata.version("pytest")
    assert runtime_worker._version("no-such-distribution-here") == ""


def test_protocol_lines_off_windows_are_the_streams_lines(monkeypatch):
    monkeypatch.setattr(runtime_worker.sys, "platform", "linux")
    assert list(
        runtime_worker._protocol_lines(io.StringIO('{"op": "start"}\n{"op": "quit"}\n'))
    ) == ['{"op": "start"}\n', '{"op": "quit"}\n']


def test_protocol_lines_on_windows_read_only_what_has_arrived(monkeypatch):
    """PeekNamedPipe says how many bytes wait; the loop reads that many, splits whole lines and keeps the rest."""
    chunks = [
        b"",
        b'{"op": "start"}\n{"op"',
        b': "ex\xc3\xa9c"}\n',
        b"",
        b"tail-without-newline",
    ]
    reads: list[tuple] = []
    sleeps: list[float] = []

    class Peek:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, handle, buf, size, read, avail, left):
            assert (handle, buf, size, read, left) == ("HANDLE-7", None, 0, None, None)
            if self.calls >= len(chunks):
                return 0  # the engine closed its end
            avail._obj.value = len(chunks[self.calls])
            self.calls += 1
            return 1

    peek = Peek()
    monkeypatch.setattr(runtime_worker.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, use_last_error=False: SimpleNamespace(PeekNamedPipe=peek),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: f"HANDLE-{fd}")
    )

    def read(fd, n):
        data = chunks[peek.calls - 1]
        reads.append((fd, n))
        assert len(data) == n
        return data

    monkeypatch.setattr(runtime_worker, "os", SimpleNamespace(read=read))
    monkeypatch.setattr(runtime_worker, "time", SimpleNamespace(sleep=sleeps.append))
    stream = SimpleNamespace(fileno=lambda: 7)
    assert list(runtime_worker._protocol_lines(stream)) == [
        '{"op": "start"}',
        '{"op": "exéc"}',
    ]
    assert reads == [(7, 21), (7, 11), (7, 20)]
    assert sleeps == [0.02, 0.02]
    assert (
        peek.argtypes[0] is ctypes.wintypes.HANDLE
        and peek.restype is ctypes.wintypes.BOOL
    )


# ---- start


def test_start_signs_in_removes_the_secrets_and_says_started(wire, spark, monkeypatch):
    def no_metadata(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", no_metadata)
    for name in ("DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN", "DATABRICKS_PASSWORD"):
        monkeypatch.setenv(
            name, "fake-value"
        )  # pragma: allowlist secret (a test value, not a credential)
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example")
    w = runtime_worker.Worker("databricks-connect")
    w.start()
    assert wire.next() == {
        "op": "started",
        "client": "15.4.2",
        "python": "%d.%d" % sys.version_info[:2],
    }
    for name in ("DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN", "DATABRICKS_PASSWORD"):
        assert name not in os.environ
    assert os.environ["DATABRICKS_HOST"] == "https://adb.example"  # not a secret: kept
    assert w.namespace["spark"] is spark and w.namespace["__name__"] == "__main__"
    assert sys.stdin.read() == ""  # a cell's input() can't eat the engine's messages


def test_start_names_the_installed_client_version(wire, spark, monkeypatch):
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "16.1.0" if name == "databricks-connect" else "",
    )
    runtime_worker.Worker().start()
    assert wire.next()["client"] == "16.1.0"


def test_start_of_an_unknown_kind_refuses(wire):
    with pytest.raises(
        ValueError, match="No runtime of kind 'snowpark' in this worker."
    ):
        runtime_worker.Worker("snowpark").start()
    assert wire.empty()


# ---- run


def test_run_shows_the_last_expression_and_printed_text(worker, wire):
    worker.run(
        "c1",
        "x = 41\nprint('hello')\nimport sys\nprint('warn', file=sys.stderr)\nx + 1",
        100,
    )
    assert wire.next() == {
        "op": "result",
        "id": "c1",
        "status": "ok",
        "outputs": [
            {"type": "stream", "text": "hello\nwarn\n"},
            {"type": "text", "text": "42"},
        ],
    }
    assert worker.running is False and worker.namespace["x"] == 41


def test_run_keeps_names_between_cells_and_a_statement_shows_nothing(worker, wire):
    worker.run(1, "y = [1, 2]", 100)
    assert wire.next() == {"op": "result", "id": 1, "status": "ok", "outputs": []}
    worker.run(2, "y.append(3)", 100)  # an expression whose value is None
    assert wire.next()["outputs"] == []
    worker.run(3, "y", 100)
    assert wire.next()["outputs"] == [{"type": "text", "text": "[1, 2, 3]"}]
    worker.run(4, "", 100)
    assert wire.next()["outputs"] == []


def test_run_previews_displayed_and_last_frames(worker, wire):
    worker.namespace["df"] = FakeFrame(pd.DataFrame({"n": [1, 2, 3, 4]}))
    worker.run("c", "display(df)\ndisplay('note')\ndf", 2)
    frame = {"type": "frame", "columns": ["n"], "rows": [[1], [2]]}
    assert wire.next()["outputs"] == [frame, {"type": "text", "text": "'note'"}, frame]
    worker.run("d", "1", 2)  # what was displayed before is cleared for the next cell
    assert wire.next()["outputs"] == [{"type": "text", "text": "1"}]


def test_run_sends_the_cells_error_after_what_it_printed(worker, wire):
    worker.run("e", "print('before')\nraise KeyError('missing')", 100)
    assert wire.next() == {
        "op": "result",
        "id": "e",
        "status": "error",
        "outputs": [
            {"type": "stream", "text": "before\n"},
            {
                "type": "error",
                "ename": "KeyError",
                "evalue": "'missing'",
                "traceback": ["KeyError: 'missing'\n"],
            },
        ],
    }
    assert worker.running is False


def test_run_sends_a_syntax_error(worker, wire):
    worker.run("s", "def (:", 100)
    msg = wire.next()
    assert msg["status"] == "error" and len(msg["outputs"]) == 1
    assert msg["outputs"][0]["ename"] == "SyntaxError" and msg["outputs"][0][
        "traceback"
    ][-1].startswith("SyntaxError")


def test_run_catches_system_exit(worker, wire):
    worker.run("q", "raise SystemExit(3)", 100)
    assert wire.next()["outputs"] == [
        {
            "type": "error",
            "ename": "SystemExit",
            "evalue": "3",
            "traceback": ["SystemExit: 3\n"],
        }
    ]


# ---- interrupt


def test_interrupt_reaches_the_session_only_while_a_cell_runs(worker, spark):
    worker.interrupt()
    assert spark.interrupts == 0
    worker.running = True
    worker.interrupt()
    assert spark.interrupts == 1


def test_interrupt_that_fails_is_quiet(worker, spark):
    spark.fail = True
    worker.running = True
    worker.interrupt()  # nothing raised
    assert spark.interrupts == 1


def test_interrupt_before_start_does_nothing():
    w = runtime_worker.Worker()
    w.running = True
    w.interrupt()
    assert w._interrupt is None


# ---- main


class Feed:
    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()

    def put(self, message) -> None:
        self.q.put(message if isinstance(message, str) else json.dumps(message) + "\n")

    def __iter__(self):
        while (line := self.q.get(timeout=10)) is not None:
            yield line


@pytest.fixture
def process(monkeypatch, wire, spark, tmp_path):
    """main() in a thread, reading the test's feed, with sys.path and argv put back after."""
    site_dir = tmp_path / "site-packages"
    extra = tmp_path / "from-pth"
    site_dir.mkdir()
    extra.mkdir()
    (site_dir / "runtime.pth").write_text(str(extra) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    feed = Feed()
    monkeypatch.setattr(sys, "stdin", feed)
    monkeypatch.setattr(runtime_worker.sys, "platform", "linux")
    done = threading.Event()
    errors: list = []

    def run():
        try:
            runtime_worker.main()
        except BaseException as exc:  # noqa: BLE001 - the test says what went wrong
            errors.append(exc)
        finally:
            done.set()

    state = SimpleNamespace(
        feed=feed,
        wire=wire,
        site=str(site_dir),
        extra=str(extra),
        done=done,
        errors=errors,
        thread=threading.Thread(target=run, daemon=True),
    )
    yield state
    feed.q.put(None)
    state.thread.join(5)


def test_main_starts_runs_interrupts_and_quits(process, monkeypatch, spark):
    monkeypatch.setattr(
        sys, "argv", ["runtime_worker.py", process.site, "databricks-connect"]
    )
    process.thread.start()
    process.feed.put("not json\n")
    process.feed.put({"op": "start"})
    assert process.wire.next()["op"] == "started"
    assert sys.path[:2] == [
        process.site,
        process.extra,
    ]  # the runtime's folder, and its .pth entries, first
    process.feed.put(
        {"op": "exec", "id": "c1", "code": "spark.long_job()", "preview_rows": 5}
    )
    assert spark.running.wait(5)
    process.feed.put({"op": "interrupt"})
    assert process.wire.next() == {
        "op": "result",
        "id": "c1",
        "status": "error",
        "outputs": [
            {
                "type": "error",
                "ename": "RuntimeError",
                "evalue": "Job cancelled",
                "traceback": ["RuntimeError: Job cancelled\n"],
            }
        ],
    }
    assert spark.interrupts == 1
    process.feed.put(
        {"op": "exec", "id": "c2", "code": None}
    )  # no code, no preview_rows: an empty cell
    assert process.wire.next() == {
        "op": "result",
        "id": "c2",
        "status": "ok",
        "outputs": [],
    }
    process.feed.put({"op": "unknown"})
    process.feed.put({"op": "quit"})
    assert process.done.wait(5) and process.errors == []
    assert process.wire.empty()


def test_main_says_why_the_session_couldnt_start(process, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime_worker.py", process.site, "snowpark"])
    process.thread.start()
    process.feed.put({"op": "start"})
    assert process.wire.next() == {
        "op": "error",
        "ename": "ValueError",
        "evalue": "No runtime of kind 'snowpark' in this worker.",
    }
    process.feed.q.put(None)  # the engine closed stdin
    assert process.done.wait(5) and process.errors == []


def test_main_defaults_to_databricks_connect(process, monkeypatch, spark):
    monkeypatch.setattr(sys, "argv", ["runtime_worker.py", process.site])
    spark_fail = {"n": 0}

    def broken():
        spark_fail["n"] += 1
        raise ConnectionRefusedError("Sign in to Databricks first.")

    monkeypatch.setitem(runtime_worker.STARTS, "databricks-connect", (broken, ()))
    process.thread.start()
    process.feed.put({"op": "start"})
    assert process.wire.next() == {
        "op": "error",
        "ename": "ConnectionRefusedError",
        "evalue": "Sign in to Databricks first.",
    }
    assert spark_fail["n"] == 1
    process.feed.put({"op": "quit"})
    assert process.done.wait(5) and process.errors == []
