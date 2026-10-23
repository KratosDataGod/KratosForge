"""Find the data files in a folder and say which SQL table each one becomes (FD-LDE-002).

Discovery only looks at the file system; it opens no file. The engine (engine.py) then turns each
table into a view and asks DuckDB for its columns, so a file DuckDB can't read is reported there,
one table at a time, without failing the others.

How files become tables, the way DaffyDB names them:

    orders.parquet                     dbo.orders        (the source's default schema)
    Sales.Customer.csv                 Sales.Customer    (a dotted file name sets the schema)
    Sales/Customer.csv                 Sales.Customer    (a folder sets the schema)
    inventory/part-00000.parquet ...   dbo.inventory     (a folder of part files is one table)
    events/year=2026/month=10/*.parquet  dbo.events      (Hive partitions are one table)
    sales_delta/_delta_log/...         dbo.sales_delta   (a Delta table, read at its current version)

With ``naming = "flat"`` folders don't set the schema: ``Sales/Customer.csv`` is
``dbo.Sales_Customer``. When two files give the same name, Parquet wins over CSV and CSV over JSON
(as in DaffyDB), and the other file is reported as a conflict rather than silently dropped.
Excel workbooks are found and reported; reading them needs a package the engine doesn't ship yet.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Suffix (lower case) -> format. Compressed CSV and JSON are read by DuckDB directly.
FORMATS = {
    ".parquet": "parquet", ".parq": "parquet",
    ".csv": "csv", ".tsv": "csv", ".csv.gz": "csv", ".tsv.gz": "csv", ".csv.zst": "csv",
    ".json": "json", ".json.gz": "json", ".json.zst": "json",
    ".jsonl": "json", ".ndjson": "json", ".jsonl.gz": "json", ".ndjson.gz": "json",
    ".xlsx": "excel", ".xlsm": "excel",
}
# When two files map to one table name: lower wins.
PRECEDENCE = {"delta": 0, "parquet": 1, "csv": 2, "json": 3, "excel": 4}

# Folders that hold tools, builds and caches, never data.
EXCLUDED_DIRS = {"node_modules", "bin", "obj", "__pycache__", "venv", "env", "site-packages", "dist", "build",
                 "target", "$recycle.bin", "system volume information"}
# Configuration files that happen to be JSON.
EXCLUDED_FILES = {"package.json", "package-lock.json", "tsconfig.json", "appsettings.json", "launchsettings.json",
                  "composer.json", "manifest.json", "settings.json", "project.json", "desktop.ini", "thumbs.db"}

_PART = re.compile(r"^(part|data|chunk|file)[-_.]?\d+|^\d+$|^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)
_HIVE = re.compile(r"^[^=]+=.*$")

NAMING_RULES = ("folders", "flat")


@dataclass
class Source:
    """A folder registered as a local data source."""

    path: str
    schema: str = "dbo"            # the schema for files that don't name one
    naming: str = "folders"        # "folders": a top-level folder is a schema; "flat": it isn't
    max_depth: int = 4             # folders below the source that are searched
    exclude: list[str] = field(default_factory=list)  # extra folder or file names (glob) to skip
    # A file or table folder's path below the source ("sales/2026.csv") -> "schema.table" to call it.
    names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.naming not in NAMING_RULES:
            raise ValueError(f"naming must be one of {NAMING_RULES}, got {self.naming!r}")
        if not 0 <= int(self.max_depth) <= 10:
            raise ValueError("max_depth must be between 0 and 10")
        check_identifier("schema", self.schema)
        for rel, name in self.names.items():
            schema, dot, table = name.partition(".")
            if not dot:
                raise ValueError(f"Name {rel!r} as schema.table, got {name!r}")
            check_identifier("schema", schema)
            check_identifier("table", table)


@dataclass
class Found:
    """One logical table: the files it reads and how."""

    schema: str
    table: str
    format: str                     # parquet | csv | json | excel | delta
    paths: list[str]                # absolute files, or the table folder for delta / partitioned parquet
    folder: str | None = None       # set when the table is a folder (parts, partitions, delta)
    hive: bool = False              # folder has key=value partitions
    size: int = 0
    files: int = 0

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.table}".lower()


@dataclass
class Issue:
    path: str
    problem: str


@dataclass
class Discovery:
    tables: list[Found]
    issues: list[Issue]
    fingerprint: str


_IDENT_BAD = re.compile(r"[\x00-\x1f\"]")


def check_identifier(kind: str, name: str) -> str:
    if not name or len(name) > 128 or _IDENT_BAD.search(name) or name != name.strip():
        raise ValueError(f"Invalid {kind} name {name!r}: 1-128 characters, no double quotes or control characters, "
                         "no spaces at either end.")
    return name


def file_format(name: str) -> tuple[str, str] | None:
    """(format, the name without its data suffix) for a data file name, else None."""
    lower = name.lower()
    for suffix in sorted(FORMATS, key=len, reverse=True):
        if lower.endswith(suffix) and len(name) > len(suffix):
            return FORMATS[suffix], name[: -len(suffix)]
    return None


def _excluded(name: str, extra: list[str]) -> bool:
    from fnmatch import fnmatch
    lower = name.lower()
    return any(fnmatch(lower, pattern.lower()) for pattern in extra)


_WRITER_DIRS = {"_delta_log", "_temporary", "_spark_metadata", "_committed", "_started"}


def _skip_dir(name: str, extra: list[str]) -> bool:
    return name.startswith((".", "~")) or name.lower() in EXCLUDED_DIRS | _WRITER_DIRS or _excluded(name, extra)


def _is_delta(folder: Path) -> bool:
    return (folder / "_delta_log").is_dir()


def _dataset_folder(folder: Path, extra: list[str]) -> tuple[str, bool] | None:
    """(format, hive) when ``folder`` is one table split into files: part files, or Hive partitions."""
    formats: set[str] = set()
    names: list[str] = []
    hive = False
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            if _HIVE.match(entry.name):
                hive = True
            elif not _skip_dir(entry.name, extra):
                return None  # a real sub-folder: this folder groups tables, it isn't one
        elif entry.is_file(follow_symlinks=False):
            if entry.name.startswith((".", "_")) or entry.name.lower().endswith((".crc", ".tmp")):
                continue  # _SUCCESS, .part-0.crc and other writer leftovers
            got = file_format(entry.name)
            if got is None:
                continue
            formats.add(got[0])
            names.append(got[1])
    if hive:
        found = _hive_format(folder)
        return (found, True) if found in ("parquet", "csv", "json") else None
    if len(formats) == 1 and len(names) >= 1 and formats <= {"parquet", "csv", "json"} and \
            all(_PART.match(n) for n in names):
        return formats.pop(), False
    return None


def _hive_format(folder: Path) -> str | None:
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if _HIVE.match(d)]
        for name in files:
            got = file_format(name)
            if got:
                return got[0]
    return None


def _folder_stats(folder: Path) -> tuple[int, int, int]:
    """Bytes, files and the newest change below a table folder (a new Delta commit, a new part file)."""
    size = count = newest = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue
            size, count, newest = size + st.st_size, count + 1, max(newest, st.st_mtime_ns)
    return size, count, newest


def _part_files(folder: Path, fmt: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if _HIVE.match(d))
        for name in sorted(files):
            got = file_format(name)
            if got and got[0] == fmt and not name.startswith((".", "_")):
                out.append(os.path.join(root, name))
    return out


def _names(source: Source, rel_parts: list[str], stem: str) -> tuple[str, str]:
    """(schema, table) for a file or table folder at ``rel_parts`` (folders below the source)."""
    if source.naming == "folders" and rel_parts:
        schema, rest = rel_parts[0], rel_parts[1:]
        return schema, "_".join(rest + [stem])
    if "." in stem and not rel_parts:
        schema, _, table = stem.partition(".")
        if schema and table:
            return schema, table
    return source.schema, "_".join(rel_parts + [stem])


def discover(source: Source, max_files: int = 20000) -> Discovery:
    root = Path(source.path).expanduser()
    issues: list[Issue] = []
    candidates: list[Found] = []
    digest = hashlib.sha256()
    if not root.is_dir():
        return Discovery([], [Issue(str(root), "The folder doesn't exist or can't be opened.")], "missing")
    seen = 0

    names = {k.replace("\\", "/").strip("/").lower(): v for k, v in source.names.items()}

    def add(found: Found) -> None:
        rel = Path(found.folder or found.paths[0]).relative_to(root).as_posix().lower()
        if rel in names:
            found.schema, _, found.table = names[rel].partition(".")
        try:
            check_identifier("schema", found.schema)
            check_identifier("table", found.table)
        except ValueError as exc:
            issues.append(Issue(found.folder or found.paths[0], str(exc)))
            return
        candidates.append(found)

    def walk(folder: Path, rel: list[str]) -> None:
        nonlocal seen
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError as exc:
            issues.append(Issue(str(folder), f"Can't open the folder: {exc.strerror or exc}"))
            return
        for entry in entries:
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                if _skip_dir(entry.name, source.exclude):
                    continue
                if _is_delta(path):
                    kind, hive = "delta", False
                else:
                    got = _dataset_folder(path, source.exclude)
                    kind, hive = got if got else (None, False)
                if kind:
                    size, count, newest = _folder_stats(path)
                    digest.update(f"{path}|{size}|{count}|{newest}".encode())
                    schema, table = _names(source, rel, entry.name)
                    # Delta: the log says which files are current. Parts: exactly the data files, so
                    # writer leftovers (.crc, _SUCCESS) are never read.
                    paths = [str(path)] if kind == "delta" else _part_files(path, kind)
                    add(Found(schema, table, kind, paths, folder=str(path), hive=hive, size=size, files=count))
                elif len(rel) < source.max_depth:
                    walk(path, rel + [entry.name])
                continue
            if not entry.is_file(follow_symlinks=False):
                continue  # links and devices: a source never reaches outside its folder through them
            if entry.name.lower() in EXCLUDED_FILES or entry.name.startswith((".", "~$")) or \
                    _excluded(entry.name, source.exclude):
                continue
            got = file_format(entry.name)
            if got is None:
                continue
            seen += 1
            if seen > max_files:
                return
            fmt, stem = got
            try:
                stat = entry.stat()
            except OSError as exc:
                issues.append(Issue(str(path), f"Can't read the file: {exc.strerror or exc}"))
                continue
            digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode())
            schema, table = _names(source, rel, stem)
            add(Found(schema, table, fmt, [str(path)], size=stat.st_size, files=1))

    walk(root, [])
    if seen > max_files:
        issues.append(Issue(str(root), f"More than {max_files} data files: discovery stopped there. "
                                       "Register a smaller folder, or exclude folders."))
    tables = _resolve_conflicts(candidates, issues)
    return Discovery(tables, issues, digest.hexdigest())


def _resolve_conflicts(candidates: list[Found], issues: list[Issue]) -> list[Found]:
    by_key: dict[str, list[Found]] = {}
    for found in candidates:
        by_key.setdefault(found.key, []).append(found)
    tables: list[Found] = []
    for group in by_key.values():
        group.sort(key=lambda f: (PRECEDENCE[f.format], f.paths[0]))
        winner = group[0]
        tables.append(winner)
        for loser in group[1:]:
            issues.append(Issue(loser.folder or loser.paths[0],
                                f"Also named {winner.schema}.{winner.table}, so it isn't a table: "
                                f"{Path(winner.folder or winner.paths[0]).name} ({winner.format}) is. "
                                "Rename one of them, or set a table name for it."))
    return sorted(tables, key=lambda f: f.key)


def fingerprint(source: Source) -> str:
    """Changes when any data file below the source is added, removed, resized or rewritten."""
    return discover(source).fingerprint
