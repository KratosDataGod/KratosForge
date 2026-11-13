"""The writer that test_crash_kills.py kills: a seeded sequence of local Delta writes.

Run as a script, it makes the writes of steps ``first``..``last`` of the plan for ``seed`` through
the app's own LocalStore and maintenance code, printing "done <step>" after each, so the parent
knows which steps had finished when it killed the process. Imported, it gives the plan and what
the table and the meta table must hold after each step (the model the parent checks against).

Not a test module: pytest collects test_*.py only.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Any

BLOCK = 100_000  # each step's ids start at step * BLOCK, so steps never collide by accident
ROWS = 40_000  # rows per data write: big enough that a write takes a while to kill in
OPS = ("append", "overwrite", "merge", "optimize", "vacuum", "meta")
LAKEHOUSE, SCHEMA, TABLE, META = "LH_Bronze", "dbo", "crash", "watermarks"


def plan(seed: int, steps: int) -> list[str]:
    """Step 0 makes the table; the rest are drawn from every kind of write."""
    rng = random.Random(seed)
    return ["append"] + [rng.choice(OPS) for _ in range(steps - 1)]


def step_ids(op: str, step: int) -> list[int]:
    if op in ("append", "overwrite"):
        return list(range(step * BLOCK, step * BLOCK + ROWS))
    if op == "merge":  # the second half of the last block's ids, then as many new ones
        start = (step - 1) * BLOCK + ROWS // 2
        return list(range(start, start + ROWS))
    return []


def model(ops: list[str]) -> list[tuple[dict[int, int], list[str]]]:
    """After each step: the table as {id: step that last wrote it}, and the meta table's values."""
    rows: dict[int, int] = {}
    meta: list[str] = []
    out = []
    for step, op in enumerate(ops):
        ids = step_ids(op, step)
        if op == "overwrite":
            rows = dict.fromkeys(ids, step)
        elif op in ("append", "merge"):
            rows = rows | dict.fromkeys(ids, step)
        elif op == "meta":
            meta = [*meta, str(step)]
        out.append((rows, meta))
    return out


def frame(op: str, step: int) -> Any:
    import polars as pl
    ids = step_ids(op, step)
    return pl.DataFrame({"id": ids, "step": [step] * len(ids),
                         "pad": [f"row {i} of step {step}" for i in ids]})  # some bytes per row


def run(store: Any, op: str, step: int) -> None:
    from deltalake import DeltaTable

    from fabric_desktop import maintenance
    uri = store.uri(LAKEHOUSE, SCHEMA, TABLE)
    if op in ("append", "overwrite"):
        store.write(LAKEHOUSE, SCHEMA, TABLE, frame(op, step), mode=op)
    elif op == "merge":
        store.upsert(LAKEHOUSE, SCHEMA, TABLE, frame(op, step), on=["id"])
    elif op == "optimize":  # the Maintain page's Optimize on this PC: compaction, then VACUUM
        maintenance.compact_local(uri, 0, allow_shorter=True)  # deletes all it can: the hardest case
    elif op == "vacuum":  # VACUUM that deletes every file the latest version no longer names
        DeltaTable(uri).vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
    elif op == "meta":
        store.meta_append(META, [{"entity": "crash", "value": str(step), "run_id": f"step-{step}"}])


def main(argv: list[str]) -> None:
    sidecar, root, seed, steps, first, last = argv[1], Path(argv[2]), int(argv[3]), int(argv[4]), int(argv[5]), int(argv[6])
    sys.path.insert(0, sidecar)
    from fabric_desktop.meta.store import LocalStore
    store = LocalStore(root, "dev")
    ops = plan(seed, steps)
    print("ready", flush=True)
    for step in range(first, last + 1):
        run(store, ops[step], step)
        print(f"done {step}", flush=True)


if __name__ == "__main__":
    main(sys.argv)
