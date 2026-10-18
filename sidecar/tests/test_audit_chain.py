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
