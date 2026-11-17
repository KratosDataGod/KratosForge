"""A SQLite file another process holds for longer than the wait: the UI is told why and what to do.

Real processes: a child takes an exclusive lock on the file and keeps it, as a scheduled run, the
agent, or antivirus or backup software can, while this process makes a change through server.handle.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap

from fabric_desktop import server, sqlitefile

HOLD = """
    import sqlite3, sys, time
    db = sqlite3.connect(sys.argv[1], isolation_level=None)
    db.execute("begin exclusive")
    print("held", flush=True)
    sys.stdin.readline()  # until the test says to let go
    db.execute("commit")
"""


def holder(path):
    p = subprocess.Popen([sys.executable, "-c", textwrap.dedent(HOLD), str(path)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held", p.stderr.read()
    return p


def release(p):
    out, err = p.communicate("go\n", timeout=60)
    assert p.returncode == 0, err


class Service:
    """Calls by the names of real RPCs that change and read one SQLite file, waiting half a second
    rather than the stores' 30 so the test doesn't sit out the real timeout."""

    def __init__(self, path):
        self.path = path

    def integrations_set_enabled(self, enabled):
        with sqlitefile.connect(self.path, timeout=0.5) as db:
            db.execute("insert into t values (?)", (int(enabled),))
        return {"ok": True}

    def integrations_status(self):
        with sqlitefile.connect(self.path, timeout=0.5) as db:
            return {"rows": db.execute("select count(*) from t").fetchone()[0]}


def store(tmp_path):
    path = tmp_path / "store.sqlite"
    with sqlitefile.connect(path) as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table t(x)")
    return path


def call(svc, method, **params):
    return server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))


def test_a_change_to_a_file_another_process_holds_says_why_and_what_to_do_and_works_after(tmp_path):
    path = store(tmp_path)
    svc = Service(path)
    p = holder(path)
    try:
        reply = call(svc, "integrations_set_enabled", enabled=True)
        assert "result" not in reply, reply
        error = reply["error"]
        assert error.startswith("A database file stayed busy with another change for too long, so this didn't finish."), error
        assert "Try again in a moment." in error and error.endswith("(OperationalError: database is locked)"), error
        assert call(svc, "integrations_status") == {"id": 1, "result": {"rows": 0}}  # WAL: a read gets past the lock
    finally:
        release(p)
    assert call(svc, "integrations_set_enabled", enabled=True) == {"id": 1, "result": {"ok": True}}
    assert call(svc, "integrations_status") == {"id": 1, "result": {"rows": 1}}


def test_only_a_busy_file_gets_the_message():
    assert sqlitefile.busy_message(sqlite3.OperationalError("no such table: t")) is None
    assert sqlitefile.busy_message(sqlite3.OperationalError("database table is locked")) is None  # same connection
    assert sqlitefile.busy_message(ValueError("database is locked")) is None
    try:
        try:
            raise sqlite3.OperationalError("database is locked")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("couldn't save the setting") from exc
    except RuntimeError as wrapped:
        assert sqlitefile.busy_message(wrapped)
