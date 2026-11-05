"""Regenerate central_shapes.json: every distinct SQLite shape the platform's database had on main.

For each commit in this branch's history (main's, plus the branch's own) that changed central/core.py
or central/policy.py, oldest first, this runs that commit's own ensure_schema (core.py with policy.py, both standard-library only) on an empty
SQLite file and records the tables and columns it made. Consecutive identical shapes are kept once.

    python sidecar/tests/fixtures/central_shapes.py      (in a full clone, on the branch, committed)

A PR that changes the platform's tables commits its change, then runs this and commits the fixture, so
the fixture's newest shape is the one the PR makes (test_the_fixture_s_newest_shape_is_today_s). Run on
a branch, the fixture also holds the branch's own earlier commits under their pre-squash ids; run again
on main after the merge, it may differ in those. Either way the newest shape is HEAD's.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
FILES = ("sidecar/fabric_desktop/central/core.py", "sidecar/fabric_desktop/central/policy.py")
BUILD = """
import os, sqlite3, sys
sys.path.insert(0, sys.argv[1])
import old.core as core
if os.path.exists(os.path.join(sys.argv[1], "old", "policy.py")):
    import old.policy
conn = sqlite3.connect(sys.argv[2])
core.ensure_schema(core.Db(conn, "sqlite"))
conn.commit()
"""


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def _shape(path):
    conn = sqlite3.connect(path)
    try:
        names = [n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: [[c[1], c[2]] + ([1] if c[5] else []) for c in conn.execute(f"PRAGMA table_info('{n}')")]
                for n in names}
    finally:
        conn.close()


def _built(commit):
    """The shape that commit's own ensure_schema makes on an empty file."""
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "old"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        for f in FILES:
            if f.split("/")[-1] in _git("ls-tree", "--name-only", commit, "sidecar/fabric_desktop/central/"):
                (pkg / Path(f).name).write_text(_git("show", f"{commit}:{f}"))
        subprocess.run([sys.executable, "-I", "-c", BUILD, tmp, str(Path(tmp) / "db.sqlite")], check=True)
        return _shape(Path(tmp) / "db.sqlite")


def main():
    shapes = []
    log = _git("log", "--topo-order", "--reverse", "--abbrev=8", "--format=%h %ad", "--date=short", "HEAD", "--", *FILES)
    for line in log.splitlines():
        commit, date = line.split()
        shape = _built(commit)
        if not shapes or shapes[-1][2] != shape:
            shapes.append((commit, date, shape))
    # HEAD's own shape is always the newest, whatever order the history above came in.
    head, date = _git("log", "-1", "--abbrev=8", "--format=%h %ad", "--date=short", "HEAD").split()
    shape = _built(head)
    if shapes[-1][2] != shape:
        shapes.append((head, date, shape))
    tables, out = [], []
    for commit, date, shape in shapes:
        ids = []
        for name, cols in sorted(shape.items()):
            if [name, cols] not in tables:
                tables.append([name, cols])
            ids.append(tables.index([name, cols]))
        out.append({"commit": commit, "date": date, "tables": ids})
    doc = {"about": "Every distinct SQLite shape central ensure_schema (core.py with policy.py) made on main, oldest "
                    "first, for test_central_upgrades.py. Each table is [name, [[column, type, 1 if primary key], "
                    "...]]; each shape lists its tables by index. Regenerate with tests/fixtures/central_shapes.py.",
           "tables": tables, "shapes": out}
    (HERE / "central_shapes.json").write_text(json.dumps(doc, indent=0, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
