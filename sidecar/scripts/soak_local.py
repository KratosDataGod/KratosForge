"""Soak the engine on this PC: notebook cells, local SQL and exports in a loop, watching for leaks.

Runs the real Service in this process against a local data project (no Fabric, no sign-in) and, each
loop, runs a notebook cell, a background local SQL run with a CSV and a Parquet export, and an in-process
local query. After each loop it samples the process's memory (RSS), open file handles, threads and the
files left in the app's folders and the temp folder, and at the end prints the first and last samples
and each one's slope per hour (a least-squares line over the second half, after warm-up).

    python scripts/soak_local.py --minutes 60 [--out soak.jsonl]

It exits 1 when a count other than memory keeps growing (more than ``--tolerance`` over the run).
Memory is reported, not judged: Python and DuckDB keep freed memory for reuse.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rss_mb(pid: int | str = "self") -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:  # Windows, or anywhere psutil is installed
        import psutil
        return psutil.Process(None if pid == "self" else int(pid)).memory_info().rss / 2**20
    except ImportError:
        return float("nan")


def handles(pid: int | str = "self") -> int:
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        try:
            import psutil
            return psutil.Process(None if pid == "self" else int(pid)).num_handles()  # type: ignore[attr-defined, no-any-return]
        except (ImportError, AttributeError):
            return -1


def _threads(pid: int) -> int:
    try:
        return len(os.listdir(f"/proc/{pid}/task"))
    except OSError:
        return -1


def _children() -> int:
    """Processes this one started that are still running (a SQL worker, a notebook kernel)."""
    try:
        return sum(1 for p in os.listdir("/proc") if p.isdigit() and
                   Path(f"/proc/{p}/stat").read_text().split(") ")[-1].split()[1] == str(os.getpid()))
    except OSError:
        return -1


def files_under(*folders: Path) -> int:
    return sum(len(files) for folder in folders if folder.exists() for _, _, files in os.walk(folder))


def slope_per_hour(samples: list[dict], key: str) -> float:
    half = samples[len(samples) // 2:]
    if len(half) < 2:
        return 0.0
    xs = [s["t"] for s in half]
    ys = [s[key] for s in half]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    return 0.0 if not var else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var * 3600


def wait(get, done, limit: float = 120.0) -> dict:
    deadline = time.monotonic() + limit
    while True:
        status = get()
        if done(status) or time.monotonic() > deadline:
            return status
        time.sleep(0.05)


class Soak:
    def __init__(self, root: Path):
        from fabric_desktop import known_folders
        from fabric_desktop.service import Service

        self.root = root
        self.downloads = root / "Downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        known_folders.downloads = lambda: self.downloads  # exports land here, not in this PC's Downloads
        self.svc = Service(root=root / "app")
        data = root / "data"
        data.mkdir()
        (data / "orders.csv").write_text("id,amount,day\n" + "".join(
            f"{i},{i * 1.5},2026-11-{1 + i % 28:02d}\n" for i in range(5000)), encoding="utf-8")
        self.pid = self.svc.local_create_project("Soak")["id"]
        self.svc.local_add_source(self.pid, str(data))
        self.loops = 0

    def loop(self) -> None:
        svc, i = self.svc, self.loops
        cell = svc.run_cell(
            "import duckdb, pandas as pd\n"
            f"df = pd.DataFrame({{'x': range({1000 + i % 50})}})\n"
            "con = duckdb.connect()\n"
            "n = con.execute('SELECT count(*) FROM df').fetchone()[0]\n"
            "con.close()\n"
            "print(n)\n")
        assert cell.get("status") == "ok", cell
        eid = svc.sql_start("SELECT day, count(*) AS n, sum(amount) AS total FROM dbo.orders GROUP BY day "
                            "ORDER BY day", target="local", project=self.pid, dialect="duckdb")["id"]
        status = wait(lambda: svc.sql_status(eid), lambda s: s["state"] not in ("queued", "running"))
        assert status["state"] == "completed", status
        for fmt in ("csv", "parquet"):
            xid = svc.sql_export(eid, fmt, name=f"soak-{i}")["id"]
            done = wait(lambda xid=xid: svc.sql_export_status(xid), lambda s: s["state"] not in ("queued", "running"))
            assert done["state"] == "completed", done
            Path(done["path"]).unlink()  # a person moves or deletes what they exported
        svc.local_query(self.pid, "SELECT max(amount) FROM dbo.orders")
        self.loops += 1

    def sample(self, started: float) -> dict:
        return {"t": round(time.monotonic() - started, 1), "loops": self.loops, "rss_mb": round(rss_mb(), 1),
                "handles": handles(), "threads": threading.active_count(),
                "app_files": files_under(self.root / "app"), "downloads": files_under(self.downloads),
                "temp_files": files_under(Path(tempfile.gettempdir())) - files_under(self.root),
                "objects": len(gc.get_objects()), **self.kept()}

    def kept(self) -> dict:
        """What the engine keeps between calls: each must stop growing once its limit or retention is reached."""
        runs = self.svc.sql_runs
        kernel = self.svc.sessions.kernel(None)
        proc = getattr(kernel, "_proc", None)  # a notebook kernel in its own process (the default)
        child = {"kernel_rss_mb": round(rss_mb(proc.pid), 1), "kernel_handles": handles(proc.pid),
                 "kernel_threads": _threads(proc.pid)} if proc is not None else {}
        return {"sql_runs": len(runs._runs), "sql_exports": len(runs._exports),
                "local_queries": len(self.svc.local_data._queries), "engine_children": _children()} | child

    def close(self) -> None:
        self.svc.local_data.close_all()
        for kernel in self.svc.sessions.kernels():  # a notebook kernel in its own process ends with it
            getattr(kernel, "close", lambda: None)()


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--minutes", type=float, default=5.0)
    args.add_argument("--out", default="")
    args.add_argument("--warmup", type=int, default=40, help="loops before the first sample")
    args.add_argument("--tolerance", type=int, default=8, help="growth allowed in a count over the run")
    opts = args.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="kf-soak-") as folder:
        soak = Soak(Path(folder))
        started = time.monotonic()
        for _ in range(opts.warmup):  # imports, the first connections, and runs filling their kept number
            soak.loop()
        samples = [soak.sample(started)]
        out = open(opts.out, "a", encoding="utf-8") if opts.out else None
        try:
            while time.monotonic() - started < opts.minutes * 60:
                soak.loop()
                samples.append(soak.sample(started))
                if out:
                    out.write(json.dumps(samples[-1]) + "\n")
                    out.flush()
        finally:
            if out:
                out.close()
            soak.close()
    first, last = samples[0], samples[-1]
    print(f"{last['loops']} loops in {last['t'] / 60:.1f} min")
    grew = []
    for key in ("rss_mb", "objects", "handles", "threads", "app_files", "downloads", "temp_files", "sql_runs",
                "sql_exports", "local_queries", "engine_children", "kernel_rss_mb", "kernel_handles", "kernel_threads"):
        if key not in last:
            continue
        print(f"  {key:<11} {first[key]:>9} -> {last[key]:<9} slope {slope_per_hour(samples, key):+.1f}/h")
        if key not in ("rss_mb", "objects", "kernel_rss_mb") and last[key] - first[key] > opts.tolerance:
            grew.append(key)
    if grew:
        print("still growing:", ", ".join(grew))
    return 1 if grew else 0


if __name__ == "__main__":
    raise SystemExit(main())
