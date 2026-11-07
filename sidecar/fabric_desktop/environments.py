"""Notebook environments: extra Python packages for a notebook, like a Fabric Environment item or
Databricks notebook-scoped libraries (Preview; notebooks in their own process only).

An environment is a folder on this PC (``<data>/environments/<name>/site-packages``) that pip
installs into. A notebook attached to one gets the folder on its process's ``sys.path`` after the
engine's own packages, so a package can add to the engine but never replace what it ships
(polars, duckdb, deltalake...). Installs are resolved against the engine's exact versions
(a constraints file pip gets), so pip refuses a package that needs a different numpy or pyarrow
instead of installing one that would be shadowed and break at import.

Which notebook uses which environment is kept in ``environments.json``. Several notebooks can
share one, as with a Fabric Environment. Installing downloads from PyPI, or from the index the
PC's pip is set up for (pip.ini, PIP_INDEX_URL), with nothing from the person's sign-in.
Organizations can turn installs off (``allow_notebook_packages`` in the policy file).

Where packages come from (Preview): with ``notebook_package_folder`` set in the policy file, pip
installs from that folder of approved wheels first and reaches no index at all. Only when a package
isn't there, and ``allow_pypi_packages`` isn't false, does it try again with PyPI (the folder still
offered, and every package it has pinned to the folder's version, so PyPI only adds what's missing). A folder that's set but can't be reached stops the install; it never falls back to PyPI.
"""

from __future__ import annotations

import builtins
import importlib.metadata
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (InvalidSdistFilename, InvalidWheelFilename, canonicalize_name, parse_sdist_filename,
                             parse_wheel_filename)
from packaging.version import Version

from . import app_control, jsonfile, net_trust

INSTALL_SECONDS = 15 * 60
# Spark itself: notebooks' spark is the engine's local one (spark_local), and these bring a second
# pyspark that would fight it. databricks-connect also needs a process of its own (ADR-004).
REFUSED = {"pyspark": "spark here is the app's own local Spark API",
           "databricks-connect": "Databricks Connect runs in a process of its own, not in a notebook environment",
           "delta-spark": "Delta tables are read and written through the app's own Delta support"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
# Windows device names: a folder can't be called these, with or without an extension.
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9]) *(\..*)?$", re.IGNORECASE)


def check_name(name: str) -> str:
    name = str(name or "").strip()
    if not _NAME.match(name) or name.endswith((".", " ")):
        raise ValueError("An environment name is letters, numbers, spaces, dots, dashes and underscores (64 at most).")
    if _RESERVED.match(name.strip()):
        raise ValueError(f"{name} is a name Windows keeps for a device. Choose another name.")
    return name


def check_packages(specs: list[str]) -> list[str]:
    """Package requirements only (``polars-ds``, ``scikit-learn==1.5.2``, ``great_expectations[spark]>=1``).
    Options, URLs and paths are refused: they could point pip anywhere."""
    out = []
    for spec in specs or []:
        spec = str(spec).strip()
        if not spec:
            continue
        if (spec.startswith("-") or "://" in spec or "@" in spec or "/" in spec or "\\" in spec
                or re.search(r"\.(whl|zip|tgz|tar|tar\.(gz|bz2|xz))$", spec, re.I)):  # pip reads a local file
            raise ValueError(f"{spec!r} isn't a package name. Give names and versions, like scikit-learn==1.5.2; "
                             "pip options, URLs and paths aren't accepted.")
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            raise ValueError(f"{spec!r} isn't a package name and version pip understands.") from None
        why = REFUSED.get(re.sub(r"[-_.]+", "-", req.name).lower())
        if why:
            raise ValueError(f"{req.name} can't be added to a notebook environment: {why}.")
        out.append(str(req))
    if not out:
        raise ValueError("Name at least one package.")
    return out


# What pip says when no source it was given has a package (or a version of it that fits).
_NOT_FOUND = ("No matching distribution found", "Could not find a version that satisfies")
# pip's own default is 5 retries of 15 seconds each per address: a PC whose firewall drops PyPI's
# packets waited minutes before failing. Two still ride out a brief network blip.
PIP_RETRIES = 2
PIP_OFFLINE_FIX = ("If your network blocks PyPI, ask IT to allow pypi.org and files.pythonhosted.org, or to "
                   "set up a package folder (notebook_package_folder in the policy file).")


def folder_pins(folder: Path, engine: list[str]) -> list[str]:
    """``name==version`` for every package the approved folder has (its highest version),
    so the PyPI try can't take a different version of it: pip picks the highest version across the
    folder and the index, so without these a public package of the same name (a squatted internal
    name, or simply a newer release) would replace the approved wheel. Packages the engine pins are
    left to the engine's constraint, which already fixes their version."""
    pinned = {canonicalize_name(p.split("==")[0]) for p in engine}
    best: dict[str, Version] = {}
    for item in folder.iterdir():
        try:  # wheels, and the source archives --find-links also reads
            if item.name.endswith(".whl"):
                name, version, _, _ = parse_wheel_filename(item.name)
            elif item.name.endswith((".tar.gz", ".zip")):
                name, version = parse_sdist_filename(item.name)
            else:
                continue
        except (InvalidWheelFilename, InvalidSdistFilename):
            continue
        if name not in pinned and (name not in best or version > best[name]):
            best[name] = version
    return [f"{name}=={version}" for name, version in sorted(best.items())]


def install_commands(python: str, target: Path, pins: Path, specs: list[str],
                     folder: str = "", pypi: bool = True, approved: Path | None = None) -> list[list[str]]:
    """The pip commands an install tries, in order: the approved folder alone, then (if allowed)
    the index with the folder still offered and ``approved`` (folder_pins) as constraints, so a
    package the folder has can only be the folder's version. With no folder, the index alone."""
    base = [python, "-m", "pip", "install", "--target", str(target), "--upgrade", "--no-input",
            "--disable-pip-version-check", "--retries", str(PIP_RETRIES), "-c", str(pins)]
    if not folder:
        return [base + specs]
    first = base + ["--no-index", "--find-links", folder, *specs]
    if not pypi:
        return [first]
    if approved is None:
        raise ValueError("The PyPI try needs the folder's versions as constraints (folder_pins).")
    return [first, base + ["-c", str(approved), "--find-links", folder, *specs]]


def engine_pins() -> list[str]:
    """The engine's own packages at their exact versions, for pip's constraints."""
    pins = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            pins[name.lower()] = f"{name}=={dist.version}"
    return sorted(pins.values())


class Environments:
    def __init__(self, root: Path, python: str | None = None):
        self.folder = root / "environments"
        self.index = root / "environments.json"
        self.python = python or sys.executable
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}

    # ---- the index

    def _read(self) -> dict[str, Any]:
        data = jsonfile.read_json(self.index, {})
        return {"environments": dict(data.get("environments") or {}), "notebooks": dict(data.get("notebooks") or {})}

    def _write(self, data: dict[str, Any]) -> None:
        jsonfile.write_json(self.index, data)

    def site_packages(self, name: str) -> Path:
        return self.folder / check_name(name) / "site-packages"

    def of(self, notebook: str) -> str | None:
        return self._read()["notebooks"].get(notebook)

    def paths_for(self, notebook: str) -> list[str]:
        env = self.of(notebook)
        return [str(self.site_packages(env))] if env else []

    def list(self) -> list[dict[str, Any]]:
        data = self._read()
        out = []
        for name, info in sorted(data["environments"].items(), key=lambda kv: kv[0].lower()):
            out.append({"name": name, "packages": info.get("packages", []), "installed": self.installed(name),
                        "notebooks": sorted(nb for nb, env in data["notebooks"].items() if env == name),
                        "updated": info.get("updated")})
        return out

    def installed(self, name: str) -> builtins.list[dict[str, str]]:
        """What is in the environment's folder that the engine doesn't already have."""
        folder = self.site_packages(name)
        if not folder.is_dir():
            return []
        engine = {p.split("==")[0].lower() for p in engine_pins()}
        found = []
        for dist in importlib.metadata.distributions(path=[str(folder)]):
            n = dist.metadata.get("Name") or ""
            if n and n.lower() not in engine:
                found.append({"name": n, "version": dist.version})
        return sorted(found, key=lambda d: d["name"].lower())

    # ---- changes

    def create(self, name: str) -> dict[str, Any]:
        name = check_name(name)
        with self._lock:
            data = self._read()
            if any(n.lower() == name.lower() for n in data["environments"]):
                raise ValueError(f"There's already an environment called {name}.")
            # The folder first: an environment is never listed without somewhere to install into.
            self.site_packages(name).mkdir(parents=True, exist_ok=True)
            data["environments"][name] = {"packages": [], "updated": time.time()}
            self._write(data)
        return {"name": name}

    def delete(self, name: str) -> dict[str, Any]:
        name = check_name(name)
        with self._lock:
            data = self._read()
            if name not in data["environments"]:
                raise ValueError(f"There's no environment called {name}.")
            if self._busy(name):
                raise RuntimeError(f"{name} is installing packages. Wait for it to finish.")
            detached = sorted(nb for nb, env in data["notebooks"].items() if env == name)
            # Moved aside first: on Windows that fails while a notebook's process has its files open,
            # so nothing is half deleted and the environment stays as it was.
            gone = self.folder / f".deleting-{uuid.uuid4().hex[:8]}"
            try:
                if (self.folder / name).exists():
                    (self.folder / name).rename(gone)
            except OSError:
                raise RuntimeError(f"A running notebook is using {name}'s packages. Stop its session (Sessions in "
                                   "the notebook status bar), then delete the environment.") from None
            data["environments"].pop(name)
            data["notebooks"] = {nb: env for nb, env in data["notebooks"].items() if env != name}
            self._write(data)
        # This one, and any an earlier delete couldn't finish (a file Windows still had open).
        for leftover in self.folder.glob(".deleting-*"):
            shutil.rmtree(leftover, ignore_errors=True)
        return {"deleted": name, "detached": detached}

    def attach(self, notebook: str, name: str | None) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            if name:
                name = check_name(name)
                if name not in data["environments"]:
                    raise ValueError(f"There's no environment called {name}.")
                data["notebooks"][notebook] = name
            else:
                data["notebooks"].pop(notebook, None)
            self._write(data)
        return {"notebook": notebook, "environment": name or None}

    def rename_notebook(self, old: str, new: str) -> None:
        with self._lock:
            data = self._read()
            if old in data["notebooks"]:
                data["notebooks"][new] = data["notebooks"].pop(old)
                self._write(data)

    def _busy(self, name: str) -> bool:
        return any(j["environment"] == name and j["state"] == "running" for j in self.jobs.values())

    def start(self, name: str, specs: builtins.list[str], remove: bool = False, folder: str = "",
              pypi: bool = True) -> dict[str, Any]:
        """Install (or, with ``remove``, uninstall) packages in the background; ``job`` follows it.
        ``folder`` and ``pypi`` are the policy's package source (install_commands)."""
        name = check_name(name)
        specs = check_packages(specs)
        with self._lock:  # with the check, so a delete can't land between them and the install bring it back
            if name not in self._read()["environments"]:
                raise ValueError(f"There's no environment called {name}.")
            if self._busy(name):
                raise RuntimeError(f"{name} is already installing packages. Wait for it to finish.")
            jid = uuid.uuid4().hex[:12]
            job = self.jobs[jid] = {"id": jid, "environment": name, "packages": specs, "remove": remove,
                                    "state": "running", "log": "", "started": time.time(), "finished": None,
                                    "folder": folder, "pypi": pypi}
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"env-{jid}").start()
        return dict(job)

    def job(self, jid: str) -> dict[str, Any]:
        if jid not in self.jobs:
            raise ValueError("That install isn't known. It may be from before the app restarted.")
        return dict(self.jobs[jid])

    def _run(self, job: dict[str, Any]) -> None:
        name, specs = job["environment"], job["packages"]
        target = self.site_packages(name)
        target.mkdir(parents=True, exist_ok=True)
        try:
            if job["remove"]:
                removed = [s for s in specs if self._remove(target, Requirement(s).name)]
                job["log"] = "Removed " + ", ".join(removed) if removed else "None of those were installed."
                ok = True
            else:
                pins = target.parent / "engine-constraints.txt"
                pins.write_text("\n".join(engine_pins()) + "\n", encoding="utf-8")
                ok = self._install(job, target, pins, specs)
            if ok:
                with self._lock:
                    data = self._read()
                    info = data["environments"].setdefault(name, {"packages": []})
                    names = {Requirement(s).name.lower() for s in specs}
                    kept = [p for p in info.get("packages", []) if Requirement(p).name.lower() not in names]
                    info["packages"] = kept if job["remove"] else kept + specs
                    info["updated"] = time.time()
                    self._write(data)
            job["state"] = "done" if ok else "failed"
        except subprocess.TimeoutExpired:
            job["state"], job["log"] = "failed", f"pip didn't finish within {INSTALL_SECONDS // 60} minutes, so it was stopped."
        except Exception as exc:  # noqa: BLE001 - the job reports why it failed
            job["state"], job["log"] = "failed", f"{type(exc).__name__}: {exc}"
        job["finished"] = time.time()

    def _install(self, job: dict[str, Any], target: Path, pins: Path, specs: builtins.list[str]) -> bool:
        folder, pypi = job.get("folder") or "", job.get("pypi", True)
        if not folder and not pypi:  # the service refuses this first; never reach an index regardless
            job["log"] = "Your organization doesn't allow PyPI and names no package folder, so nothing was installed."
            return False
        if folder and not Path(folder).is_dir():
            job["log"] = (f"Your organization's package folder ({folder}) can't be reached from this PC, so nothing "
                          "was installed. Check you're on the network, or ask IT.")
            return False
        approved = None
        if folder and pypi:
            approved = target.parent / "folder-constraints.txt"
            approved.write_text("\n".join(folder_pins(Path(folder), engine_pins())) + "\n", encoding="utf-8")
        commands = install_commands(self.python, target, pins, specs, folder, pypi, approved)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log, missing = [], False
        for i, cmd in enumerate(commands):
            source = ("your organization's package folder" if "--no-index" in cmd
                      else "PyPI, after your organization's package folder" if folder else "PyPI")
            done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=INSTALL_SECONDS,
                                  creationflags=flags, check=False)
            log.append(f"From {source}:\n" + done.stdout + done.stderr)
            if done.returncode == 0:
                if line := app_control.blocked_line(app_control.blocked_packages(target)):
                    log.append(line)
                job["log"] = "\n".join(log)[-20000:]
                return True
            missing = any(text in done.stdout + done.stderr for text in _NOT_FOUND)
            if not missing or i == len(commands) - 1:
                break
        if folder and not pypi and missing:
            log.append("A package isn't in your organization's package folder, and your organization doesn't allow "
                       "installing from PyPI. Ask IT to add it to the folder.")
        elif pypi and (offline := net_trust.pip_unreachable(log[-1], PIP_OFFLINE_FIX)):
            log.append(offline)
        job["log"] = "\n".join(log)[-20000:]
        return False

    @staticmethod
    def _remove(target: Path, package: str) -> bool:
        """Remove one package's files from the folder, by its RECORD (pip --target has no uninstall)."""
        wanted = re.sub(r"[-_.]+", "-", package).lower()
        for dist in importlib.metadata.distributions(path=[str(target)]):
            if re.sub(r"[-_.]+", "-", dist.metadata.get("Name") or "").lower() != wanted:
                continue
            root = target.resolve()
            folders: set[Path] = set()
            for f in dist.files or []:
                p = (target / f).resolve()
                if p.is_relative_to(root) and p.is_file():
                    p.unlink()
                    folders.update(q for q in p.parents if q != root and q.is_relative_to(root))
            info = Path(str(dist._path))  # type: ignore[attr-defined]  # the .dist-info folder
            shutil.rmtree(info, ignore_errors=True)
            # The package's folders, once empty: a folder left behind still imports, as an empty
            # namespace package, so the package would seem installed with nothing in it. Their
            # __pycache__ goes too (only caches; Python writes ones the RECORD doesn't list).
            for folder in sorted(folders, key=lambda q: len(q.parts), reverse=True):
                shutil.rmtree(folder / "__pycache__", ignore_errors=True)
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            return True
        return False
