"""The app's own SQLite stores after the process writing them is killed part way through.

Real processes: a child writes one committed change after another to a store (the workloads file,
the local platform's keys, integrations, the agent's job queue, the optimize outbox) and prints each
one once its call has returned; this process kills it (SIGKILL, or TerminateProcess on Windows) after
a few, so the kill lands during a later write. Then the store opens through its own class, as the
next start does, passes SQLite's integrity check, and still holds everything the child printed.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parents[1]
ROUNDS = 3

CHILD = """
    import sys
    from pathlib import Path
    kind, path, start = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    if kind == "workloads":
        from fabric_desktop.workloads.store import ManagementStore
        s = ManagementStore(path)
        write = lambda i: s.audit_queue(f"k{i}", "wl", "run", "ok", {"n": i})
    elif kind == "keys":
        from fabric_desktop.central.client import LocalPlatform
        s = LocalPlatform(path, lambda: "me@contoso.com")
        s.call("ensure_key_table", name="customer")
        write = lambda i: print(f"key {i} {s.call('issue_keys', name='customer', business_keys=[f'c{i}'])['keys'][f'c{i}']}")
    elif kind == "integrations":
        from fabric_desktop.integrations.store import IntegrationStore
        s = IntegrationStore(path)
        write = lambda i: s.add_client(f"tool {i}")
    elif kind == "jobqueue":
        from fabric_desktop.jobqueue import JobQueue
        s = JobQueue(path)
        write = lambda i: s.enqueue("run", {"n": i})
    else:
        from fabric_desktop.optimize.outbox import Outbox, ANALYSIS
        s = Outbox(path, max_items=100_000)
        write = lambda i: s.add("lh-1", ANALYSIS, {"n": i, "i": i})
    print("ready", flush=True)
    for i in range(start, start + 100_000):
        write(i)
        print(i, flush=True)
"""


def kill_after(kind: str, path: Path, start: int, writes: int) -> tuple[list[int], dict[int, int]]:
    """Run the child until it has printed ``writes`` committed writes, then kill it. Returns the
    numbers it printed and, for keys, the surrogate key it was given for each."""
    p = subprocess.Popen([sys.executable, "-c", textwrap.dedent(CHILD), kind, str(path), str(start)], cwd=SIDECAR,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    done, keys = [], {}
    try:
        assert p.stdout is not None
        assert p.stdout.readline().strip() == "ready", p.stderr.read() if p.stderr else ""
        while len(done) < writes:
            line = p.stdout.readline()
            assert line, p.stderr.read() if p.stderr else "the child ended"
            if line.startswith("key "):
                _, i, key = line.split()
                keys[int(i)] = int(key)
            else:
                done.append(int(line))
    finally:
        p.kill()  # SIGKILL; TerminateProcess on Windows
        p.wait(timeout=60)
    return done, keys


def committed(kind: str, path: Path) -> tuple[set[int], dict[int, int]]:
    """Open the store through its own class, as the next start does, and read back what it holds."""
    import json
    if kind == "workloads":
        from fabric_desktop.workloads.store import ManagementStore
        ManagementStore(path)
        sql = "select key from audit_out"
        return {int(r[0][1:]) for r in rows(path, sql)}, {}
    if kind == "keys":
        from fabric_desktop.central.client import LocalPlatform, PlatformKeys
        exported = PlatformKeys(LocalPlatform(path, lambda: "me@contoso.com"), "dev").export("customer")
        keys = {int(str(bk)[1:]): int(sk) for sk, bk, *_ in exported if str(bk).startswith("c")}
        return set(keys), keys
    if kind == "integrations":
        from fabric_desktop.integrations.store import IntegrationStore
        return {int(c["name"].split()[1]) for c in IntegrationStore(path).clients()}, {}
    if kind == "jobqueue":
        from fabric_desktop.jobqueue import JobQueue
        JobQueue(path)
        return {json.loads(r[0])["n"] for r in rows(path, "select payload from jobs")}, {}
    from fabric_desktop.optimize.outbox import Outbox
    Outbox(path, max_items=100_000)
    return {json.loads(r[0])["n"] for r in rows(path, "select payload from outbox")}, {}


def rows(path: Path, sql: str) -> list[tuple]:
    db = sqlite3.connect(path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


FILES = {"workloads": "workloads.sqlite", "keys": "platform/keys.sqlite", "integrations": "integrations/integrations.sqlite",
         "jobqueue": "agent/queue.sqlite", "outbox": "optimize/outbox.sqlite"}


@pytest.mark.parametrize("kind", list(FILES))
def test_a_store_killed_mid_write_opens_passes_its_check_and_keeps_every_committed_write(tmp_path, kind):
    path = tmp_path / FILES[kind]
    printed: list[int] = []
    issued: dict[int, int] = {}
    for r in range(ROUNDS):
        done, keys = kill_after(kind, path, r * 1000, 25 + 10 * r)
        printed += done
        issued.update(keys)
        held, held_keys = committed(kind, path)  # opens as the next start does: no error, nothing set aside
        assert not list(path.parent.glob("*.corrupt-*")), "the store was set aside"
        check = rows(path, "pragma integrity_check")
        assert check == [("ok",)], check
        missing = sorted(set(printed) - held)
        assert not missing, f"{len(missing)} committed writes lost after round {r + 1}: {missing[:10]}"
        for i, key in issued.items():  # a surrogate key once given is never different after a crash
            assert held_keys.get(i) == key, (i, key, held_keys.get(i))
