"""One chain writer for both audit trails, safe across processes (HD-017, QA-012)."""

import json
import multiprocessing
import os
import sys
import types

import pytest

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


def _write_mixed(folder, app, count, integrations, ready, go):
    """One writer as the app, the agent or a scheduled run does it; the first app window also writes the
    AI tools' trail beside it, through the same chain writer, as its integrations do."""
    log = AuditLog(folder, app=app)
    ready.release()
    go.wait(60)  # every writer starts at the same moment, so their writes really interleave
    for i in range(count):
        log.record("meta_run", "ok", 0.0, {"entity": f"{app}-{i}"})
        if app == "desktop-1":
            audit_chain.append(integrations, {"at": "2026-11-11T00:00:00+00:00", "capability": "list_tables",
                                              "outcome": "ok", "n": i})


def test_two_app_windows_the_agent_and_a_schedule_at_once_lose_no_line(tmp_path):
    """The real case: two app processes, the agent and a scheduled run append to one trail at the same
    time, while the AI tools' trail is written next to it. Every line lands once, in one chain."""
    trail, tools = tmp_path / "audit", tmp_path / "integrations"
    ctx = multiprocessing.get_context("spawn")
    names, count = ("desktop-1", "desktop-2", "agent", "schedule"), 150
    ready, go = ctx.Semaphore(0), ctx.Event()
    procs = [ctx.Process(target=_write_mixed, args=(trail, name, count, tools, ready, go)) for name in names]
    for p in procs:
        p.start()
    for _ in procs:
        assert ready.acquire(timeout=120)
    go.set()
    for p in procs:
        p.join(180)
        assert p.exitcode == 0
    assert AuditLog(trail).verify() == {"lines": len(names) * count, "ok": True, "problems": []}
    lines = [json.loads(line) for path in audit_chain.files(trail) for line in path.read_text().splitlines()]
    entities = sorted(line["target"]["entity"] for line in lines)
    assert entities == sorted(f"{n}-{i}" for n in names for i in range(count))  # each once: none lost, none twice
    assert json.loads((trail / audit_chain.HEAD).read_text()) == {"hash": lines[-1]["hash"], "lines": len(lines)}
    assert audit_chain.verify(tools) == {"lines": count, "ok": True, "problems": []}
    order = [line["target"]["entity"].rsplit("-", 1)[0] for line in lines]
    assert sum(a != b for a, b in zip(order, order[1:], strict=False)) > len(names)  # the writers did take turns


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


# --------------------------------------------------------------------------- damaged lines and the lock

def _line(folder, at="2026-11-06T10:00:00Z", **extra):
    return audit_chain.append(folder, {"at": at, "action": "meta_run", **extra})


@pytest.mark.parametrize("damage", ["[1, 2]", "\"text\"", "7", "{\"no\": \"hash\"}", "{not json"])
def test_a_line_that_isnt_an_audit_line_is_reported_not_raised(tmp_path, damage):
    _line(tmp_path)
    with (tmp_path / "audit-2026-11.jsonl").open("a", encoding="utf-8") as f:
        f.write(damage + "\n\n")
    out = audit_chain.verify(tmp_path)
    assert out["lines"] == 2 and not out["ok"]
    assert "audit-2026-11.jsonl line 2: not an audit line" in out["problems"]


def test_a_damaged_last_line_starts_the_chain_again_and_is_reported(tmp_path):
    _line(tmp_path)
    with (tmp_path / "audit-2026-11.jsonl").open("a", encoding="utf-8") as f:
        f.write("[\"cut off\n")
    assert audit_chain.last_hash(tmp_path) == audit_chain.GENESIS
    entry = _line(tmp_path)
    assert entry["prev"] == audit_chain.GENESIS
    problems = audit_chain.verify(tmp_path)["problems"]
    assert any("line 2: not an audit line" in p for p in problems)
    assert any("line 3: a line before it is missing or changed" in p for p in problems)


def test_a_changed_line_is_reported(tmp_path):
    _line(tmp_path, n=1)
    _line(tmp_path, n=2)
    path = tmp_path / "audit-2026-11.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace('"n": 1', '"n": 9'), encoding="utf-8")
    assert audit_chain.verify(tmp_path)["problems"] == ["audit-2026-11.jsonl line 1: this line was changed"]


def test_the_newest_month_with_a_line_gives_the_last_hash(tmp_path):
    first = _line(tmp_path, at="2026-10-31T23:59:00Z")
    (tmp_path / "audit-2026-11.jsonl").write_text("\n", encoding="utf-8")  # a month started, nothing in it
    assert audit_chain.last_hash(tmp_path) == first["hash"]
    second = _line(tmp_path, at="2026-11-01T00:01:00Z")
    assert second["prev"] == first["hash"] and audit_chain.verify(tmp_path)["ok"]


def test_a_head_that_isnt_an_object_counts_as_none(tmp_path):
    (tmp_path / audit_chain.HEAD).write_text("[1]", encoding="utf-8")
    _line(tmp_path)
    assert json.loads((tmp_path / audit_chain.HEAD).read_text())["lines"] == 1
    assert audit_chain.verify(tmp_path)["ok"]


class FakeMsvcrt:
    LK_LOCK, LK_UNLCK = 1, 0

    def __init__(self, busy):
        self.busy, self.calls = busy, []

    def locking(self, fd, mode, nbytes):
        self.calls.append(mode)
        if mode == self.LK_LOCK and self.busy:
            self.busy -= 1
            raise OSError(36, "Resource deadlock avoided")


def test_on_windows_the_lock_waits_for_another_writer_then_lets_go(tmp_path, monkeypatch):
    fake = FakeMsvcrt(busy=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    # only audit_chain sees Windows: os.name itself would turn every Path into a WindowsPath
    monkeypatch.setattr(audit_chain, "os", types.SimpleNamespace(name="nt", SEEK_END=os.SEEK_END))
    monkeypatch.setattr(audit_chain.time, "sleep", lambda s: None)
    with audit_chain.locked(tmp_path / "trail"):
        assert fake.calls == [1, 1, 1]
    assert fake.calls == [1, 1, 1, 0] and (tmp_path / "trail" / ".lock").exists()
