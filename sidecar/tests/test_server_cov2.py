"""The engine's request loop and start (server.py) seen from outside: a reply that can't be sent, a shell
that has gone, the usage count that fails, the redacting stderr, the log file, and main() as the app starts
it, run in this process with the process's own handles faked."""

from __future__ import annotations

import io
import json
import logging
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import data_format, notebook, server


class Engine:
    def app_settings(self):
        return {"ok": True}

    def engine_state(self):
        class Unprintable:
            def __str__(self):
                raise TypeError("no text")

        return {"value": Unprintable()}


def _serve(lines: list[str], engine=None, stdout=None) -> list[dict]:
    stdout = stdout if stdout is not None else io.StringIO()
    server.serve(engine or Engine(), stdin=io.StringIO("".join(lines)), stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_blank_lines_are_skipped_and_every_request_gets_one_reply():
    replies = _serve(["\n", "   \n", json.dumps({"id": 7, "method": "app_settings"}) + "\n", "\n"])
    assert replies == [{"id": 7, "result": {"ok": True}}]


def test_a_result_that_cannot_be_written_is_still_answered_with_why():
    [reply] = _serve([json.dumps({"id": 3, "method": "engine_state"}) + "\n"])
    assert reply == {"id": 3, "error": "Could not send the result: TypeError"}


def test_a_shell_that_has_gone_is_logged_and_the_loop_keeps_draining(capsys):
    class Gone(io.StringIO):
        def write(self, text):
            raise BrokenPipeError("pipe closed")

    server.serve(Engine(), stdin=io.StringIO(json.dumps({"id": 1, "method": "app_settings"}) + "\n"
                                             + json.dumps({"id": 2, "method": "app_settings"}) + "\n"), stdout=Gone())
    err = capsys.readouterr().err
    assert err.count("rpc: could not answer: BrokenPipeError: pipe closed") == 2


def test_a_usage_count_that_fails_is_logged_and_the_call_goes_on(capsys):
    def record(*args):
        raise OSError("disk gone")

    service = SimpleNamespace(telemetry=SimpleNamespace(record=record))
    server._count(service, "meta_run", {"id": 1, "result": {}}, 0.1)  # returns: the call isn't failed
    assert capsys.readouterr().err == "telemetry: could not count meta_run: OSError\n"


def test_a_usage_count_records_the_action_whether_it_failed_and_its_error():
    seen = []
    service = SimpleNamespace(telemetry=SimpleNamespace(record=lambda *a: seen.append(a)))
    server._count(service, "meta_run", {"id": 1, "error": "boom"}, 0.5)
    assert seen == [("meta_run", False, 0.5, "boom")]


def test_the_redacting_stream_looks_like_a_text_file_for_libraries_that_check():
    class Raw(io.StringIO):
        encoding = "cp1252"

        def fileno(self):
            return 9

    stream = server._RedactedStream(Raw())
    assert stream.writable() and not stream.isatty()
    assert stream.encoding == "cp1252" and stream.fileno() == 9
    assert server._RedactedStream(SimpleNamespace(encoding=None, flush=lambda: None)).encoding == "utf-8"


def test_a_line_longer_than_the_hold_goes_out_as_it_is(monkeypatch):
    raw = io.StringIO()
    stream = server._RedactedStream(raw)
    monkeypatch.setattr(server._RedactedStream, "HOLD", 10)
    assert stream.write("x" * 25) == 25
    assert raw.getvalue() == "x" * 25 and stream._part == ""
    stream.write("short")
    assert raw.getvalue() == "x" * 25  # held until a newline or a flush
    stream.flush()
    assert raw.getvalue() == "x" * 25 + "short"


def test_the_log_file_is_the_engines_log_under_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with server._log_file() as log:
        log.write("first\n")
    with server._log_file() as log:
        log.write("second\n")
    assert (tmp_path / "FabricDesktop" / "sidecar.log").read_text(encoding="utf-8").split() == ["first", "second"]


def test_log_lines_go_to_stderr_once_with_secrets_removed(monkeypatch):
    logger = logging.getLogger("fabric_desktop")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logger.level)
    server._log_to_stderr()
    server._log_to_stderr()  # a second call adds no second handler
    [handler] = logger.handlers
    assert logger.level == logging.INFO and isinstance(handler.formatter, server._SafeFormatter)


def test_the_local_api_and_editor_channel_failing_to_start_are_logged(capsys):
    def fails(what):
        def call():
            raise RuntimeError(f"{what} port taken")
        return call

    server._start_integration_http(SimpleNamespace(integrations_sync_http=fails("api"), editor_sync=fails("editor")))
    err = capsys.readouterr().err.splitlines()
    assert err == ["integrations: local API not started: api port taken",
                   "integrations: editor channel not started: editor port taken"]


def test_the_launchers_are_refreshed_and_a_failure_never_stops_the_engine(tmp_path, monkeypatch, capsys):
    from fabric_desktop.integrations import config

    wrote = []
    monkeypatch.setattr(server, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "launcher_path", lambda root: tmp_path / "launcher.cmd")
    monkeypatch.setattr(config, "write_launcher", lambda root: wrote.append(("ai", root)))
    monkeypatch.setattr(config, "write_cli_launcher", lambda root: wrote.append(("cli", root)))
    server._refresh_integration_launcher()
    assert wrote == [("cli", tmp_path)]  # no AI tools' launcher yet: only the CLI's
    (tmp_path / "launcher.cmd").write_text("x")
    server._refresh_integration_launcher()
    assert wrote[1:] == [("ai", tmp_path), ("cli", tmp_path)]

    def broken(root):
        raise PermissionError("read-only")

    monkeypatch.setattr(config, "write_cli_launcher", broken)
    server._refresh_integration_launcher()
    assert capsys.readouterr().err == "integrations: could not update the launcher: read-only\n"


class Handle(io.StringIO):
    def __init__(self, fd: int, text: str = ""):
        super().__init__(text)
        self.fd = fd

    def fileno(self):
        return self.fd

    def close(self):  # read after main returns
        pass


@pytest.fixture
def started(monkeypatch, tmp_path):
    """main()'s process handles, faked: fd 1 for stdout, fd 2 for stderr, the reply pipe it opens, and stdin."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rpc_out, stderr, dups = Handle(11), Handle(2), []
    monkeypatch.setattr(server, "os", SimpleNamespace(
        dup=lambda fd: dups.append(("dup", fd)) or 11, dup2=lambda a, b: dups.append(("dup2", a, b)),
        fdopen=lambda fd, *a, **k: rpc_out if fd == 11 else pytest.fail(f"fdopen {fd}")))
    crashes = []
    monkeypatch.setattr(server, "_report_native_crashes", crashes.append)
    monkeypatch.setattr(server, "_log_to_stderr", lambda: None)
    monkeypatch.setattr(server, "_refresh_integration_launcher", lambda: pytest.fail("no store may open"))

    def start(requests: list[dict], stderr_now=stderr):
        monkeypatch.setattr(sys, "stdout", Handle(1))  # set here: pytest's capture sets them again after setup
        monkeypatch.setattr(sys, "stderr", stderr_now)
        monkeypatch.setattr(sys, "stdin", sys.stdin)
        body = "".join(json.dumps(r) + "\n" for r in requests).encode()
        sys.stdin = io.TextIOWrapper(io.BytesIO(body), encoding="ascii")
        server.main()
        return [json.loads(line) for line in rpc_out.getvalue().splitlines()]

    return SimpleNamespace(start=start, stderr=stderr, dups=dups, crashes=crashes)


def test_main_answers_every_call_with_why_when_the_data_is_a_newer_builds(started, monkeypatch):
    def newer(root):
        raise data_format.NewerData("This data folder was last used by a newer KratosForge.")

    monkeypatch.setattr(data_format, "check", newer)
    replies = started.start([{"id": 1, "method": "app_settings"}])
    assert replies == [{"id": 1, "error": "NewerData: This data folder was last used by a newer KratosForge."}]
    assert "engine: This data folder was last used by a newer KratosForge." in started.stderr.getvalue()
    assert started.dups == [("dup", 1), ("dup2", 2, 1)]  # fd 1 now goes to stderr; replies have their own handle
    assert started.crashes == [started.stderr]
    assert isinstance(sys.stderr, server._RedactedStream) and sys.stdout is sys.stderr
    assert sys.stdin.encoding == "utf-8"  # the requests are read as UTF-8 whatever the code page


def test_main_answers_every_call_with_why_when_the_data_folder_cannot_be_written(started, monkeypatch, tmp_path):
    def read_only(root):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(data_format, "check", read_only)
    [reply] = started.start([{"id": 2, "method": "app_settings"}])
    assert reply["id"] == 2 and "can't write to its data folder" in reply["error"]
    assert reply["error"].startswith("OSError: ") and "Access is denied" in reply["error"]
    assert "engine: KratosForge can't write to its data folder" in started.stderr.getvalue()


def test_main_serves_the_engine_then_ends_its_query_processes_and_sessions(started, monkeypatch):
    ended, sweeps, http = [], [], []

    class Service:
        def __init__(self):
            self._sql_runs = SimpleNamespace(shutdown=lambda: ended.append("sql runs"))
            self._engines = SimpleNamespace(close_all=lambda: ended.append("engine sessions"))

        def policy_record(self):
            ended.append("policy recorded")

        def app_settings(self):
            return {"ok": True}

    monkeypatch.setattr(data_format, "check", lambda root: None)
    monkeypatch.setattr(server, "_refresh_integration_launcher", lambda: sweeps.append("launcher"))
    monkeypatch.setattr(notebook, "sweep_spills", lambda: sweeps.append("spills"))
    monkeypatch.setattr(server, "Service", Service)
    monkeypatch.setattr(server, "_start_integration_http", lambda service: http.append(type(service).__name__))
    assert started.start([{"id": 5, "method": "app_settings"}]) == [{"id": 5, "result": {"ok": True}}]
    assert sweeps == ["launcher", "spills"]
    assert ended == ["policy recorded", "sql runs", "engine sessions"]


def test_main_with_no_stderr_logs_to_the_engines_log_file(started, monkeypatch):
    log = Handle(2)
    monkeypatch.setattr(server, "_log_file", lambda: log)

    def newer(root):
        raise data_format.NewerData("newer data")

    monkeypatch.setattr(data_format, "check", newer)
    started.start([], stderr_now=None)
    assert started.crashes == [log] and "engine: newer data" in log.getvalue()
