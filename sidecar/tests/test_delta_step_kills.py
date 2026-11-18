"""Kill a local Delta write at each exact step of its commit, and check the table after (WR-121).

test_crash_kills.py kills at seeded random moments, so where a kill lands depends on the machine.
This test kills at named steps instead. kill_at.c, loaded with LD_PRELOAD into a child process that
runs one write through the app's LocalStore, sends SIGKILL to that process just before or just
after delta-rs puts a file in place:

- data-staged: a data file written in full under its staging name, not yet in place;
- data-in-place: the data file in place, and no commit names it yet;
- commit-staged: the commit written under its staging name, not yet published;
- commit-published: the commit published, and its staged copy not yet removed;
- checkpoint-staged: the commit published, and its checkpoint not yet in place;
- last-checkpoint-staged: the checkpoint in place, and _last_checkpoint still names the one before.

After each kill:
- the child died at that step, not before it, and not of anything else;
- the table opens and reads exactly as before the write, or exactly as after it once the commit was
  published, with no partial or doubled rows and nothing from a leftover file;
- the next write needs nothing from the user and reads back as the model says;
- the Maintain page's health check reports what the kill left, and Compact on this PC clears it.

Linux only, where the shim can be built (it needs a C compiler and glibc). In CI a missing compiler
fails the test rather than skipping it. Windows has the random kills of test_crash_kills.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from crash_writer import LAKEHOUSE, SCHEMA, TABLE, model, run
from deltalake import DeltaTable

from fabric_desktop import maintenance
from fabric_desktop.meta.store import LocalStore

HERE = Path(__file__).parent

# step: (call, before or after it, text in the target path, landed: whether the write's commit is in)
STEPS = {
    "data-staged": ("rename", "before", ".parquet", False),
    "data-in-place": ("rename", "after", ".parquet", False),
    "commit-staged": ("linkat", "before", ".json", False),
    "commit-published": ("linkat", "after", ".json", True),
    "checkpoint-staged": ("rename", "before", ".checkpoint.parquet", True),
    "last-checkpoint-staged": ("rename", "before", "_last_checkpoint", True),
}
OPS = ("append", "overwrite", "merge")

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="LD_PRELOAD shim: Linux only; Windows runs test_crash_kills.py")


@pytest.fixture(scope="module")
def shim(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        if os.environ.get("CI"):
            pytest.fail("no C compiler to build tests/kill_at.c, so no step was killed")
        pytest.skip("no C compiler to build tests/kill_at.c")
    out = tmp_path_factory.mktemp("shim") / "kill_at.so"
    subprocess.run([cc, "-shared", "-fPIC", "-Wall", "-Werror", "-o", str(out), str(HERE / "kill_at.c"), "-ldl"],
                   check=True, capture_output=True)
    return out


def rows(store: LocalStore) -> dict[int, int]:
    df = store.read(LAKEHOUSE, SCHEMA, TABLE)
    assert df is not None, "the table doesn't open"
    ids = df["id"].to_list()
    assert len(ids) == len(set(ids)), "a row is doubled"
    assert df["pad"].null_count() == 0, "a row is partial"
    return dict(zip(ids, df["step"].to_list(), strict=True))


CHILD = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from crash_writer import run
from fabric_desktop.meta.store import LocalStore
run(LocalStore(sys.argv[3], "dev"), sys.argv[4], int(sys.argv[5]))
print("finished", flush=True)
"""


def kill_write(shim: Path, root: Path, op: str, step: int, at: tuple[str, str, str]) -> subprocess.CompletedProcess:
    call, when, text = at
    env = os.environ | {"LD_PRELOAD": str(shim), "KF_KILL_AT": f"{call} {when} {text} 1"}
    return subprocess.run([sys.executable, "-c", CHILD, str(HERE.parent), str(HERE), str(root), op, str(step)],
                          env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("where", list(STEPS))
def test_a_write_killed_at_each_step_leaves_the_table_whole_and_writable(shim, tmp_path, where, op):
    call, when, text, landed = STEPS[where]
    root = tmp_path / "lh"
    store = LocalStore(root, "dev")
    run(store, "append", 0)
    run(store, "append", 1)  # a merge then updates half of these rows
    uri = store.uri(LAKEHOUSE, SCHEMA, TABLE)
    # A checkpoint at every commit, so the write under test writes one (Delta's default is every 100).
    DeltaTable(uri).alter.set_table_properties({"delta.checkpointInterval": "1"})
    before = DeltaTable(uri).version()
    ops = ["append", "append", op]
    states = model(ops)

    killed = kill_write(shim, root, op, 2, (call, when, text))
    assert killed.returncode == -9 and "finished" not in killed.stdout, (
        f"{where} {op}: the write wasn't killed at that step (exit {killed.returncode})\n{killed.stderr[-3000:]}")

    # The table reads as before the write, or as after it once the commit was published: nothing between.
    assert DeltaTable(uri).version() == before + landed, f"{where} {op}: the table is at the wrong version"
    assert rows(store) == states[2 if landed else 1][0], f"{where} {op}: the table doesn't read as its last commit"

    # The next write needs nothing from the user, and reads back as the model says.
    run(store, "append", 3)
    after = model([*ops[:2], *([op] if landed else ["meta"]), "append"])[-1][0]
    assert rows(store) == after, f"{where} {op}: the write after the kill read back wrong"
    assert DeltaTable(uri).version() == before + landed + 1

    # Compact on this PC clears what the kill left once it is past the age a live write could be at, and
    # the table still reads the same.
    stale = time.time() - (maintenance.STALE_STAGED_HOURS + 1) * 3600
    for staged_file in Path(uri).rglob("*#*"):
        os.utime(staged_file, (stale, stale))
    left = sorted(p.relative_to(uri).as_posix() for p in Path(uri).rglob("*#*"))
    assert set(left) <= set(maintenance.leftovers(uri)), f"{where} {op}: health doesn't report {left}"
    maintenance.compact_local(uri, 0, allow_shorter=True)
    assert rows(store) == after
    staged = [p.name for p in Path(uri).rglob("*#*")]
    assert staged == [], f"{where} {op}: Compact left staged files {staged}"
