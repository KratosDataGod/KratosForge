"""Time the engine's start-up, a notebook's start and local SQL queries, the same way each time.

    cd sidecar
    python ../examples/benchmark/startup_and_sql.py
    python ../examples/benchmark/startup_and_sql.py --runs 10 --rows 1000000 --json bench.json

Three measurements, each repeated --runs times, reported as median, p95 (the nearest-rank value,
so with few runs it is the slowest) and min, in milliseconds. One uncounted start-up and notebook
start come first (--warmup), since the very first compiles bytecode and fills the OS file cache:

- start-up: a fresh ``python -m fabric_desktop.server``, as the app starts it, from the process
  starting to its first answer (``app_settings``) on the RPC pipe. It runs with LOCALAPPDATA and
  HOME pointed at a throwaway folder, so it reads and writes none of your own settings.
- notebook start: a fresh process that imports the kernel, makes a ``Kernel()`` and runs a first
  cell (``1 + 1``), then a first spark cell (``spark.range(1000).count()``). Reported to the first
  cell's result and to the first spark cell's result, both from the process starting.
- SQL: a local data project over a generated Parquet file of --rows rows. The first query after
  the project opens is timed once per run (a fresh project each run); then each query below runs
  --runs times on the open project, in DuckDB and, for the T-SQL one, through the T-SQL translator.

Nothing touches OneLake, Fabric or the network: everything is local, in a temporary folder that is
removed at the end. The numbers depend on the PC; compare runs on the same PC. The baseline and how
to read it are in docs/performance.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

SIDECAR = Path(__file__).resolve().parents[2] / "sidecar"

QUERIES: list[tuple[str, str, str]] = [
    ("count", "duckdb", "SELECT COUNT(*) FROM dbo.events"),
    ("group by", "duckdb", "SELECT region, COUNT(*), SUM(amount) FROM dbo.events GROUP BY region ORDER BY region"),
    ("filter", "duckdb", "SELECT * FROM dbo.events WHERE id = 4242"),
    ("t-sql top", "tsql", "SELECT TOP 10 region, SUM(amount) AS total FROM dbo.events GROUP BY region ORDER BY total DESC"),
]

NOTEBOOK = textwrap.dedent("""
    import json, sys, time
    t0 = float(sys.argv[1])
    from fabric_desktop.notebook import Kernel
    k = Kernel()
    first = k.run("1 + 1")
    t1 = time.time()
    spark = k.run("spark.range(1000).count()")
    t2 = time.time()
    ok = first.get("status") == "ok" and spark.get("status") == "ok"
    print(json.dumps({"first_cell": (t1 - t0) * 1000, "first_spark_cell": (t2 - t0) * 1000, "ok": ok,
                      "error": None if ok else str(first.get("error") or spark.get("error"))}))
""")


def summary(ms: list[float]) -> dict[str, float]:
    ordered = sorted(ms)
    rank = max(0, -(-95 * len(ordered) // 100) - 1)  # nearest rank
    return {"median": round(statistics.median(ordered), 1), "p95": round(ordered[rank], 1),
            "min": round(ordered[0], 1), "runs": len(ordered)}


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ, LOCALAPPDATA=str(home), HOME=str(home), USERPROFILE=str(home), PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SIDECAR), env.get("PYTHONPATH", "")) if p)
    return env


def startup(home: Path, timeout: float) -> float:
    """Milliseconds from starting the engine to its first answer."""
    started = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.server"], cwd=SIDECAR, env=_env(home),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"id": 1, "method": "app_settings", "params": {}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()  # the engine answers only once it is serving
        elapsed = (time.perf_counter() - started) * 1000
        reply = json.loads(line) if line else {}
        if reply.get("id") != 1 or "result" not in reply:
            raise RuntimeError(f"the engine did not answer: {line.strip() or 'no reply'}")
        return elapsed
    finally:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(timeout=timeout)
        except Exception:  # noqa: BLE001 - a benchmark run; make sure it ends
            proc.kill()
            proc.wait()


def notebook_start(home: Path, timeout: float) -> dict[str, float]:
    started = time.time()
    out = subprocess.run([sys.executable, "-c", NOTEBOOK, repr(started)], cwd=SIDECAR, env=_env(home),
                         capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
    if out.returncode or not lines:
        raise RuntimeError(f"the notebook run failed: {(out.stderr or out.stdout).strip()[-500:]}")
    result = json.loads(lines[-1])
    if not result["ok"]:
        raise RuntimeError(f"a notebook cell failed: {result['error']}")
    return result


def make_data(folder: Path, rows: int) -> None:
    import duckdb
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / "events.parquet").as_posix()
    duckdb.connect().execute(f"""
        COPY (SELECT range AS id, ['north', 'south', 'east', 'west'][(range % 4) + 1] AS region,
                     (range * 7 % 1000) / 10.0 AS amount, DATE '2024-01-01' + CAST(range % 365 AS INTEGER) AS day
              FROM range({int(rows)})) TO '{target}' (FORMAT parquet)""")


def sql(work: Path, rows: int, runs: int) -> dict[str, Any]:
    sys.path.insert(0, str(SIDECAR))
    from fabric_desktop.localdata import LocalData
    data = work / "data"
    make_data(data, rows)
    first: list[float] = []
    timings: dict[str, list[float]] = {name: [] for name, _, _ in QUERIES}
    for run in range(runs):
        local = LocalData(work / f"local-data-{run}")
        try:
            pid = local.create("Benchmark")["id"]
            local.add_source(pid, str(data))
            engine = local.engine(pid)
            started = time.perf_counter()
            engine.execute(QUERIES[0][2])
            first.append((time.perf_counter() - started) * 1000)
            if run == 0:  # a fresh project each run for the first query; the others on one open project
                for _ in range(runs):
                    for name, dialect, text in QUERIES:
                        started = time.perf_counter()
                        engine.execute(text, dialect=dialect)
                        timings[name].append((time.perf_counter() - started) * 1000)
        finally:
            local.close_all()
    return {"rows": rows, "first query on a new project": summary(first), **{name: summary(ms) for name, ms in timings.items()}}


def bench(runs: int, rows: int, warmup: int = 1, timeout: float = 120.0) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kf-bench-") as tmp:
        work = Path(tmp)
        home = work / "home"
        home.mkdir()
        for _ in range(warmup):  # not counted: the first start compiles bytecode and fills the OS file cache
            startup(home, timeout)
            notebook_start(home, timeout)
        starts = [startup(home, timeout) for _ in range(runs)]
        notebooks = [notebook_start(home, timeout) for _ in range(runs)]
        result: dict[str, Any] = {
            "machine": {"python": platform.python_version(), "system": platform.platform(),
                        "cpus": os.cpu_count()},
            "runs": runs, "warmup": warmup,
            "startup": summary(starts),
            "notebook first cell": summary([n["first_cell"] for n in notebooks]),
            "notebook first spark cell": summary([n["first_spark_cell"] for n in notebooks]),
            "sql": sql(work, rows, runs),
        }
    return result


def table(result: dict[str, Any]) -> str:
    lines = [f"{result['runs']} runs on {result['machine']['system']}, Python {result['machine']['python']}, "
             f"{result['machine']['cpus']} CPUs; milliseconds", "",
             "| Measurement | Median | p95 | Min |", "| --- | ---: | ---: | ---: |"]
    rows = [(k, result[k]) for k in ("startup", "notebook first cell", "notebook first spark cell")]
    rows += [(f"SQL {k} ({result['sql']['rows']:,} rows)", v) for k, v in result["sql"].items() if k != "rows"]
    for name, s in rows:
        lines.append(f"| {name} | {s['median']:,} | {s['p95']:,} | {s['min']:,} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="times each measurement is repeated (default 5)")
    parser.add_argument("--rows", type=int, default=1_000_000, help="rows in the SQL table (default 1,000,000)")
    parser.add_argument("--warmup", type=int, default=1, help="uncounted start-ups and notebook starts first (default 1)")
    parser.add_argument("--json", type=Path, help="also write the result to this file")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.rows < 1 or args.warmup < 0:
        parser.error("--runs and --rows take a whole number of 1 or more, --warmup of 0 or more")
    result = bench(args.runs, args.rows, args.warmup)
    print(table(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
