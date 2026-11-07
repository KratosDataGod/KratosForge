"""Temporary pytest plugin: time where Academy exercise runs spend their time on Windows.
Loaded with -p academy_profile (PYTHONPATH=.github/diag). Not for merge."""
from __future__ import annotations

import collections
import functools
import json
import os
import time

STATS: dict[str, list[float]] = collections.defaultdict(list)
CUR = {"test": ""}


def _wrap(mod, name, label=None):
    orig = getattr(mod, name)

    @functools.wraps(orig)
    def inner(*a, **k):
        t = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            key = label(a, k) if label else name
            STATS[f"{mod.__name__.rsplit('.', 1)[-1]}.{key}"].append(time.perf_counter() - t)
    setattr(mod, name, inner)


def _icacls_label(a, k):
    flags = [x for x in a if x.startswith("/")]
    return "_icacls " + (flags[0] if flags else "(read)")


def pytest_configure(config):
    from fabric_desktop.academy import appcontainer as ac
    _wrap(ac, "_icacls", _icacls_label)
    for n in ("grant_runtime", "runtime_paths", "check_sealed_folders_closed", "check_grants_keep_out",
              "deny_app_packages", "sweep", "copy_tree", "mirror_back", "app_package_aces", "sealed"):
        if hasattr(ac, n):
            _wrap(ac, n)
    _wrap(ac.Profile, "__init__", lambda a, k: "Profile.create")
    _wrap(ac.Profile, "close", lambda a, k: "Profile.close")
    _wrap(ac.LowboxProcess, "__init__", lambda a, k: "LowboxProcess.start")
    _wrap(ac.LowboxProcess, "wait", lambda a, k: "LowboxProcess.wait")
    try:
        from fabric_desktop.academy import sandbox
        for n in dir(sandbox):
            if n in ("run", "run_learner", "_run", "start"):
                _wrap(sandbox, n)
    except Exception:
        pass
    import subprocess

    def _sub(a, k):
        args = a[0] if a else k.get("args")
        first = args if isinstance(args, (str, bytes)) or not args else args[0]
        return "subprocess.run " + os.path.basename(str(first))[:40]
    _wrap(subprocess, "run", _sub)


def pytest_sessionfinish(session):
    rows = sorted(((sum(v), len(v), k) for k, v in STATS.items()), reverse=True)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    out = os.path.join(os.environ.get("RUNNER_TEMP", "."), f"acprof-{worker}.json")
    with open(out, "w") as f:
        json.dump(rows, f)
    print(f"\n[academy profile {worker}]")
    for total, n, k in rows[:30]:
        print(f"{total:9.1f}s {n:6d}x {total / n * 1000:9.1f} ms each  {k}")
