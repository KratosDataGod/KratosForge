"""The headless agent's JSON log lines (logs.py): where they go, what each carries, and that nothing
credential-shaped reaches a log file."""

from __future__ import annotations

import json
import logging

import pytest

from fabric_desktop import logs


@pytest.fixture
def agent_log():
    logger = logging.getLogger("fabric_desktop.agent")
    before = (list(logger.handlers), logger.level, logger.propagate)
    yield
    for h in logger.handlers:
        h.close()
    logger.handlers[:], logger.level, logger.propagate = before


def test_lines_go_to_stdout_and_a_rotating_file_as_one_json_object_each(tmp_path, capsys, agent_log):
    folder = tmp_path / "logs" / "agent"
    log = logs.setup("worker-1", folder, level="DEBUG")
    assert log.level == logging.DEBUG and not log.propagate
    log.info("job %s done", "j1", extra=logs.fields(job_id="j1", attempt=2))
    for h in log.handlers:
        h.flush()
    out = json.loads(capsys.readouterr().out.strip())
    assert out["message"] == "job j1 done" and out["level"] == "INFO" and out["logger"] == "fabric_desktop.agent"
    assert out["worker"] == "worker-1" and out["job_id"] == "j1" and out["attempt"] == 2 and out["host"]
    assert out["at"].endswith("+00:00")
    rotating = next(h for h in log.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
    assert rotating.maxBytes == 20 << 20 and rotating.backupCount == 10
    assert json.loads((folder / "agent.log").read_text(encoding="utf-8").strip()) == out


def test_without_a_folder_lines_only_go_to_stdout_and_setup_again_doesnt_double_them(capsys, agent_log):
    logs.setup("w", None)
    log = logs.setup("w", None)
    assert len(log.handlers) == 1
    log.info("once")
    assert capsys.readouterr().out.count("once") == 1


def test_an_exception_is_written_with_its_traceback(capsys, agent_log):
    log = logs.setup("w", None)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("run failed")
    line = json.loads(capsys.readouterr().out)
    assert line["level"] == "ERROR" and "RuntimeError: boom" in line["exception"]


def test_a_secret_in_a_message_or_a_field_doesnt_reach_the_log(tmp_path, capsys, agent_log):
    log = logs.setup("w", tmp_path)
    secret = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlLXZhbHVl"
    log.info("token %s", secret, extra=logs.fields(header=f"Authorization: Bearer {secret}", obj=object()))
    for h in log.handlers:
        h.flush()
    written = capsys.readouterr().out + (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert secret not in written and "object object at" in written
