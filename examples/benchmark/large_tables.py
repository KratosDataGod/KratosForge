"""Measure an incremental metadata run on a large table: Delta MERGE writes versus rewriting the table.

    cd sidecar
    python ../examples/benchmark/large_tables.py --rows 5000000 --changed 1000 --new 1000 --spread recent

It builds a throwaway metadata project in a temporary folder with one entity, bronze.events, loaded
incrementally (keys = id, watermark = modified) from a Delta source table of --rows rows in --files
daily files. After the first load, the source gets --changed updated and --new new rows, and the
incremental run is timed twice in fresh processes: once as the engine runs now (merge_writes=True)
and once the way it ran before 0.20 (read the whole table, merge in memory, overwrite it).

Reported per mode: seconds for the run, the process's peak memory (on Windows, peak working set),
files the write added and removed, the bytes it added to the table folder, and whether the two
tables ended up with the same rows. Nothing touches OneLake: everything is local Delta tables.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

DAY0 = dt.date(2026, 1, 1)


def peak_mb() -> float:
    """Peak resident memory of this process so far, in MB."""
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    except ImportError:  # Windows
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / (1024 * 1024)


def folder_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*.parquet"))


def source_rows(start: int, stop: int, day: dt.date, amount: float):
    import numpy as np
    import polars as pl
    ids = np.arange(start, stop, dtype=np.int64)
    return pl.DataFrame({"id": ids, "customer": ids % 50_000, "amount": np.full(len(ids), amount),
                         "status": ["shipped"] * len(ids), "note": ["standard delivery, no remarks"] * len(ids),
                         "modified": [day] * len(ids)})


def build(root: Path, rows: int, files: int) -> None:
    """The project, the source table and the first full load."""
    from deltalake import write_deltalake

    from fabric_desktop.meta import Engine, LocalStore, Project
    from fabric_desktop.meta.templates import init_project

    init_project(root, "3", "benchmark", sample=False)
    src = root.parent / "source"
    (root / "metadata" / "entities" / "bronze.events.toml").write_text(textwrap.dedent(f"""\
        name = "bronze.events"
        owner = "benchmark"
        [source]
        table = {json.dumps(src.as_posix())}
        [load]
        mode = "incremental"
        watermark = "modified"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        customer = "int64"
        amount = "float64"
        status = "string"
        note = "string"
        modified = "date"
        """), encoding="utf-8")
    store = LocalStore(root / ".local" / "dev", "dev")
    per = rows // files
    for i in range(files):
        # One file a day, as a daily load builds a table; then one load per day keeps it in files.
        write_deltalake(str(src), source_rows(i * per, (i + 1) * per, DAY0 + dt.timedelta(days=i), 1.0).to_arrow(),
                        mode="append")
        res = Engine(Project(root), store, DAY0 + dt.timedelta(days=i)).run("bronze.events")
        assert res.status == "ok", res.message


def change(root: Path, rows: int, files: int, changed: int, new: int, spread: str) -> None:
    """Updated rows plus brand-new ones, all with a later modified date. The updates hit the most
    recent day's rows (spread="recent", the usual case: late changes to recent orders) or rows spread
    evenly over every file (spread="all", the worst case for MERGE)."""
    import numpy as np
    import polars as pl
    from deltalake import write_deltalake
    day = DAY0 + dt.timedelta(days=1000)
    first = 0 if spread == "all" else rows - rows // files
    pool = rows - first
    step = max(pool // max(changed, 1), 1)
    upd = source_rows(first, rows, day, 2.0).filter(pl.Series(np.arange(pool) % step == 0)).head(changed)
    add = source_rows(rows, rows + new, day, 2.0)
    write_deltalake(str(root.parent / "source"), pl.concat([upd, add]).to_arrow(), mode="append")


def measure(root: Path, merge: bool) -> dict:
    """One incremental run, in this (fresh) process."""
    from deltalake import DeltaTable

    from fabric_desktop.meta import Engine, LocalStore, Project
    store = LocalStore(root / ".local" / "dev", "dev")
    uri = store.uri("LH_Bronze", "dbo", "events")
    before_bytes, before_files = folder_bytes(Path(uri)), set(DeltaTable(uri).file_uris())
    t0 = time.perf_counter()
    res = Engine(Project(root), store, DAY0 + dt.timedelta(days=1000), merge_writes=merge).run("bronze.events")
    seconds = time.perf_counter() - t0
    assert res.status == "ok", res.message
    after_files = set(DeltaTable(uri).file_uris())
    return {"mode": "Delta MERGE" if merge else "rewrite (before 0.20)", "seconds": round(seconds, 2),
            "peak_mb": round(peak_mb()), "rows_read": res.rows_read, "rows_written": res.rows_written,
            "files_added": len(after_files - before_files), "files_removed": len(before_files - after_files),
            "files_in_table": len(after_files), "mb_written": round((folder_bytes(Path(uri)) - before_bytes) / 2**20, 1),
            "write_step": next(s.detail for s in res.steps if s.name == "write")}


def checksum(root: Path) -> str:
    import polars as pl

    from fabric_desktop.meta import LocalStore
    from fabric_desktop.meta.store import read_table_uri
    df = read_table_uri(LocalStore(root / ".local" / "dev", "dev").uri("LH_Bronze", "dbo", "events"), None)
    return f"{df.height}:{int(df.drop('_run_id').hash_rows().sum())}" if df is not None else "-"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=5_000_000)
    ap.add_argument("--files", type=int, default=20)
    ap.add_argument("--changed", type=int, default=1000)
    ap.add_argument("--new", type=int, default=1000)
    ap.add_argument("--spread", choices=["recent", "all"], default="recent",
                    help="updates hit the latest day's rows (recent) or rows in every file (all)")
    ap.add_argument("--work", help="folder to build in (default: a temporary folder)")
    ap.add_argument("--_measure", help=argparse.SUPPRESS)
    ap.add_argument("--_merge", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--_checksum", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a._measure:
        print(json.dumps(measure(Path(a._measure), bool(a._merge))))
        return
    if a._checksum:
        print(checksum(Path(a._checksum)))
        return

    import shutil
    work = Path(a.work or tempfile.mkdtemp(prefix="fd-large-"))
    base = work / "base" / "project"
    t0 = time.perf_counter()
    build(base, a.rows, a.files)
    change(base, a.rows, a.files, a.changed, a.new, a.spread)
    print(f"built {a.rows:,} rows in {a.files} files in {time.perf_counter() - t0:.0f} s", file=sys.stderr)
    out = []
    for merge in (True, False):
        copy = work / ("merge" if merge else "rewrite")
        shutil.copytree(base.parent, copy)
        # Each mode runs in its own process so its peak memory is its own.
        cmd = [sys.executable, __file__, "--_measure", str(copy / "project"), "--_merge", str(int(merge))]
        r = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True,
                                      env=os.environ | {"PYTHONIOENCODING": "utf-8"}).stdout.strip().splitlines()[-1])
        r["checksum"] = subprocess.run([sys.executable, __file__, "--_checksum", str(copy / "project")], check=True,
                                       capture_output=True, text=True).stdout.strip()
        out.append(r)
    print(f"\n{a.rows:,} rows, {a.files} files; incremental run with {a.changed:,} changed "
          f"({'in every file' if a.spread == 'all' else 'recent'}) and {a.new:,} new rows\n")
    print("| Write | Seconds | Peak memory (MB) | Rows read | Files added / removed | Files in table | MB written |")
    print("| --- | --: | --: | --: | --: | --: | --: |")
    for r in out:
        print(f"| {r['mode']} | {r['seconds']} | {r['peak_mb']} | {r['rows_read']:,} | "
              f"{r['files_added']} / {r['files_removed']} | {r['files_in_table']} | {r['mb_written']} |")
    print(f"\nSame rows in both tables: {'yes' if out[0]['checksum'] == out[1]['checksum'] else 'NO'} "
          f"({out[0]['checksum'].split(':')[0]} rows)")
    print(f"MERGE write step: {out[0]['write_step']}")
    if not a.work:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
