"""Gate 3: promote one environment's metadata to the next (Dev to Test, Test to Prod)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from . import release as release_mod
from . import snapshot
from .project import GateError, Project, validate
from .store import Store


def promote_checks(project: Project, source: Store, target_env: str, today: dt.date | None = None) -> list[str]:
    """Every reason the project can't be promoted from ``source`` yet."""
    errors = validate(project, today)
    version = project.version
    if source.env != "dev":
        deps = source.meta("deployments").filter(pl.col("env") == source.env)
        if not deps.height or deps["version"][-1] != version:
            errors.append(f"{source.env} is not on metadata {version}; promote it to {source.env} first")
    log = source.meta("run_log")
    for name in project.entities:
        runs = log.filter(pl.col("entity") == name)
        if not runs.height:
            errors.append(f"{name}: has never run in {source.env}")
            continue
        last = runs.row(-1, named=True)
        if last["logic"] != project.fingerprint(project.entities[name]):
            errors.append(f"{name}: changed since its last {source.env} run; run it again")
        elif last["status"] != "ok":
            errors.append(f"{name}: its last {source.env} run failed")
    if target_env == source.env:
        errors.append("pick a different environment to promote to")
    return errors


def promote(project: Project, source: Store, target: Store, today: dt.date | None = None,
            release: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy the metadata to ``target`` and record the deployment, with ``release`` (a release
    receipt from meta/release.py) when there is one."""
    errors = promote_checks(project, source, target.env, today)
    if errors:
        raise GateError("promote", errors)
    version = project.version
    target.meta_append("entities", [{"version": version, "name": e.name,
                                     "config": e.path.read_text(encoding="utf-8")}
                                    for e in project.entities.values()])
    snapshot.save(target, project)  # before the deployment row, so no release lacks its files
    row = {"env": target.env, "version": version, "source_env": source.env,
           "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")}
    if release is not None:
        row |= release_mod.deployment_columns(release)
    target.meta_append("deployments", [row])
    out = {"env": target.env, "version": version, "entities": len(project.entities)}
    if release is not None:
        out["release"] = {k: row[k] for k in ("commit", "branch", "dirty", "pushed", "receipt_id")}
    return out


def releases(target: Store) -> list[dict[str, Any]]:
    """Every release promoted or rolled back to ``target``'s environment, newest first, each saying
    whether its files are kept here (so it can be rolled back to)."""
    deps = target.meta("deployments")
    kept = snapshot.versions(target)
    rows = [r for r in deps.iter_rows(named=True) if r.get("env") == target.env] if deps.height else []
    out = []
    for i, r in enumerate(reversed(rows)):
        out.append({k: r.get(k) for k in ("version", "source_env", "at", "commit", "branch", "dirty", "pushed",
                                          "by", "receipt_id")}
                   | {"current": i == 0, "kept": r.get("version") in kept,
                      "verified": release_mod.verified(r.get("receipt"), r.get("receipt_id"))})
    return out


def rollback(project: Project, target: Store, version: str, by: str | None = None,
             object_id: str | None = None) -> dict[str, Any]:
    """Put ``target``'s environment back on an earlier release it ran, from the files it keeps.

    Nothing is copied from Dev, Git or the project folder: the environment's own copy of that
    release becomes what it runs, and the rollback is recorded as a new release naming the one
    it replaced. Tables keep their rows until their next run."""
    history = releases(target)
    if not history:
        raise GateError("rollback", [f"Nothing has been promoted to {target.env} yet."])
    current = history[0]
    errors = []
    if current["version"] == version:
        errors.append(f"{target.env.title()} is already on {version}.")
    earlier = next((r for r in history if r["version"] == version), None)
    if earlier is None:
        errors.append(f"{version} was never released to {target.env.title()}.")
    elif not earlier["kept"]:
        errors.append(f"{target.env.title()} doesn't keep the files of {version}; it was promoted before "
                      "releases kept them, so promote it again instead.")
    if errors:
        raise GateError("rollback", errors)
    released = snapshot.restore(project, target, version)  # proves the files add up before anything is written
    body = release_mod.rollback_record(released, target.env, version, current, earlier, by, object_id)
    row = {"env": target.env, "version": version, "source_env": target.env,
           "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")} | release_mod.deployment_columns(body)
    target.meta_append("deployments", [row])
    return {"env": target.env, "version": version, "replaced": current["version"],
            "receipt_id": row["receipt_id"], "project": released}
