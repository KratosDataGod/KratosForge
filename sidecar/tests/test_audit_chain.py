"""One chain writer for both audit trails, safe across processes (HD-017, QA-012)."""

import json
import multiprocessing

from fabric_desktop import audit_chain
from fabric_desktop.audit import AuditLog


def _write(folder, app, count):
    log = AuditLog(folder, app=app)  # each process starts with its own view of the folder
    for i in range(count):
        log.record("meta_run", "ok", 0.1, {"entity": f"{app}-{i}"})


def test_processes_writing_at_once_keep_one_unbroken_chain(tmp_path):
    ctx = multiprocessing.get_context("spawn")  # as Windows starts them
    procs = [ctx.Process(target=_write, args=(tmp_path, name, 40)) for name in ("desktop", "agent", "schedule")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    result = AuditLog(tmp_path).verify()
    assert result == {"lines": 120, "ok": True, "problems": []}


def test_a_long_running_writer_sees_lines_other_processes_added(tmp_path):
    app = AuditLog(tmp_path)
    app.record("meta_run", "ok", 0.1)
    _write(tmp_path, "agent", 2)  # another process's lines, written while the app is open
    app.record("meta_promote", "ok", 0.1)
    assert app.verify()["ok"]  # the app chained onto the agent's last line, not its own


def test_the_last_line_is_found_in_a_large_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_chain, "_TAIL", 64)  # smaller than one line: the reader must widen
    log = AuditLog(tmp_path)
    for i in range(5):
        log.record("meta_run", "ok", 0.1, {"entity": "x" * 150, "n": i})
    last = json.loads(audit_chain.files(tmp_path)[-1].read_text().splitlines()[-1])
    assert audit_chain.last_hash(tmp_path) == last["hash"] and log.verify()["ok"]


def _trail(folder, count):
    log = AuditLog(folder)
    for i in range(count):
        log.record("meta_run", "ok", 0.1, {"n": i})
    return log, audit_chain.files(folder)[-1]


def _cut(path, keep):
    path.write_text("".join(line + "\n" for line in path.read_text().splitlines()[:keep]))


def test_lines_deleted_from_the_end_are_reported(tmp_path):
    log, path = _trail(tmp_path, 5)
    assert log.verify() == {"lines": 5, "ok": True, "problems": []}
    _cut(path, 3)  # the chain that is left still checks out line by line
    result = log.verify()
    assert not result["ok"] and "deleted from the end" in result["problems"][0]


def test_lines_written_after_a_cut_do_not_hide_it(tmp_path):
    log, path = _trail(tmp_path, 5)
    _cut(path, 3)
    log.record("meta_run", "ok", 0.1)
    log.record("meta_run", "ok", 0.1)
    head = json.loads((tmp_path / audit_chain.HEAD).read_text())
    assert head["lines"] == 5 and head["lost"]["lines"] == 5
    result = log.verify()
    assert not result["ok"] and any("before lines went missing" in p for p in result["problems"])


def test_a_trail_from_before_the_head_starts_one_without_a_false_alarm(tmp_path):
    log, _ = _trail(tmp_path, 3)
    (tmp_path / audit_chain.HEAD).unlink()
    assert log.verify()["ok"]
    log.record("meta_run", "ok", 0.1)
    assert json.loads((tmp_path / audit_chain.HEAD).read_text()) == {"hash": audit_chain.last_hash(tmp_path), "lines": 4}
    assert log.verify()["ok"]


def test_a_write_that_stopped_before_its_head_is_not_a_loss(tmp_path, monkeypatch):
    log, _ = _trail(tmp_path, 2)
    real = audit_chain.jsonfile.write_json
    monkeypatch.setattr(audit_chain.jsonfile, "write_json", lambda *a, **k: None)
    log.record("meta_run", "ok", 0.1)  # the line is written, the head isn't
    assert log.verify()["ok"]
    monkeypatch.setattr(audit_chain.jsonfile, "write_json", real)
    log.record("meta_run", "ok", 0.1)
    assert "lost" not in json.loads((tmp_path / audit_chain.HEAD).read_text()) and log.verify()["ok"]


def test_the_ai_tools_trail_gets_a_head_too(tmp_path):
    entry = {"at": "2026-10-24T00:00:00Z", "tool": "x"}
    audit_chain.append(tmp_path, entry)
    assert json.loads((tmp_path / audit_chain.HEAD).read_text()) == {"hash": entry["hash"], "lines": 1}
