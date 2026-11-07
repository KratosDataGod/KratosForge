"""Fail when a workflow file under .github/workflows can't be read as a GitHub Actions workflow.

GitHub only reports a broken workflow file after it has merged (the docs site stopped deploying
when one did). This reads every file as YAML and checks the shape Actions needs: a trigger
(``on``), and jobs that each name a runner or a reusable workflow, with steps that each ``run``
or ``uses`` something. It doesn't check expressions or contexts.

Usage: python .github/scripts/check_workflows.py [folder]   (default: .github/workflows)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


def problems(path: Path) -> list[str]:
    try:
        doc: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path.name}: not valid YAML: {' '.join(str(exc).split())}"]
    if not isinstance(doc, dict):
        return [f"{path.name}: not a mapping"]
    out = []
    # YAML 1.1 reads a bare `on` key as true.
    if "on" not in doc and True not in doc:
        out.append(f"{path.name}: no `on` trigger")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [*out, f"{path.name}: no jobs"]
    for name, job in jobs.items():
        where = f"{path.name}: job {name}"
        if not isinstance(job, dict):
            out.append(f"{where} is not a mapping")
            continue
        if "uses" in job:   # a reusable workflow
            continue
        if "runs-on" not in job:
            out.append(f"{where} has no runs-on")
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            out.append(f"{where} has no steps")
            continue
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or ("run" in step) == ("uses" in step):
                out.append(f"{where}, step {i}: needs exactly one of run or uses")
    return out


def main(argv: list[str]) -> int:
    folder = Path(argv[1]) if len(argv) > 1 else Path(".github/workflows")
    files = sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])
    if not files:
        print(f"No workflow files in {folder}.")
        return 1
    found = [p for f in files for p in problems(f)]
    for p in found:
        print(f"::error::{p}")
    print(f"{len(files)} workflow files read, {len(found)} problems.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
