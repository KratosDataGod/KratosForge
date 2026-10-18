"""The exact project files a release put in an environment, kept in that environment's LH_Meta.

Promote copies every file that makes up the metadata version (configs, transforms, tests, the
same set ``Project.version`` hashes) into ``meta.release_files`` of the environment it promotes
to. Test and Prod then run from that copy, not from whatever the runner's own folder holds, and
rolling an environment back points it at an earlier release's copy. ``environments.toml`` is
deployment settings, not part of a release, so a run always takes the current one.

A restored release is written once to ``.local/releases/<env>/<version>/`` beside the project's
own folder, and is used again while its files still hash to the version.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

import polars as pl

from ..redact import describe
from .project import _IGNORED, _UNVERSIONED, Project
from .store import Store

_log = logging.getLogger(__name__)


def files(project: Project) -> dict[str, bytes]:
    """Every file in the metadata version, by project-relative path (the set ``Project.version`` reads)."""
    out = {}
    for p in sorted(project.root.rglob("*")):
        rel = p.relative_to(project.root)
        if p.is_file() and not (set(rel.parts) & _IGNORED) and p.suffix != ".pyc" \
                and rel.as_posix() not in _UNVERSIONED:
            out[rel.as_posix()] = p.read_bytes().replace(b"\r\n", b"\n")
    return out


def versions(store: Store) -> set[str]:
    """The releases whose files this environment holds."""
    df = store.meta("release_files")
    return set(df["version"].unique().to_list()) if df.height else set()


def save(store: Store, project: Project) -> int:
    """Keep this version's files in ``store``'s environment, once. Returns how many were written."""
    version = project.version
    if version in versions(store):
        return 0
    rows = [{"version": version, "path": rel, "sha256": hashlib.sha256(data).hexdigest(),
             "content": base64.b64encode(data).decode("ascii")} for rel, data in files(project).items()]
    store.meta_append("release_files", rows)
    return len(rows)


def restore(project: Project, store: Store, version: str) -> Project:
    """The project as release ``version`` had it in ``store``'s environment, ready to run.

    Raises LookupError when the environment holds no files for that release (it was promoted
    before releases kept them) and ValueError when the files don't add up to the version."""
    dest = project.local_data / "releases" / store.env / version
    if dest.is_dir() and _matches(project, dest, version):
        return Project(dest, local_data=project.local_data)
    df = store.meta("release_files").filter(pl.col("version") == version)
    if not df.height:
        raise LookupError(f"{store.env.title()} doesn't keep the files of release {version}; "
                          "it was promoted before releases kept them.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=dest.parent))
    try:
        for r in df.unique("path", keep="last").iter_rows(named=True):
            data = base64.b64decode(r["content"])
            if hashlib.sha256(data).hexdigest() != r["sha256"]:
                raise ValueError(f"{r['path']} in release {version} doesn't match its recorded hash")
            target = work / r["path"]
            if not target.resolve().is_relative_to(work.resolve()):
                raise ValueError(f"{r['path']} in release {version} points outside the project")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if not _matches(project, work, version):
            raise ValueError(f"The files {store.env.title()} keeps for release {version} don't add up to it")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(work, dest)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return Project(dest, local_data=project.local_data)


def _matches(project: Project, folder: Path, version: str) -> bool:
    """Whether ``folder`` holds release ``version``, with the project's current environments.toml."""
    env_file = project.root / "environments.toml"
    if env_file.exists():
        shutil.copyfile(env_file, folder / "environments.toml")
    try:
        return Project(folder, local_data=project.local_data).version == version
    except Exception as exc:  # noqa: BLE001 - a folder that doesn't load isn't the release
        _log.debug("The folder doesn't load as a release: %s", describe(exc))
        return False
