"""A short soak: loop the engine's everyday work for a while and check that nothing it holds keeps growing.

    cd sidecar
    python ../examples/benchmark/memory_soak.py --minutes 20
    python ../examples/benchmark/memory_soak.py --minutes 10 --json soak.json --summary soak.md

This process stands in for the engine. Over and over it:

- runs a notebook cell in a ProcessKernel (the app's notebook process): the cell overwrites a small
  local Delta table and reads it back. The kernel is restarted every --restart-every cells, and the
  old process must have ended;
- runs a query on the SQL page (SqlExecutions, which starts a worker process per query) over a
  local Delta table, then reads a page of its result;
- writes through LocalStore as metadata runs do: an overwrite, a MERGE of the same ids, and a
  metadata-table append.

Every table stays the same size, so what it holds should level off. Every --sample seconds it
records this process's memory (resident on Linux, private bytes on Windows), open handles, OS and
Python threads, the kernel's memory, its child processes (Linux) and the files in its temp and SQL
results folders. After the first --warmup minutes, a measurement is "growing" when all three hold:

- its least-squares slope is past the limit per hour in LIMITS;
- that slope over the measured time adds up to at least the limit's minimum rise;
- the median of the last third of the samples is above the median of the first third by at least
  half that rise (a straight climb gives two thirds of it), so one late jump or a sawtooth doesn't
  count, only a steady climb.

It is advisory: a growing measurement is reported (a GitHub warning in CI) and the exit code is 0,
unless --gate is given, when it is 1. A step that fails is not advisory: the soak exits 2, since then
it measured nothing. Nothing touches OneLake, Fabric or the network; everything is in a temporary
folder, with LOCALAPPDATA and HOME pointed there, that is removed at the end. How to read it:
docs/performance.md#memory-soak.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

SIDECAR = Path(__file__).resolve().parents[2] / "sidecar"

# Measurement: (growth per hour that counts, the least total rise that counts, unit). Chosen from the
# runs in docs/performance.md#memory-soak: well above what a flat run's noise gives over 15 minutes,
# and well below what a leak of one object per loop would.
LIMITS: dict[str, tuple[float, float, str]] = {
    "engine_mb": (60.0, 25.0, "MB"),
    "handles": (40.0, 10.0, "handles"),
    "os_threads": (40.0, 10.0, "threads"),
    "py_threads": (12.0, 3.0, "threads"),
    "children": (12.0, 3.0, "processes"),
    "files": (40.0, 10.0, "files"),
}

CELL = """
import polars as pl
from deltalake import write_deltalake
n = globals().get('n', 0) + 1
write_deltalake({path!r}, pl.DataFrame({{'i': list(range(2000)), 'n': [n] * 2000, 's': ['x' * 40] * 2000}}).to_arrow(),
                mode='overwrite')
df = pl.read_delta({path!r})
assert df.height == 2000 and df['n'][0] == n, df.height
df.tail(20)
"""
QUERY = "SELECT country, count(*) AS n, sum(amount) AS total FROM sales GROUP BY country ORDER BY 1"


# ---- measuring

def handles() -> int | None:
    """Open handles (Windows) or file descriptors (Linux) of this process."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetProcessHandleCount.restype = wintypes.BOOL
        count = wintypes.DWORD()
        return int(count.value) if k.GetProcessHandleCount(k.GetCurrentProcess(), ctypes.byref(count)) else None
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return None


def os_threads() -> int | None:
    """This process's OS threads (Linux only: Windows has no cheap call for it)."""
    try:
        for line in Path(f"/proc/{os.getpid()}/status").read_text().splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def children() -> int | None:
    """Live child processes of this process (Linux only)."""
    try:
        tasks = Path(f"/proc/{os.getpid()}/task")
        return sum(len((t / "children").read_text().split()) for t in tasks.iterdir())
    except OSError:
        return None


def files(*folders: Path | None) -> int:
    count = 0
    for folder in folders:
        if folder is not None:
            for _, _, names in os.walk(folder):
                count += len(names)
    return count


# ---- judging

def growth(points: list[tuple[float, float]], per_hour: float, min_rise: float, warmup: float) -> dict[str, Any]:
    """Whether the samples ``(seconds, value)`` after ``warmup`` seconds climb steadily (see the module doc)."""
    kept = [(t, v) for t, v in points if t >= warmup]
    if len(kept) < 6 or kept[-1][0] - kept[0][0] <= 0:
        return {"status": "too few samples", "samples": len(kept)}
    ts, vs = [t for t, _ in kept], [v for _, v in kept]
    mt, mv = statistics.fmean(ts), statistics.fmean(vs)
    spread = sum((t - mt) ** 2 for t in ts)
    slope = sum((t - mt) * (v - mv) for t, v in kept) / spread * 3600  # per hour
    hours = (ts[-1] - ts[0]) / 3600
    third = len(vs) // 3
    step = statistics.median(vs[-third:]) - statistics.median(vs[:third])
    grows = slope > per_hour and slope * hours >= min_rise and step >= slope * hours / 2
    return {"status": "growing" if grows else "flat", "per_hour": round(slope, 2), "rise": round(slope * hours, 2),
            "thirds_step": round(step, 2), "first": vs[0], "last": vs[-1], "min": min(vs), "max": max(vs),
            "samples": len(kept)}


def judge(samples: list[dict[str, Any]], warmup: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, (per_hour, min_rise, unit) in LIMITS.items():
        points = [(s["t"], float(s[name])) for s in samples if s.get(name) is not None]
        if not points:
            out[name] = {"status": "not measured here", "unit": unit}
            continue
        out[name] = {**growth(points, per_hour, min_rise, warmup), "limit_per_hour": per_hour, "min_rise": min_rise,
                     "unit": unit}
    return out


def summary(result: dict[str, Any]) -> str:
    lines = [f"### Memory soak ({result['machine']['system']}, {result['minutes']} min, "
             f"{result['loops']} loops, {result['errors']} failed steps)", "",
             "| Measurement | Result | Per hour (limit) | Rise | First, last after warm-up |", "| --- | --- | --- | --- | --- |"]
    for name, j in result["growth"].items():
        if "per_hour" not in j:
            lines.append(f"| {name} | {j['status']} | | | |")
            continue
        lines.append(f"| {name} | {j['status']} | {j['per_hour']} {j['unit']} ({j['limit_per_hour']}) | {j['rise']} | "
                     f"{j['first']:g}, {j['last']:g} |")
    if result["last_error"]:
        lines += ["", "Last failed steps: " + json.dumps(result["last_error"])]
    return "\n".join(lines) + "\n"


# ---- the soak

def soak(work: Path, minutes: float, sample: float, restart_every: int, log: Any) -> dict[str, Any]:
    import pyarrow as pa
    import polars as pl
    from deltalake import write_deltalake

    from fabric_desktop import sql_exec
    from fabric_desktop.kernel_proc import ProcessKernel, memory_used
    from fabric_desktop.meta.store import LocalStore

    lake = work / "lake"
    write_deltalake(str(lake / "sales"), pa.table({"id": list(range(1000)), "country": ["NL", "US", "DE", "FR"] * 250,
                                                   "amount": [float(i) for i in range(1000)]}), mode="overwrite")
    sql = sql_exec.SqlExecutions(work / "data", token=lambda: "x", downloads=lambda: work / "Downloads", test_lake=lake,
                                 limits=lambda: {"sql_page_rows": 1000, "sql_time_limit_minutes": 10,
                                                 "sql_memory_limit_percent": 30, "sql_max_concurrent": 2,
                                                 "sql_result_retention_hours": 24, "sql_temp_dir": ""})
    ref = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test", "local_root": str(lake), "test_lake": str(lake)}
    store = LocalStore(work / "store", "dev")
    frame = pl.DataFrame({"id": list(range(2000)), "amount": [float(i) for i in range(2000)]})
    cell = CELL.format(path=str(work / "nb" / "events"))
    kernel = ProcessKernel()

    counts = {"cells": 0, "restarts": 0, "sql": 0, "writes": 0}
    errors, last_error, loops, samples = 0, {}, 0, []
    start = time.monotonic()
    next_sample = start

    def step(name: str, fn: Any) -> None:
        nonlocal errors
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - recorded, reported and fails the soak (exit 2)
            errors += 1
            last_error[name] = repr(e)[:400]

    def notebook() -> None:
        r = kernel.run(cell)
        counts["cells"] += 1
        if r["status"] != "ok":
            raise RuntimeError(str(r["outputs"])[-400:])
        if counts["cells"] % restart_every == 0:
            old = kernel._proc
            kernel.restart()
            counts["restarts"] += 1
            if old is not None and old.poll() is None:
                raise RuntimeError(f"the kernel process {old.pid} was still running after a restart")

    def query() -> None:
        rid = sql.start(QUERY, lakehouse_ref=ref)["id"]
        deadline = time.monotonic() + 120
        while sql.status(rid)["state"] not in sql_exec.FINISHED and time.monotonic() < deadline:
            time.sleep(0.02)
        st = sql.status(rid)
        if st["state"] != "completed":
            raise RuntimeError(str(st)[:400])
        if len(sql.page(rid, 0, 100)["rows"]) != 4:
            raise RuntimeError("the SQL page did not have the 4 countries")
        counts["sql"] += 1

    def writes() -> None:
        store.write("LH_Bronze", "dbo", "soak", frame, mode="overwrite")
        store.upsert("LH_Bronze", "dbo", "soak", frame.with_columns(pl.col("amount") + loops), on=["id"])
        store.meta_append("watermarks", [{"entity": "soak", "value": str(loops), "run_id": f"soak-{loops}"}])
        counts["writes"] += 1

    try:
        while time.monotonic() - start < minutes * 60:
            step("notebook", notebook)
            step("sql", query)
            step("writes", writes)
            loops += 1
            if time.monotonic() >= next_sample:
                gc.collect()
                kernel_pid = kernel.live_pid
                s = {"t": round(time.monotonic() - start, 1), "loops": loops, **counts, "errors": errors,
                     "engine_mb": round((memory_used(os.getpid()) or 0) / 2**20, 1), "handles": handles(),
                     "os_threads": os_threads(), "py_threads": threading.active_count(), "children": children(),
                     "kernel_mb": round((memory_used(kernel_pid) or 0) / 2**20, 1) if kernel_pid else None,
                     "files": files(Path(tempfile.gettempdir()), sql.base(), kernel.output_dir)}
                samples.append(s)
                print(json.dumps(s), file=log, flush=True)
                next_sample += sample
    finally:
        kernel.close()
        sql.shutdown()
    return {"loops": loops, "counts": counts, "errors": errors, "last_error": last_error, "samples": samples}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--warmup", type=float, default=5, help="minutes left out of the growth check")
    ap.add_argument("--sample", type=float, default=15, help="seconds between samples")
    ap.add_argument("--restart-every", type=int, default=25, help="notebook cells between kernel restarts")
    ap.add_argument("--json", type=Path, help="also write the result and every sample here")
    ap.add_argument("--summary", type=Path, help="append a Markdown summary here (CI's job summary)")
    ap.add_argument("--gate", action="store_true", help="exit 1 when a measurement grows (without it, only report)")
    args = ap.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="kf-soak-"))
    home = work / "home"
    home.mkdir()
    (work / "tmp").mkdir()
    os.environ.update(LOCALAPPDATA=str(home), HOME=str(home), USERPROFILE=str(home), PYTHONUNBUFFERED="1",
                      TMPDIR=str(work / "tmp"), TEMP=str(work / "tmp"), TMP=str(work / "tmp"))
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(SIDECAR), os.environ.get("PYTHONPATH", "")) if p)
    tempfile.tempdir = str(work / "tmp")
    sys.path.insert(0, str(SIDECAR))
    try:
        run = soak(work, args.minutes, args.sample, args.restart_every, sys.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result = {"minutes": args.minutes, "warmup_minutes": args.warmup,
              "machine": {"system": platform.system(), "python": platform.python_version(), "cpus": os.cpu_count()},
              **run, "growth": judge(run["samples"], args.warmup * 60)}
    if args.json:
        args.json.write_text(json.dumps(result, indent=1))
    text = summary(result)
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as f:
            f.write(text)
    growing = [n for n, j in result["growth"].items() if j["status"] == "growing"]
    for name in growing:
        j = result["growth"][name]
        print(f"::warning title=Memory soak::{name} grew {j['per_hour']} {j['unit']} per hour after the warm-up, "
              f"past the limit of {j['limit_per_hour']} (from {j['first']:g} to {j['last']:g})")
    if run["errors"]:
        print(f"::error title=Memory soak::{run['errors']} steps failed: {json.dumps(run['last_error'])}")
        return 2
    return 1 if growing and args.gate else 0


if __name__ == "__main__":
    sys.exit(main())
