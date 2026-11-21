"""A local data project's database (data.duckdb) after the engine writing it is killed part way through.

Real processes: a child opens a persistent project through LocalData and runs a seeded list of
statements (CREATE TABLE AS, INSERT, UPDATE, DELETE, CREATE OR REPLACE), printing each one once
``execute`` has returned. This process kills it (SIGKILL, or TerminateProcess on Windows) just after
a chosen statement finished, so the kill lands during the next one, or, in one round, while closing
the project folds its write-ahead log into the file. Then the project opens again through LocalData,
as the next start does, and its table reads exactly as after the last finished statement or after
the one under way (never half of it), and a next write works.

60 rounds with timed kills (the same statements, kills from 0.2 to 8 s) passed on Linux before this
test was written; it keeps a few in CI on both systems.
"""

from __future__ import annotations

import random
import subprocess
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest

from fabric_desktop.localdata.manager import LocalData

SIDECAR = Path(__file__).resolve().parents[1]
FINGERPRINT = "SELECT count(*), coalesce(sum(hash(id, step, v) % 1000003), 0)::BIGINT FROM dbo.t"


def plan(seed: int, steps: int = 10) -> list[str]:
    r = random.Random(seed)
    out = ["CREATE TABLE dbo.t AS SELECT range AS id, 0 AS step, (range % 97)::INT AS v FROM range(200000)"]
    for k in range(1, steps):
        kind = r.choice(["insert", "insert", "update", "delete", "replace"])
        if kind == "insert":
            out.append(f"INSERT INTO dbo.t SELECT range + {k}*10000000, {k}, (hash(range) % 1000)::INT "
                       f"FROM range({r.randint(300_000, 900_000)})")
        elif kind == "update":
            out.append(f"UPDATE dbo.t SET v = v + {k} WHERE id % {r.randint(2, 5)} = 0")
        elif kind == "delete":
            out.append(f"DELETE FROM dbo.t WHERE id % {r.randint(5, 11)} = {k % 5}")
        else:
            out.append(f"CREATE OR REPLACE TABLE dbo.t AS SELECT id, step, v + 1 AS v FROM dbo.t WHERE id % 13 <> {k % 13}")
    return out


def fingerprints(statements: list[str]) -> list[tuple[int, int]]:
    """What the table reads after each statement, from the same statements run in memory."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dbo")
    found = []
    for sql in statements:
        con.execute(sql)
        row = con.execute(FINGERPRINT).fetchone()
        assert row is not None
        found.append((int(row[0]), int(row[1])))
    return found


CHILD = """
    import sys
    from pathlib import Path
    from fabric_desktop.localdata.manager import LocalData
    sys.path.insert(0, sys.argv[4])
    from test_localdata_kills import plan
    root, pid, seed = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    data = LocalData(root)
    engine = data.engine(pid)
    for k, sql in enumerate(plan(seed)):
        engine.execute(sql)
        print("done", k, flush=True)
    print("closing", flush=True)
    data.close_all()
    print("closed", flush=True)
"""


def kill_after(root: Path, pid: str, seed: int, after: str) -> list[str]:
    """Run the child until it prints ``after``, then kill it; the lines it printed."""
    p = subprocess.Popen([sys.executable, "-c", textwrap.dedent(CHILD), str(root), pid, str(seed),
                          str(Path(__file__).parent)], cwd=SIDECAR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = []
    try:
        assert p.stdout is not None
        for line in p.stdout:
            lines.append(line.strip())
            if line.strip() == after:
                break
    finally:
        p.kill()
        # Statements the child finished before the kill landed are still in the pipe: count them too.
        out, err = p.communicate(timeout=60)
        lines += [line.strip() for line in out.splitlines()]
    assert after in lines, f"the child stopped before {after!r}: {err[-2000:]}"
    return lines


# (seed, kill after): during an INSERT, an UPDATE, a DELETE, a CREATE OR REPLACE, and while closing.
ROUNDS = [(1, "done 2"), (2, "done 3"), (1, "done 3"), (3, "done 4"), (4, "closing")]


@pytest.mark.parametrize("seed,after", ROUNDS)
def test_killed_engine_leaves_last_or_next_statement(tmp_path: Path, seed: int, after: str) -> None:
    statements = plan(seed)
    want = fingerprints(statements)
    data = LocalData(tmp_path)
    pid = data.create(f"kills {seed}")["id"]
    data.close_all()

    lines = kill_after(tmp_path, pid, seed, after)
    done = max(int(line.split()[1]) for line in lines if line.startswith("done "))

    data = LocalData(tmp_path)
    try:
        engine = data.engine(pid)
        row = engine.execute(FINGERPRINT)["rows"][0]
        got = (int(row[0]), int(row[1]))
        allowed = want[done:done + 2]
        assert got in allowed, f"seed {seed}: after statement {done} the table reads {got}, not one of {allowed}"
        engine.execute("INSERT INTO dbo.t VALUES (-1, -1, -1)")
        assert engine.execute("SELECT count(*) FROM dbo.t WHERE id = -1")["rows"][0][0] == 1
    finally:
        data.close_all()

    # And it opens once more after a clean close, with the next write in it.
    data = LocalData(tmp_path)
    try:
        assert data.engine(pid).execute("SELECT count(*) FROM dbo.t")["rows"][0][0] == got[0] + 1
    finally:
        data.close_all()
