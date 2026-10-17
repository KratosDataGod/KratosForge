"""Measure a full load bigger than memory: one read of the whole source versus [load] batch_by.

    cd sidecar
    python ../examples/benchmark/batched_loads.py --scale 1
    python ../examples/benchmark/batched_loads.py --scale 10 --only batched

It builds a throwaway project in a temporary folder with one entity, bronze.lineitem, a full load of
a Delta source table shaped like TPC-H's lineitem (the same 16 columns and types, about 6 million
rows per scale factor, ship dates over 1992 to 1998), with one derived column and one check. The
rows are generated with NumPy, not by the TPC-H dbgen, so the values are not TPC-H's own. The load
then runs in fresh processes: once reading the whole source (memory_guard = false, so it really
tries) and once with batch_by = "l_shipdate" (rows per batch from free memory, or --batch-rows).

Reported per way: seconds, the process's peak memory (on Windows, peak working set), rows written,
Delta commits the load made, and whether the two tables hold the same rows. --only picks one way,
for a scale whose single read wouldn't fit: that process would run out of memory. Nothing touches
OneLake: everything is local Delta tables.
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

from large_tables import peak_mb

ROWS_PER_SCALE = 6_000_000
CHUNK = 2_000_000
DAY0 = dt.date(1992, 1, 2)
SHIP_DAYS = 2526  # 1992-01-02 to 1998-12-01, as in TPC-H


def chunk(start: int, n: int, seed: int):
    import numpy as np
    import polars as pl
    rng = np.random.default_rng(seed)
    ship = rng.integers(0, SHIP_DAYS, n)
    day0 = np.datetime64(DAY0.isoformat(), "D")
    words = np.array(["carefully", "final", "deposits", "quickly", "regular", "ironic", "packages", "furiously"])
    return pl.DataFrame({
        "l_orderkey": np.arange(start, start + n, dtype=np.int64) // 4,
        "l_partkey": rng.integers(1, 200_000 * 10, n),
        "l_suppkey": rng.integers(1, 10_000 * 10, n),
        "l_linenumber": (np.arange(start, start + n) % 4 + 1).astype(np.int32),
        "l_quantity": rng.integers(1, 51, n).astype(np.float64),
        "l_extendedprice": rng.uniform(900, 105_000, n).round(2),
        "l_discount": rng.integers(0, 11, n) / 100,
        "l_tax": rng.integers(0, 9, n) / 100,
        "l_returnflag": rng.choice(np.array(["A", "N", "R"]), n),
        "l_linestatus": rng.choice(np.array(["F", "O"]), n),
        "l_shipdate": day0 + ship,
        "l_commitdate": day0 + ship + rng.integers(-60, 60, n),
        "l_receiptdate": day0 + ship + rng.integers(1, 31, n),
        "l_shipinstruct": rng.choice(np.array(["DELIVER IN PERSON", "COLLECT COD", "NONE", "TAKE BACK RETURN"]), n),
        "l_shipmode": rng.choice(np.array(["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"]), n),
        "l_comment": [" ".join(w) for w in rng.choice(words, (n, 3))],
    })


COLUMNS = {"l_orderkey": "int64", "l_partkey": "int64", "l_suppkey": "int64", "l_linenumber": "int32",
           "l_quantity": "float64", "l_extendedprice": "float64", "l_discount": "float64", "l_tax": "float64",
           "l_returnflag": "string", "l_linestatus": "string", "l_shipdate": "date", "l_commitdate": "date",
           "l_receiptdate": "date", "l_shipinstruct": "string", "l_shipmode": "string", "l_comment": "string",
           "net_price": "float64"}


def build(work: Path, rows: int) -> None:
    from deltalake import write_deltalake

    src = work / "source"
    for i, start in enumerate(range(0, rows, CHUNK)):
        write_deltalake(str(src), chunk(start, min(CHUNK, rows - start), i).to_arrow(), mode="append")


def project(root: Path, src: Path, batched: bool) -> None:
    from fabric_desktop.meta.templates import init_project

    init_project(root, "3", "benchmark", sample=False)
    cols = "\n".join(f"{c} = {json.dumps(t)}" for c, t in COLUMNS.items())
    (root / "metadata" / "entities" / "bronze.lineitem.toml").write_text(textwrap.dedent(f"""\
        name = "bronze.lineitem"
        owner = "benchmark"
        [source]
        table = {json.dumps(src.as_posix())}
        [load]
        mode = "full"
        memory_guard = false
        {'batch_by = "l_shipdate"' if batched else ''}
        [contract]
        [contract.columns]
        """) + cols + textwrap.dedent("""

        [[derive]]
        column = "net_price"
        expr = "l_extendedprice * (1 - l_discount)"

        [[check]]
        column = "l_orderkey"
        not_null = true
        on_fail = "stop"
        """), encoding="utf-8")


def measure(root: Path) -> dict:
    """The load, in this (fresh) process."""
    from deltalake import DeltaTable

    from fabric_desktop.meta import Engine, LocalStore, Project
    store = LocalStore(root / ".local" / "dev", "dev")
    engine = Engine(Project(root), store)
    engine.batch_rows = int(os.environ["FD_BATCH_ROWS"]) if os.environ.get("FD_BATCH_ROWS") else None
    t0 = time.perf_counter()
    res = engine.run("bronze.lineitem")
    seconds = time.perf_counter() - t0
    out = {"seconds": round(seconds, 1), "peak_mb": round(peak_mb()), "status": res.status, "message": res.message,
           "rows_written": res.rows_written, "steps": [s.__dict__ for s in res.steps]}
    if res.status == "ok":
        out["commits"] = len(DeltaTable(store.uri("LH_Bronze", "dbo", "lineitem")).history())
    return out


def checksum(root: Path) -> str:
    import polars as pl

    from fabric_desktop.meta import LocalStore
    uri = LocalStore(root / ".local" / "dev", "dev").uri("LH_Bronze", "dbo", "lineitem")
    lf = pl.scan_delta(uri).drop("_run_id", "_loaded_at")
    # A sum of row hashes, worked out in streaming mode so the check itself fits in memory.
    row = lf.select(pl.len().alias("n"), pl.struct(pl.all()).hash().sum().alias("h")).collect(engine="streaming")
    return f"{row['n'][0]}:{row['h'][0]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=1, help="TPC-H scale factor: about 6 million rows each")
    ap.add_argument("--only", choices=["single", "batched"], help="run one way only")
    ap.add_argument("--batch-rows", type=int, help="rows per batch (default: worked out from free memory)")
    ap.add_argument("--work", help="folder to build in (default: a temporary folder); a source already there is reused")
    ap.add_argument("--_measure", help=argparse.SUPPRESS)
    ap.add_argument("--_checksum", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a._measure:
        print(json.dumps(measure(Path(a._measure)), default=str))
        return
    if a._checksum:
        print(checksum(Path(a._checksum)))
        return

    import shutil
    rows = int(a.scale * ROWS_PER_SCALE)
    work = Path(a.work or tempfile.mkdtemp(prefix="fd-batched-"))
    t0 = time.perf_counter()
    if not (work / "source").exists():
        build(work, rows)
        print(f"built {rows:,} source rows in {time.perf_counter() - t0:.0f} s", file=sys.stderr)
    out = {}
    for way in ("single", "batched"):
        if a.only and way != a.only:
            continue
        root = work / way
        shutil.rmtree(root, ignore_errors=True)
        project(root, work / "source", way == "batched")
        # Each way runs in its own process so its peak memory is its own.
        p = subprocess.run([sys.executable, __file__, "--_measure", str(root)], capture_output=True, text=True,
                           env=os.environ | {"PYTHONIOENCODING": "utf-8", "FD_BATCH_ROWS": str(a.batch_rows or "")})
        if p.returncode:
            out[way] = {"status": f"process ended with code {p.returncode} (out of memory if negative 9)",
                        "seconds": "-", "peak_mb": "-", "rows_written": 0}
            continue
        r = json.loads(p.stdout.strip().splitlines()[-1])
        if r["status"] == "ok":
            r["checksum"] = subprocess.run([sys.executable, __file__, "--_checksum", str(root)], check=True,
                                           capture_output=True, text=True).stdout.strip()
        out[way] = r
    print(f"\n{rows:,} rows (lineitem-shaped, scale factor {a.scale:g}), full load\n")
    print("| Read | Seconds | Peak memory (MB) | Rows written | Delta commits | Result |")
    print("| --- | --: | --: | --: | --: | --- |")
    for way, r in out.items():
        name = "whole source at once" if way == "single" else 'batch_by = "l_shipdate"'
        print(f"| {name} | {r['seconds']} | {r['peak_mb']} | {r['rows_written']:,} | {r.get('commits', '-')} | "
              f"{r['status']}{': ' + r['message'] if r.get('message') else ''} |")
    sums = [r.get("checksum") for r in out.values()]
    if len(sums) == 2 and all(sums):
        print(f"\nSame rows in both tables: {'yes' if sums[0] == sums[1] else 'NO'}")
    if "batched" in out and out["batched"].get("steps"):
        print("Batched plan: " + out["batched"]["steps"][0]["detail"])
    if not a.work:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
