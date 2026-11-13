"""Kill a process part way through local Delta writes, at seeded random moments, and check the table.

Each round makes a fresh table, then starts crash_writer.py on a seeded plan of appends,
overwrites, merges, Optimize (compaction and VACUUM, as the Maintain page runs it), VACUUM that
deletes every unreferenced file, and metadata appends, and kills it (SIGKILL; TerminateProcess
on Windows) after a seeded delay. After every kill:

- the table and the meta table open;
- each reads exactly as the model says after the last step that finished, or the one under way
  when it was killed (a commit is all or nothing), so no row is lost or doubled;
- a next write succeeds and reads back as the model says.

The same seed gives the same plan and the same kill delay. Where the kill lands inside a write still
depends on the machine's speed, so a failure names its seed and round to rerun.

Short in CI: KF_CRASH_ROUNDS rounds (default 6) from KF_CRASH_SEED (default 1). Long, by hand:
    KF_CRASH_ROUNDS=300 KF_CRASH_SEED=7 pytest -q tests/test_crash_kills.py
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from crash_writer import LAKEHOUSE, META, SCHEMA, TABLE, model, plan, run
from deltalake import DeltaTable

from fabric_desktop.meta.store import LocalStore

HERE = Path(__file__).parent
ROUNDS = int(os.environ.get("KF_CRASH_ROUNDS", "6"))
SEED = int(os.environ.get("KF_CRASH_SEED", "1"))
STEPS = 13  # step 0 makes the table here; the child runs 1..12


def table_rows(store: LocalStore) -> dict[int, int]:
    df = pl.from_arrow(DeltaTable(store.uri(LAKEHOUSE, SCHEMA, TABLE)).to_pyarrow_table())
    assert isinstance(df, pl.DataFrame)
    ids = df["id"].to_list()
    assert len(ids) == len(set(ids)), "a row is doubled"
    return dict(zip(ids, df["step"].to_list(), strict=True))


def meta_values(store: LocalStore) -> list[str]:
    return store.meta(META)["value"].to_list()


def matching(rows: dict[int, int], meta: list[str], states: list, candidates: list[int]) -> int | None:
    """The step among ``candidates`` whose model the table and meta table match, if any."""
    return next((j for j in candidates if states[j] == (rows, meta)), None)


def kill_round(tmp_path: Path, seed: int) -> str:
    rng = random.Random(seed)
    ops = plan(seed, STEPS)
    states = model(ops)
    root = tmp_path / f"r{seed}"
    store = LocalStore(root, "dev")
    run(store, ops[0], 0)
    child = subprocess.Popen(
        [sys.executable, str(HERE / "crash_writer.py"), str(HERE.parent), str(root), str(seed), str(STEPS), "1",
         str(STEPS - 1)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready", child.stderr.read() if child.stderr else ""
    time.sleep(rng.uniform(0.0, 0.6))  # seeded: from before the first write to about the last
    child.kill()
    out, err = child.communicate(timeout=60)
    done = max([0] + [int(line.split()[1]) for line in out.splitlines() if line.startswith("done ")])
    where = f"seed {seed}: plan {ops}, killed after step {done} finished"
    assert child.returncode != 0 or done == STEPS - 1, f"{where}: the writer failed by itself\n{err[-3000:]}"

    # The table and the meta table open, and read as the last step that finished or the one under way.
    rows, meta = table_rows(store), meta_values(store)
    landed = matching(rows, meta, states, [done, min(done + 1, STEPS - 1)])
    assert landed is not None, (f"{where}: the tables match no committed state "
                                f"({len(rows)} rows, steps {sorted(set(rows.values()))}, meta {meta})")

    # The next write succeeds and reads back as the model says.
    after = model([*ops[:landed + 1], "append"])
    run(store, "append", landed + 1)
    store.meta_append(META, [{"entity": "crash", "value": "next", "run_id": "next"}])
    assert table_rows(store) == after[-1][0], f"{where}: the write after the kill read back wrong"
    assert meta_values(store) == [*after[-1][1], "next"], f"{where}: the meta append after the kill read back wrong"
    return f"{ops[done + 1] if done + 1 < STEPS else 'end'}:{'landed' if landed > done else 'not landed'}"


@pytest.mark.parametrize("seed", range(SEED, SEED + ROUNDS))
def test_a_kill_at_any_point_leaves_every_table_whole_and_writable(tmp_path, seed):
    kill_round(tmp_path, seed)


if __name__ == "__main__":  # the long run by hand, with a tally of where the kills landed
    import collections
    import tempfile
    tally: collections.Counter[str] = collections.Counter()
    with tempfile.TemporaryDirectory() as tmp:
        for s in range(SEED, SEED + ROUNDS):
            tally[kill_round(Path(tmp), s)] += 1
    print(dict(sorted(tally.items())))
