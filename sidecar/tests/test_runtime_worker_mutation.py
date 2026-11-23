"""The separate-runtime worker, run in this process against fakes (mutation batch 133,
docs/quality/mutation-testing.md): the protocol's lines on Windows and elsewhere, the session's
start, a cell's outputs, interrupting, and the main loop's messages."""

from __future__ import annotations

import ctypes
import io
import json
import os
import sys
import types

import pandas as pd
import pytest

from fabric_desktop.engines import runtime_worker as rw


@pytest.fixture
def sent(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(rw, "_OUT", out)
    monkeypatch.setattr(sys, "stdin", sys.stdin)  # Worker.start replaces it; put it back afterwards
    return lambda: [json.loads(line) for line in out.getvalue().splitlines()]


class Frame:
    def __init__(self, pdf):
        self.pdf, self.limited, self.schema = pdf, [], "schema"

    def limit(self, n):
        self.limited.append(n)
        return Frame(self.pdf.head(n))

    def toPandas(self):  # noqa: N802 - PySpark's name
        return self.pdf


def test_send_writes_one_json_line_with_other_values_as_text(sent):
    rw.send({"op": "x", "v": b"z"})
    assert sent() == [{"op": "x", "v": "b'z'"}]


def test_a_preview_is_the_first_rows_with_missing_values_as_none():
    frame = Frame(pd.DataFrame({1: [1.0, float("nan"), 3.0], "b": ["x", None, "z"]}))
    out = rw._preview(frame, 2)
    assert frame.limited == [2]
    assert out == {"type": "frame", "columns": ["1", "b"], "rows": [[1.0, "x"], [None, None]]}


def test_a_frame_has_topandas_limit_and_a_schema():
    assert rw._is_frame(Frame(pd.DataFrame()))
    assert not rw._is_frame(types.SimpleNamespace(toPandas=1, limit=1))
    assert not rw._is_frame(types.SimpleNamespace(limit=1, schema=1))
    assert not rw._is_frame(types.SimpleNamespace(toPandas=1, schema=1))


def test_off_windows_the_lines_are_the_streams_own(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert list(rw._protocol_lines(io.StringIO("a\nb\n"))) == ["a\n", "b\n"]


def test_on_windows_only_what_arrived_is_read_and_lines_are_split(monkeypatch):
    read_fd, write_fd = os.pipe()
    chunks = [b"", b'{"op": "a"}\n{"op"', b': "b"}\n\xff\n', None]  # nothing yet, then two parts, then closed
    sleeps, asked = [], []

    def peek(handle, buf, size, read, ready, left):
        asked.append(handle)
        assert buf is None and size == 0 and read is None and left is None  # a peek reads nothing itself
        chunk = chunks.pop(0)
        if chunk is None:
            return 0
        if chunk:
            os.write(write_fd, chunk)
        ready._obj.value = len(chunk)
        return 1

    kernel32 = types.SimpleNamespace(PeekNamedPipe=peek)
    seen = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: seen.append((name, kw)) or kernel32, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(get_osfhandle=lambda fd: ("h", fd)))
    monkeypatch.setattr(rw.time, "sleep", sleeps.append)
    stream = types.SimpleNamespace(fileno=lambda: read_fd)
    try:
        lines = list(rw._protocol_lines(stream))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert lines == ['{"op": "a"}', '{"op": "b"}', "�"]
    assert sleeps == [0.02] and seen == [("kernel32", {"use_last_error": True})] and asked[0] == ("h", read_fd)


def test_a_version_comes_from_the_installed_distribution_or_is_empty():
    assert rw._version("pytest") == pytest.__version__
    assert rw._version("no-such-distribution-kf") == ""


def _fake_connect(monkeypatch, version="", dist=""):
    spark = types.SimpleNamespace(interruptAll=lambda: None)
    builder = types.SimpleNamespace(getOrCreate=lambda: spark)
    module = types.ModuleType("databricks.connect")
    module.DatabricksSession = types.SimpleNamespace(builder=builder)
    if version:
        module.__version__ = version
    parent = types.ModuleType("databricks")
    parent.connect = module
    monkeypatch.setitem(sys.modules, "databricks", parent)
    monkeypatch.setitem(sys.modules, "databricks.connect", module)
    monkeypatch.setattr(rw, "_version", lambda name: dist if name == "databricks-connect" else "wrong")
    return spark


def test_databricks_connect_starts_a_session_and_names_its_client(monkeypatch):
    spark = _fake_connect(monkeypatch, version="9.9", dist="16.4.2")
    names, interrupt, client = rw._start_databricks_connect()
    assert names == {"spark": spark} and interrupt == spark.interruptAll and client == "16.4.2"
    _fake_connect(monkeypatch, version="9.9", dist="")
    assert rw._start_databricks_connect()[2] == "9.9"


def _worker(monkeypatch, begin=None, secrets=("KF_SECRET",)):
    interrupted = []
    begin = begin or (lambda: ({"spark": "S"}, lambda: interrupted.append(1), "1.0"))
    monkeypatch.setitem(rw.STARTS, "fake", (begin, secrets))
    worker = rw.Worker("fake")
    return worker, interrupted


def test_the_default_kind_is_databricks_connect_and_its_secrets_are_listed():
    assert rw.Worker().kind == "databricks-connect" and rw.Worker().running is False
    assert rw.STARTS["databricks-connect"][1] == ("DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN", "DATABRICKS_PASSWORD")


def test_start_removes_the_secrets_gives_display_and_says_started(monkeypatch, sent):
    monkeypatch.setenv("KF_SECRET", "x")
    worker, _ = _worker(monkeypatch)
    worker.start()
    assert "KF_SECRET" not in os.environ and worker.namespace["spark"] == "S" and "display" in worker.namespace
    assert sys.stdin.read() == ""
    assert sent() == [{"op": "started", "client": "1.0", "python": "%d.%d" % sys.version_info[:2]}]
    with pytest.raises(ValueError, match="No runtime of kind 'nope'"):
        rw.Worker("nope").start()


def test_a_cell_shows_its_print_its_displays_and_its_last_value(monkeypatch, sent):
    worker, _ = _worker(monkeypatch)
    worker.start()
    frame = Frame(pd.DataFrame({"a": [1, 2, 3]}))
    worker.namespace["f"] = frame
    worker.run(7, "print('hi')\ndisplay(f)\n41 + 1", 2)
    result = sent()[-1]
    assert result["id"] == 7 and result["status"] == "ok" and worker.running is False
    assert result["outputs"] == [{"type": "stream", "text": "hi\n"},
                                 {"type": "frame", "columns": ["a"], "rows": [[1], [2]]},
                                 {"type": "text", "text": "42"}]
    worker.run(8, "x = 1", 2)
    assert sent()[-1]["outputs"] == []  # no last expression, nothing printed: nothing shown
    worker.run(9, "None", 2)
    assert sent()[-1]["outputs"] == []
    worker.run(10, "", 2)  # an empty cell runs and shows nothing
    assert sent()[-1]["status"] == "ok" and sent()[-1]["outputs"] == []


def test_a_failing_cell_keeps_what_it_printed_and_says_why(monkeypatch, sent):
    worker, _ = _worker(monkeypatch)
    worker.start()
    worker.run(1, "print('so far')\nraise KeyError('k')", 5)
    result = sent()[-1]
    assert result["status"] == "error" and worker.running is False
    assert result["outputs"][0] == {"type": "stream", "text": "so far\n"}
    assert result["outputs"][1]["ename"] == "KeyError" and result["outputs"][1]["evalue"] == "'k'"
    worker.run(2, "1/0", 5)
    assert [o["type"] for o in sent()[-1]["outputs"]] == ["error"]


def test_interrupt_reaches_the_session_only_while_a_cell_runs(monkeypatch, sent):
    worker, interrupted = _worker(monkeypatch)
    worker.interrupt()  # not started: nothing to stop
    worker.start()
    worker.interrupt()
    assert interrupted == []
    worker.running = True
    worker.interrupt()
    assert interrupted == [1]

    def broken():
        raise RuntimeError("gone")

    worker._interrupt = broken
    worker.interrupt()  # a failure to stop is not the cell's error


def test_windows_trust_failure_keeps_pythons_own_list(monkeypatch):
    def broken():
        raise RuntimeError("no store")

    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=broken))
    monkeypatch.setattr(sys, "platform", "win32")
    rw._trust_windows()


def test_main_puts_the_folder_first_and_answers_each_message(monkeypatch, sent, tmp_path):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "platform", "linux")
    ran = []

    def begin():
        return {}, lambda: ran.append("interrupt"), "1"

    monkeypatch.setitem(rw.STARTS, "fake", (begin, ()))
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            assert daemon is True
            started.append(args)

        def start(self):
            pass

    monkeypatch.setattr(rw.threading, "Thread", Thread)
    monkeypatch.setattr(rw.Worker, "interrupt", lambda self: ran.append("interrupt"))
    monkeypatch.setattr(sys, "argv", ["rw", str(tmp_path), "fake"])
    lines = ['not json', '{"op": "start"}', '{"op": "exec", "id": 3, "code": "1"}',
             '{"op": "exec", "id": 4, "code": null, "preview_rows": 5}', '{"op": "interrupt"}', '{"op": "quit"}',
             '{"op": "start"}']
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    rw.main()
    assert os.path.normcase(sys.path[0]) == os.path.normcase(str(tmp_path))  # site.addsitedir normalizes it
    assert [m["op"] for m in sent()] == ["started"]  # nothing after quit
    assert started == [(3, "1", 100), (4, "", 5)] and ran == ["interrupt"]


def test_main_reports_a_start_that_fails_and_defaults_to_databricks_connect(monkeypatch, sent, tmp_path):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "platform", "linux")
    kinds = []

    def begin():
        raise RuntimeError("cluster terminated")

    monkeypatch.setitem(rw.STARTS, "databricks-connect", (begin, ()))
    original = rw.Worker.__init__
    monkeypatch.setattr(rw.Worker, "__init__", lambda self, kind="x": kinds.append(kind) or original(self, kind))
    monkeypatch.setattr(sys, "argv", ["rw", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"op": "start"}\n{"op": "quit"}\n'))
    rw.main()
    assert kinds == ["databricks-connect"]
    assert sent() == [{"op": "error", "ename": "RuntimeError", "evalue": "cluster terminated"}]


def test_the_worker_imports_no_kratosforge_module_at_run_time():
    with open(rw.__file__, encoding="utf-8") as f:
        assert "fabric_desktop" not in f.read()
