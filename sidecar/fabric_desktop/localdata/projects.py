"""Local data projects: a DuckDB database on this PC, the folders it reads, and its settings (FD-LDE-001).

Each project is a folder (under ``<data>/local-data/`` unless the person picks another one):

    project.json   name, storage, settings, registered folders, column type overrides,
                   and the catalog of discovered tables from the last refresh
    data.duckdb    the database: tables people create, and views over discovered files
    tmp/           DuckDB spills here when a query needs more memory than its limit
    Tables/        the project's Delta tables, <schema>/<table>, as a lakehouse lays them out (delta.py)

``storage = "memory"`` keeps nothing between sessions except project.json: the views are made again
when it opens, and any tables are gone. Nothing here needs a Fabric sign-in or capacity.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .discovery import Source
from .. import jsonfile

SCHEMA = "local-project@1"
STORAGE = ("persistent", "memory")
_SLUG_BAD = re.compile(r"[^a-z0-9]+")
_MEMORY = re.compile(r"^\s*\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB|%)\s*$", re.IGNORECASE)


@dataclass
class Settings:
    memory_limit: str = ""        # DuckDB's memory_limit ("4GB", "60%"); empty: DuckDB's default (80% of RAM)
    threads: int = 0              # 0: one per core
    timeout_seconds: int = 0      # 0: no time limit; a query that runs longer is cancelled
    max_temp_size: str = ""       # how much DuckDB may spill to tmp/; empty: DuckDB's default
    auto_refresh: bool = True     # look for changed files (cheaply) before each query

    def __post_init__(self):
        for key in ("memory_limit", "max_temp_size"):
            value = getattr(self, key)
            if value and not _MEMORY.match(value):
                raise ValueError(f"{key} must look like 4GB, 512MB or 60%, got {value!r}")
        if not 0 <= int(self.threads) <= 256:
            raise ValueError("threads must be between 0 and 256")
        if not 0 <= int(self.timeout_seconds) <= 7 * 24 * 3600:
            raise ValueError("timeout_seconds must be between 0 and a week")


@dataclass
class Project:
    id: str
    name: str
    folder: str
    storage: str = "persistent"
    settings: Settings = field(default_factory=Settings)
    sources: list[Source] = field(default_factory=list)
    # "schema.table" (lower case) -> {"column": "DuckDB type"}
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    # Discovered tables after the last refresh: "schema.table" -> details (engine.py writes it).
    catalog: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Source path -> {"fingerprint", "refreshed_at", "issues"}
    refreshed: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = ""

    @property
    def root(self) -> Path:
        return Path(self.folder)

    @property
    def database(self) -> str:
        return ":memory:" if self.storage == "memory" else str(self.root / "data.duckdb")

    @property
    def temp_dir(self) -> Path:
        return self.root / "tmp"

    def source(self, path: str) -> Source | None:
        key = _norm(path)
        return next((s for s in self.sources if _norm(s.path) == key), None)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "folder": self.folder, "storage": self.storage,
                "settings": asdict(self.settings), "sources": [asdict(s) for s in self.sources],
                "tables": len(self.catalog), "created_at": self.created_at}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


class ProjectStore:
    """Every local data project on this PC. The list is the project folders' project.json files."""

    def __init__(self, root: Path):
        self.root = Path(root)  # <data>/local-data
        self.index_file = self.root / "projects.json"  # id -> folder, for projects kept elsewhere

    def _index(self) -> dict[str, str]:
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_index(self, index: dict[str, str]) -> None:
        _write_json(self.index_file, index)

    def list(self) -> list[Project]:
        projects = []
        for pid, folder in sorted(self._index().items()):
            try:
                projects.append(self.load(pid))
            except (OSError, ValueError, KeyError):
                continue  # a project folder that was deleted or moved: listed again once it's back
        return projects

    def load(self, pid: str) -> Project:
        folder = self._index().get(pid)
        if folder is None:
            raise KeyError(f"No local data project {pid!r}.")
        raw = json.loads((Path(folder) / "project.json").read_text(encoding="utf-8"))
        return Project(id=raw["id"], name=raw["name"], folder=folder, storage=raw.get("storage", "persistent"),
                       settings=Settings(**raw.get("settings", {})),
                       sources=[Source(**s) for s in raw.get("sources", [])],
                       overrides=raw.get("overrides", {}), catalog=raw.get("catalog", {}),
                       refreshed=raw.get("refreshed", {}), created_at=raw.get("created_at", ""))

    def save(self, project: Project) -> None:
        data = asdict(project)
        data.pop("folder")
        _write_json(project.root / "project.json", {"schema": SCHEMA, **data})

    def create(self, name: str, storage: str = "persistent", folder: str = "",
               settings: dict[str, Any] | None = None) -> Project:
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise ValueError("Give the project a name (up to 100 characters).")
        if storage not in STORAGE:
            raise ValueError(f"storage must be one of {STORAGE}, got {storage!r}")
        index = self._index()
        if any(p.name.lower() == name.lower() for p in self.list()):
            raise ValueError(f"There is already a local data project called {name!r}.")
        base = _SLUG_BAD.sub("-", name.lower()).strip("-") or "project"
        pid, n = base, 2
        while pid in index:
            pid, n = f"{base}-{n}", n + 1
        target = Path(folder).expanduser() if folder else self.root / pid
        if (target / "project.json").exists():
            raise ValueError(f"{target} already holds a local data project. Open it instead.")
        target.mkdir(parents=True, exist_ok=True)
        project = Project(id=pid, name=name, folder=str(target.resolve()), storage=storage,
                          settings=Settings(**(settings or {})),
                          created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.save(project)
        index[pid] = project.folder
        self._save_index(index)
        return project

    def forget(self, pid: str) -> None:
        """Remove the project from the list. Its folder and database stay where they are."""
        index = self._index()
        index.pop(pid, None)
        self._save_index(index)


def _write_json(path: Path, data: Any) -> None:
    """Write through a temporary file, so a crash never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        jsonfile.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
