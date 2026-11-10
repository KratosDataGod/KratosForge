"""Compare a run of startup_and_sql.py with the recorded baseline, or record a new baseline.

    cd sidecar
    python ../examples/benchmark/startup_and_sql.py --json bench.json
    python ../examples/benchmark/compare.py bench.json --baselines ../examples/benchmark/baselines --image windows-2025-vs2026
    python ../examples/benchmark/compare.py bench.json --write-baseline ../examples/benchmark/baselines/windows-2025-vs2026.json \\
        --runner "GitHub Actions windows-2025-vs2026"

Each measurement's median is compared with the baseline's median. A measurement counts as a
regression only when it is more than --ratio times the baseline (1.5 by default) AND more than
--slack-ms milliseconds slower (25 by default), so a 1 ms query that takes 2 ms on a busy runner
isn't one. The table goes to stdout (and to --summary, such as $GITHUB_STEP_SUMMARY). On GitHub
Actions each regression is also a warning annotation.

Each runner image has its own baseline, <--baselines>/<--image>.json; an image with none recorded
compares nothing and can't fail. With --streak FILE, the measurements past the margin are kept in
FILE for the next run, and --gate fails only on a measurement past the margin in this run AND the
last one on the same image (two runs in a row), so one slow runner isn't a failure. Without
--streak, --gate fails on any regression. Exit code: 1 only with --gate (see docs/performance.md).
A baseline is only ever written by hand (--write-baseline) and committed in a reviewed change.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

STARTS = ("startup", "notebook first cell", "notebook first spark cell")
SETTINGS = ("runs", "warmup")


def medians(result: dict[str, Any]) -> dict[str, float]:
    """Each measurement's median, by the name the benchmark's table uses."""
    out = {name: float(result[name]["median"]) for name in STARTS if name in result}
    for name, s in result.get("sql", {}).items():
        if name != "rows":
            out[f"SQL {name}"] = float(s["median"])
    return out


def settings(result: dict[str, Any]) -> dict[str, Any]:
    return {**{k: result.get(k) for k in SETTINGS}, "rows": result.get("sql", {}).get("rows")}


def compare(result: dict[str, Any], baseline: dict[str, Any], ratio: float = 1.5,
            slack_ms: float = 25.0) -> list[dict[str, Any]]:
    """One row per measurement in the run: its median, the baseline's, and a status of
    "regression", "faster", "within range", "no baseline" or "not comparable" (the SQL lines,
    when the run used a different table size from the baseline)."""
    base = baseline.get("medians", {})
    same_rows = settings(result)["rows"] == baseline.get("settings", {}).get("rows")
    rows = []
    for name, value in medians(result).items():
        before = base.get(name)
        if before is None:
            status = "no baseline"
        elif name.startswith("SQL ") and not same_rows:
            status = "not comparable"
        elif value > before * ratio and value - before > slack_ms:
            status = "regression"
        elif value * ratio < before and before - value > slack_ms:
            status = "faster"
        else:
            status = "within range"
        rows.append({"name": name, "median": value, "baseline": before, "status": status,
                     "change": None if not before else round(value / before, 2)})
    return rows


def markdown(rows: list[dict[str, Any]], result: dict[str, Any], baseline: dict[str, Any],
             ratio: float, slack_ms: float) -> str:
    machine = result.get("machine", {})
    lines = [
        "### Start-up, notebook start and SQL benchmark",
        "",
        f"This run: {result.get('runs')} runs on {machine.get('system')}, Python {machine.get('python')}, "
        f"{machine.get('cpus')} CPUs. Baseline: {baseline.get('runner', 'none recorded')}, "
        f"{baseline.get('recorded', '')} at {str(baseline.get('commit', ''))[:8]}. Medians in milliseconds. "
        f"A regression is more than {ratio}x the baseline and more than {slack_ms:g} ms slower.",
        "",
        "| Measurement | Median | Baseline | Change | Status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        base = "" if r["baseline"] is None else f"{r['baseline']:,}"
        change = "" if r["change"] is None else f"{r['change']}x"
        status = f"**{r['status']}**" if r["status"] == "regression" else r["status"]
        lines.append(f"| {r['name']} | {r['median']:,} | {base} | {change} | {status} |")
    return "\n".join(lines) + "\n"


def baseline_from(result: dict[str, Any], runner: str, commit: str = "") -> dict[str, Any]:
    return {"runner": runner, "recorded": dt.date.today().isoformat(), "commit": commit,
            "machine": result.get("machine", {}), "settings": settings(result), "medians": medians(result)}


def streak(regressed: list[str], previous: dict[str, Any], image: str) -> list[str]:
    """The measurements past the margin in this run that were also past it in the previous run on
    the same image: the ones the gate fails on."""
    if not previous or previous.get("image") != image:
        return []
    before = previous.get("regressed")
    if not isinstance(before, list):
        return []
    return [name for name in regressed if name in set(map(str, before))]


def read_streak(path: Path) -> dict[str, Any]:
    """The previous run's streak file, or {} when it is missing or can't be read. A damaged file is
    treated as no previous run, so this run's result replaces it instead of failing every later run."""
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return previous if isinstance(previous, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result", type=Path, help="a --json file from startup_and_sql.py")
    parser.add_argument("--baseline", type=Path, help="the baseline to compare with")
    parser.add_argument("--baselines", type=Path, help="the folder of baselines, one per runner image")
    parser.add_argument("--image", default="", help="with --baselines: the runner image this ran on")
    parser.add_argument("--streak", type=Path,
                        help="the last run's regressions on this image, read then replaced with this run's; "
                             "--gate then fails only on two runs in a row")
    parser.add_argument("--ratio", type=float, default=1.5, help="slower than this many times the baseline (1.5)")
    parser.add_argument("--slack-ms", type=float, default=25.0, help="and slower by more than this (25 ms)")
    parser.add_argument("--summary", type=Path, help="also append the table to this file")
    parser.add_argument("--gate", action="store_true", help="exit 1 when a measurement regressed")
    parser.add_argument("--write-baseline", type=Path, help="write the run as a new baseline to this file")
    parser.add_argument("--runner", default="", help="with --write-baseline: the machine or runner it ran on")
    parser.add_argument("--commit", default="", help="with --write-baseline: the commit it ran at")
    args = parser.parse_args(argv)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if args.write_baseline:
        if not args.runner:
            parser.error("--write-baseline needs --runner, so the baseline says where it was recorded")
        record = baseline_from(result, args.runner, args.commit)
        args.write_baseline.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote the baseline to {args.write_baseline}")
        return 0
    if args.baselines:
        if not args.image:
            parser.error("--baselines needs --image, the runner image the run was on")
        args.baseline = args.baselines / f"{args.image}.json"
    if not args.baseline:
        parser.error("give --baseline (or --baselines and --image) to compare with, or --write-baseline to record one")
    if args.ratio <= 1 or args.slack_ms < 0:
        parser.error("--ratio takes a number above 1, --slack-ms one of 0 or more")
    # No baseline recorded yet: every line says so, and nothing can regress.
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else {}
    rows = compare(result, baseline, args.ratio, args.slack_ms)
    text = markdown(rows, result, baseline, args.ratio, args.slack_ms)
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(text)
    regressions = [r for r in rows if r["status"] == "regression"]
    failing = [r["name"] for r in regressions]
    if args.streak:
        previous = read_streak(args.streak)
        failing = streak(failing, previous, args.image)
        args.streak.write_text(json.dumps({"image": args.image, "commit": os.environ.get("GITHUB_SHA", ""),
                                           "regressed": [r["name"] for r in regressions]}) + "\n", encoding="utf-8")
        note = ("\n".join(f"- {r['name']}: " + ("past the margin in the last run too, so this fails the check"
                                                if r["name"] in failing else
                                                "first run past the margin; the check fails if the next run is too")
                          for r in regressions) + "\n") if regressions else ""
        print(note, end="")
        if args.summary and note:
            with args.summary.open("a", encoding="utf-8") as out:
                out.write("\n" + note)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for r in regressions:
            kind = "error" if args.gate and r["name"] in failing else "warning"
            print(f"::{kind} title=Performance regression::{r['name']}: {r['median']:,} ms against a baseline "
                  f"of {r['baseline']:,} ms ({r['change']}x)")
    return 1 if args.gate and failing else 0


if __name__ == "__main__":
    sys.exit(main())
