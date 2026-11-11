"""Table health and maintenance.

Tables written from this PC carry no V-Order, which Direct Lake and the SQL analytics
endpoint read fastest, and every run adds a Delta version whose old files stay until
VACUUM. Fabric's own table maintenance job (OPTIMIZE with V-Order, then VACUUM) fixes
both on Fabric capacity; for tables on this PC the same compaction runs locally with
delta-rs, without V-Order.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

from . import durable

SMALL_FILE_BYTES = 32 * 1024 * 1024
SMALL_FILE_COUNT = 20     # this many files averaging under SMALL_FILE_BYTES is worth compacting
VERSIONS_BEFORE_VACUUM = 30
RETENTION_HOURS = 7 * 24 + 1  # Fabric's default retention is 7 days
LOG_RETENTION_DAYS = 30  # Delta's default: older log entries go at the next checkpoint
# delta-rs writes each file on this PC as <name>#<n> and then renames (data) or links (log) it into
# place, so a write stopped part way leaves files under that staging name. One untouched this long
# belongs to a write that has stopped: a live one finishes a file and publishes it in one go.
STALE_STAGED_HOURS = 1
# The names delta-rs (and Spark) give data files. Only files named so are ever counted or removed as
# leftovers: anything else in a table folder (a note, a CSV someone dropped in, desktop.ini) isn't the
# table's, whatever the log says.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DATA_FILE = re.compile(rf"part-\d{{5}}-{_UUID}(-c\d{{3}})?(\.[a-z0-9]+)?\.parquet", re.I)
_STAGED_DATA = re.compile(rf"{_DATA_FILE.pattern}#\d+", re.I)
_STAGED_LOG = re.compile(r"\d{20}\.(json|checkpoint\.parquet)#\d+")
_COMMIT = re.compile(r"\d{20}\.json")
_FOREIGN = re.compile(r"[a-z][a-z0-9+.-]*:|[/\\]", re.I)  # a URI (abfss:, file:, C:) or a rooted path


def _when(ms: Any) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).isoformat(timespec="seconds")


def _by_fabric(commit: dict[str, Any]) -> bool:
    """Spark in Fabric writes the commit; delta-rs (this app) stamps its own clientVersion."""
    return "delta-rs" not in str(commit.get("clientVersion", "")) and bool(commit.get("engineInfo"))


# Commits that change only the table's settings, not its files: a write since the last OPTIMIZE
# is a commit that isn't one of these. Fabric adds "AUTOSET VORDER TBLPROPERTY" right after the
# first OPTIMIZE ... VORDER of a table, which must not count as a write.
_NO_FILES = re.compile(r"TBLPROPERT|CONSTRAINT|^(ADD|CHANGE|RENAME|DROP) COLUMNS?\b|^UPGRADE PROTOCOL|^VACUUM|^OPTIMIZE$", re.I)
VORDER_KEY = b"com.microsoft.parquet.vorder.enabled"
FOOTERS_CHECKED = 200  # V-Order is read from this many files' footers at most, newest first


def _vorder_optimize(commit: dict[str, Any]) -> bool:
    """An OPTIMIZE by Fabric Spark that didn't say it left V-Order off."""
    vorder = (commit.get("operationParameters") or {}).get("vorder")
    return _by_fabric(commit) and str(vorder).lower() != "false"


def vorder_files(uri: str, options: dict[str, str] | None = None, limit: int = FOOTERS_CHECKED) -> dict[str, Any]:
    """How many of the table's files are V-Ordered, read from their Parquet footers, where Fabric
    stamps ``com.microsoft.parquet.vorder.enabled``. Reads the newest ``limit`` files' footers."""
    import pyarrow.parquet as pq
    from deltalake.fs import DeltaStorageHandler

    table = DeltaTable(uri, storage_options=options)
    actions = table.get_add_actions(flatten=True)
    paths = cast(list[str], actions.column("path").to_pylist()) if actions.num_rows else []
    times = cast(list[Any], actions.column("modification_time").to_pylist()) if actions.num_rows else []
    newest = [p for _, p in sorted(zip(times, paths, strict=True), key=lambda tp: str(tp[0] or ""), reverse=True)]
    fs = DeltaStorageHandler(table.table_uri, options)
    v_ordered = 0
    for path in newest[:limit]:
        with fs.open_input_file(path) as f:
            meta = pq.read_metadata(f).metadata or {}
        v_ordered += meta.get(VORDER_KEY, b"").strip().lower() == b"true"
    return {"files": len(paths), "checked": min(len(paths), limit), "v_ordered": v_ordered}


def health(uri: str, options: dict[str, str] | None = None, local: bool = False) -> dict[str, Any]:
    """File, size and history numbers for one Delta table, plus what maintenance would help.

    V-Order only matters to Fabric readers, so it isn't advised for a table on this PC."""
    try:
        table = DeltaTable(uri, storage_options=options)
    except TableNotFoundError:
        return {"exists": False, "advice": [], "status": "missing"}
    actions = table.get_add_actions(flatten=True)
    sizes = cast(list[int], actions.column("size_bytes").to_pylist()) if actions.num_rows else []
    files, total = len(sizes), sum(sizes)
    history = table.history(200)  # newest first
    version = table.version()
    last_optimize = next((h for h in history if h.get("operation") == "OPTIMIZE"), None)
    last_vacuum = next((h for h in history if str(h.get("operation", "")).startswith("VACUUM")), None)
    last_write = next((h for h in history if not _NO_FILES.search(str(h.get("operation", "")))), None)
    v_ordered = bool(last_optimize and _vorder_optimize(last_optimize) and last_write
                     and int(last_optimize.get("timestamp", 0)) >= int(last_write.get("timestamp", 0)))
    vacuum_version = int(last_vacuum.get("version", -1)) if last_vacuum and "version" in last_vacuum else None
    since_vacuum = version - vacuum_version if vacuum_version is not None else version + 1
    advice = []
    if files >= SMALL_FILE_COUNT and total / max(files, 1) < SMALL_FILE_BYTES:
        advice.append(f"{files} small files: compacting them speeds up every read")
    if not v_ordered and files and not local:
        advice.append("No V-Order since the last write: Direct Lake and the SQL endpoint read V-Ordered files faster")
    if since_vacuum >= VERSIONS_BEFORE_VACUUM:
        advice.append(f"{since_vacuum} versions since the last VACUUM: old files still take up storage")
    left: list[str] = []
    if local:
        left = leftovers(uri, table)
        if left:
            days = kept_days(table.metadata().configuration) or RETENTION_HOURS // 24
            n = len(left)
            advice.append(f"{n} file{'' if n == 1 else 's'} ({_mb(_size(uri, left))}) left by writes that stopped part way. "
                          f"Compact removes the unfinished ones, and the rest once they're older than the "
                          f"{days} days of history the table keeps")
    return {"exists": True, "version": version, "files": files, "bytes": total,
            "avg_file_bytes": total // files if files else 0, "v_ordered": v_ordered,
            "last_optimize": _when(last_optimize.get("timestamp")) if last_optimize else None,
            "last_vacuum": _when(last_vacuum.get("timestamp")) if last_vacuum else None,
            "last_write": _when(last_write.get("timestamp")) if last_write else None,
            "versions_since_vacuum": since_vacuum, "leftover_files": len(left), "advice": advice,
            "status": "needs_maintenance" if advice else "healthy"}


def retention_hours(days: int) -> int:
    return int(days) * 24 + 1


def fabric_retention(days: int) -> str:
    """Retention as Fabric's table maintenance job takes it: d.hh:mm:ss, an hour of margin."""
    return f"{int(days)}.01:00:00"


def retention_properties(days: int) -> dict[str, str]:
    """Table properties that keep ``days`` of history for every engine: VACUUM keeps removed
    files that long, and the log keeps its entries at least that long (Delta's default is 30)."""
    return {"delta.deletedFileRetentionDuration": f"interval {int(days)} days",
            "delta.logRetentionDuration": f"interval {max(int(days), LOG_RETENTION_DAYS)} days"}


_UNIT_HOURS = {"hour": 1, "day": 24, "week": 24 * 7}


def kept_days(config: dict[str, str] | None) -> int | None:
    """The history a table's own ``delta.deletedFileRetentionDuration`` keeps, in whole days
    (rounded up), or None when it doesn't set one. Spark writes it as ``interval 30 days``."""
    text = str((config or {}).get("delta.deletedFileRetentionDuration") or "").lower()
    m = re.search(r"(\d+)\s*(hour|day|week)", text)
    if not m:
        return None
    return -(-int(m.group(1)) * _UNIT_HOURS[m.group(2)] // 24)


def vacuum_preview(uri: str, days: int, options: dict[str, str] | None = None) -> list[str]:
    """The files (relative to the table folder) VACUUM keeping ``days`` of history would delete.
    A dry run: nothing is deleted."""
    table = DeltaTable(uri, storage_options=options)
    paths = table.vacuum(retention_hours=retention_hours(days), dry_run=True, enforce_retention_duration=False)
    root = uri.rstrip("/") + "/"
    return [p[len(root):] if p.startswith(root) else p for p in paths]


def set_retention(uri: str, days: int, options: dict[str, str] | None = None) -> bool:
    """Write the retention properties onto the table if they differ. True when it changed."""
    table = DeltaTable(uri, storage_options=options)
    wanted = retention_properties(days)
    config = table.metadata().configuration or {}
    if all(config.get(k) == v for k, v in wanted.items()):
        return False
    table.alter.set_table_properties(wanted, raise_if_not_exists=False)
    return True


def compact_local(uri: str, days: int = 7) -> dict[str, Any]:
    """OPTIMIZE (compaction, no V-Order) and VACUUM for a table on this PC, keeping ``days`` of history."""
    before = DeltaTable(uri).version()
    set_retention(uri, days)
    table = DeltaTable(uri)
    metrics = table.optimize.compact()
    removed = table.vacuum(retention_hours=retention_hours(days), dry_run=False, enforce_retention_duration=False)
    # A data file no commit names (a write stopped before its commit) goes once it's as old as the
    # history kept, the age at which VACUUM removes any file: a write still under way never loses one.
    unnamed = _remove(uri, _unnamed(uri, table, retention_hours(days)))
    staged = _remove(uri, _stale_staged(uri))
    durable.sync_commits(uri, before + 1, DeltaTable(uri).version())  # the compacted files and their commits
    return {"files_added": metrics.get("numFilesAdded", 0), "files_removed": metrics.get("numFilesRemoved", 0),
            "vacuumed": len(removed), "unnamed_removed": unnamed, "staged_removed": staged}


def _is_link(entry: os.DirEntry[str]) -> bool:
    """A symbolic link, or on Windows any reparse point (a junction, a mount point). An entry gone
    meanwhile counts as one, so the walk skips it."""
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:  # silent-ok: gone meanwhile; skipped like a link
        return True
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _data_folder_files(uri: str) -> Any:
    """The plain files of a table folder outside its log, never going into a linked folder or one Delta
    keeps out of the table (_delta_log, _change_data and any other _ or . folder)."""
    stack = [uri]
    while stack:
        with os.scandir(stack.pop()) as it:
            for entry in it:
                if _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not entry.name.startswith(("_", ".")):
                        stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    yield entry


def _older_than(entry: os.DirEntry[str], hours: float) -> bool:
    try:
        return entry.stat(follow_symlinks=False).st_mtime < time.time() - hours * 3600
    except OSError:  # silent-ok: gone meanwhile
        return False


def _stale_staged(uri: str) -> list[str]:
    """Files still under delta-rs's staging name, untouched for STALE_STAGED_HOURS, as paths relative to
    the table folder: data files and log commits a stopped write never put in place."""
    out = [e.path for e in _data_folder_files(uri)
           if _STAGED_DATA.fullmatch(e.name) and _older_than(e, STALE_STAGED_HOURS)]
    log = Path(uri) / "_delta_log"
    if log.is_dir():
        with os.scandir(log) as it:
            out += [e.path for e in it if not _is_link(e) and e.is_file(follow_symlinks=False)
                    and _STAGED_LOG.fullmatch(e.name) and _older_than(e, STALE_STAGED_HOURS)]
    return sorted(Path(p).relative_to(uri).as_posix() for p in out)


def _unnamed(uri: str, table: DeltaTable, hours: float) -> list[str]:
    """Data files untouched for ``hours`` that neither the table's current version names nor a commit
    removed (those are VACUUM's), as paths relative to the table folder."""
    named = {unquote(p) for p in cast(list[str], table.get_add_actions(flatten=True).column("path").to_pylist())}
    named |= {unquote(p) for p in table.vacuum(retention_hours=0, dry_run=True, enforce_retention_duration=False)}
    out = []
    for e in _data_folder_files(uri):
        rel = Path(e.path).relative_to(uri).as_posix()
        if _DATA_FILE.fullmatch(e.name) and rel not in named and _older_than(e, hours):
            out.append(rel)
    return sorted(out)


def _remove(uri: str, paths: list[str]) -> int:
    """Delete these files of the table folder. No reader or later write ever opens one: a commit is only
    read under its own name, and a data file only once a commit names it."""
    removed = 0
    for rel in paths:
        try:
            (Path(uri) / rel).unlink()
            removed += 1
        except FileNotFoundError:  # silent-ok: removed meanwhile
            continue
    return removed


def leftovers(uri: str, table: DeltaTable | None = None) -> list[str]:
    """What writes stopped part way left in a table folder on this PC, as paths relative to it: data
    files no commit names and stale staged files, all untouched for STALE_STAGED_HOURS so a write
    still under way isn't counted. Only files named as Delta names them. Nothing is deleted."""
    table = table or DeltaTable(uri)
    return sorted(set(_stale_staged(uri)) | set(_unnamed(uri, table, STALE_STAGED_HOURS)))


def _named_anywhere(uri: str, table: DeltaTable | None) -> set[str]:
    """Every file path any version of the table still on disk names: the current version's files, the
    files commits removed (VACUUM's), and each add and remove in every commit file in _delta_log, so a
    file only an older version reads (time travel) counts as named too."""
    named: set[str] = set()
    if table is not None:
        named |= {unquote(p) for p in cast(list[str], table.get_add_actions(flatten=True).column("path").to_pylist())}
        root = uri.rstrip("/") + "/"
        named |= {unquote(p[len(root):] if p.startswith(root) else p)
                  for p in table.vacuum(retention_hours=0, dry_run=True, enforce_retention_duration=False)}
    log = Path(uri) / "_delta_log"
    if not log.is_dir():
        return named
    with os.scandir(log) as it:
        commits = [e.path for e in it if _COMMIT.fullmatch(e.name) and not _is_link(e)]
    for path in commits:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if '"add"' not in line and '"remove"' not in line:
                    continue
                action = json.loads(line)
                for kind in ("add", "remove"):
                    if isinstance(action.get(kind), dict) and action[kind].get("path"):
                        named.add(unquote(str(action[kind]["path"])))
    return named


def stopped_run_leftovers(uri: str, since: float) -> dict[str, Any]:
    """The files a run that ended mid-write (its process gone, nothing committed) left in a table folder
    on this PC, as paths relative to it, with their bytes. A dry run: nothing is deleted.

    A file counts only when every one of these holds, so it can only be that run's:
    - delta-rs names it as a data file (or its staged copy), or it is a staged commit in _delta_log;
    - it was last written at or after ``since``, the stopped run's start;
    - no version of the table still on disk names it (_named_anywhere);
    - it isn't in a linked folder, _delta_log aside, or a folder Delta keeps out of the table.
    And none count when any commit was made at or after ``since``: another writer was at work then,
    and its files can't be told from the stopped run's, so all are left for Compact and VACUUM.

    Nothing counts either when the table folder or its _delta_log is a link (a symbolic link, a
    junction), or when any version names a file by a full path or URI (a shallow clone, an imported
    table), which can't be matched against the folder's files.

    The caller must hold the table's write lease and know the run's process has ended. That is not
    proof no other write is under way: compaction holds a lease of its own and runs alongside
    appends, and notebook and SQL writes to a local table don't take the table's lease. Their files,
    not committed yet, look just like these. So this is a list to report, not one to delete from."""
    root = Path(uri)
    if _linked(root) or _linked(root / "_delta_log"):
        return {"files": [], "bytes": 0, "refused": "the table folder or its log is a link, so its files aren't looked at"}
    if not root.is_dir():
        return {"files": [], "bytes": 0, "refused": None}
    try:
        table: DeltaTable | None = DeltaTable(uri)
    except TableNotFoundError:  # silent-ok: a first load that stopped: no version names anything
        table = None
    if table is not None:
        since_ms = since * 1000
        # A commit with no time can't be shown to come before the run: it counts as after.
        if any(c.get("timestamp") is None or int(c["timestamp"]) >= since_ms for c in table.history(20)):
            return {"files": [], "bytes": 0,
                    "refused": "a commit was made to the table after the stopped run started, so its files "
                               "can't be told from another writer's: they're left for Compact"}
    named = _named_anywhere(uri, table)
    if any(_FOREIGN.match(p) for p in named):
        return {"files": [], "bytes": 0,
                "refused": "a version of the table names a file by its full path, so which files it names "
                           "can't be told from the folder"}
    out = []
    for e in _data_folder_files(uri):
        rel = Path(e.path).relative_to(uri).as_posix()
        if (_DATA_FILE.fullmatch(e.name) or _STAGED_DATA.fullmatch(e.name)) and rel not in named \
                and _written_since(e, since):
            out.append(rel)
    log = Path(uri) / "_delta_log"
    if log.is_dir():
        with os.scandir(log) as it:
            out += [f"_delta_log/{e.name}" for e in it if not _is_link(e) and e.is_file(follow_symlinks=False)
                    and _STAGED_LOG.fullmatch(e.name) and _written_since(e, since)]
    out.sort()
    return {"files": out, "bytes": _size(uri, out), "refused": None}


def _linked(path: Path) -> bool:
    """A symbolic link, or on Windows any reparse point (a junction, a mount point). Missing: False."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:  # silent-ok: nothing there to follow
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _written_since(entry: os.DirEntry[str], since: float) -> bool:
    try:
        return entry.stat(follow_symlinks=False).st_mtime >= since
    except OSError:  # silent-ok: gone meanwhile
        return False


def _size(uri: str, paths: list[str]) -> int:
    total = 0
    for rel in paths:
        try:
            total += (Path(uri) / rel).stat().st_size
        except OSError:  # silent-ok: gone meanwhile
            continue
    return total


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"
