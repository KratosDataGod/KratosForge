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
import logging
import math
import numbers
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

from deltalake import CommitProperties, DeltaTable, PostCommitHookProperties
from deltalake.exceptions import TableNotFoundError

from . import durable, retrying

_log = logging.getLogger(__name__)

SMALL_FILE_BYTES = 32 * 1024 * 1024
SMALL_FILE_COUNT = 20     # this many files averaging under SMALL_FILE_BYTES is worth compacting
VERSIONS_BEFORE_VACUUM = 30
RETENTION_HOURS = 7 * 24 + 1  # Fabric's default retention is 7 days
MIN_RETENTION_DAYS = 7  # Delta's default; VACUUM with less can delete files a reader or writer still needs
LOG_RETENTION_DAYS = 30  # Delta's default: older log entries go at the next checkpoint
# delta-rs cleans the log when a commit writes a checkpoint (every delta.checkpointInterval versions) unless
# delta.enableExpiredLogCleanup is false: it deletes each commit and checkpoint older than
# delta.logRetentionDuration that comes before the last checkpoint at or before the oldest log file it keeps.
# The version current when the history kept starts can be older than that, and is then deleted: it (and its
# files, which VACUUM keeps) can no longer be loaded, and a time travel to then reads a later version. A
# RESTORE or a property change on this PC takes KEEP_LOG and cleans no log; OPTIMIZE and VACUUM ignore it
# (delta-rs 1.6.6), so keep_log comes first.
# It writes no checkpoint either: one drops the removals older than deletedFileRetentionDuration, and a VACUUM
# after it no longer knows to delete their files. A later commit at the interval checkpoints.
KEEP_LOG = PostCommitHookProperties(create_checkpoint=False, cleanup_expired_logs=False)
# An ordinary write (an append, overwrite, MERGE, UPDATE or DELETE) checkpoints at the interval as usual but
# cleans no log in its commit: after_write cleans it once keep_log has kept what the history kept reads. Before
# the commit its version isn't known (another writer can take the one expected, and a write pinned to the
# version it read would conflict with a property commit made first), after it, it is.
WRITE_HOOKS = PostCommitHookProperties(create_checkpoint=True, cleanup_expired_logs=False)
_LOG_FILE = re.compile(r"(\d{20})\.(json|checkpoint(\.\d+\.\d+)?\.parquet)")
_INTERVAL = re.compile(r"(?:interval\s+)?(\d+(?:\.\d+)?)\s*(millisecond|second|minute|hour|day|week)s?", re.I)
_UNIT_SECONDS = {"millisecond": 0.001, "second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400}
_RS_UNIT_SECONDS = {"nanosecond": 1e-9, "microsecond": 1e-6} | _UNIT_SECONDS
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
# A commit, a checkpoint, or the _last_checkpoint pointer that a write killed while checkpointing never put in place
_STAGED_LOG = re.compile(r"(\d{20}\.(json|checkpoint\.parquet)|_last_checkpoint)#\d+")
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
        # The log's path is percent-encoded once over the file's name (region=North%2520America for the folder
        # region=North%20America), and the store opens a name as it is: decoded, as a reader of the log does.
        with fs.open_input_file(unquote(path)) as f:
            meta = pq.read_metadata(f).metadata or {}
        v_ordered += meta.get(VORDER_KEY, b"").strip().lower() == b"true"
    return {"files": len(paths), "checked": min(len(paths), limit), "v_ordered": v_ordered}


def health(uri: str, options: dict[str, str] | None = None, local: bool = False,
           table: DeltaTable | None = None) -> dict[str, Any]:
    """File, size and history numbers for one Delta table, plus what maintenance would help.

    V-Order only matters to Fabric readers, so it isn't advised for a table on this PC. ``table``,
    when given, is that table already loaded, so its log isn't read a second time."""
    if table is None:
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


def _retention_number(value: Any, unit: str) -> float:
    """``value`` as a finite number, or the refusal: NaN, infinity, True and text are no length of history."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise ValueError(f"VACUUM has to keep at least {MIN_RETENTION_DAYS} days of history, and {value!r} isn't a "
                         f"number of {unit}. Nothing was changed. Give a number of {unit}, "
                         f"{MIN_RETENTION_DAYS if unit == 'days' else MIN_RETENTION_DAYS * 24} or more.")
    return float(value)


def check_retention(days: int, allow_shorter: bool = False) -> None:
    """Refuse a VACUUM keeping less than MIN_RETENTION_DAYS of history, unless a policy allows it
    (``allow_shorter``). With less, VACUUM can delete files that a read or write still under way in
    another process is using, and time travel to those versions stops working. What isn't a finite
    number of days, or is negative, is refused whatever ``allow_shorter`` says."""
    n = _retention_number(days, "days")
    if n < 0 or (n < MIN_RETENTION_DAYS and not allow_shorter):
        raise ValueError(f"VACUUM has to keep at least {MIN_RETENTION_DAYS} days of history, not {n:g}, because "
                         "with less it can delete files that readers or writers in other processes still use, and time "
                         "travel to those versions stops working. Nothing was changed.")


def check_retain_hours(hours: Any) -> None:
    """Refuse ``VACUUM t RETAIN n HOURS`` keeping less than MIN_RETENTION_DAYS (in hours), or n that isn't a
    finite number. There is no way past it."""
    n = _retention_number(hours, "hours")
    if n < MIN_RETENTION_DAYS * 24:
        raise ValueError(f"VACUUM keeps at least {MIN_RETENTION_DAYS} days ({MIN_RETENTION_DAYS * 24} hours) of "
                         f"history on this PC, not {n:g} hours, because with less it can delete files that readers "
                         "or writers in other processes still use, and time travel to those versions stops working. "
                         f"Nothing was changed. Use RETAIN {MIN_RETENTION_DAYS * 24} HOURS or more, or leave RETAIN out.")


def skewed_versions(table: DeltaTable, hours: float, now: float | None = None) -> tuple[list[int], int | None]:
    """The versions inside the retention whose files VACUUM would still delete because a later commit is dated
    before the cutoff although an earlier one isn't (a writer's clock out of step), and the oldest such later
    commit's time (ms). VACUUM goes by each removal's own date; a version is inside the retention when a commit
    at or before the one that replaced it is dated inside it. A commit with no date counts as now. Both are
    empty for a log whose dates rise with its versions."""
    now_ms = (time.time() if now is None else now) * 1000
    cutoff = now_ms - hours * 3_600_000 + 300_000  # five minutes later than VACUUM's own, which reads the clock after
    stamps = sorted((int(h["version"]), now_ms if h.get("timestamp") is None else float(h["timestamp"]))
                    for h in table.history() if "version" in h)
    earliest_after = [math.inf] * (len(stamps) + 1)  # [i]: the earliest date from commit i on
    for i in range(len(stamps) - 1, -1, -1):
        earliest_after[i] = min(stamps[i][1], earliest_after[i + 1])
    keep: list[int] = []
    oldest: float | None = None
    latest = -math.inf
    for i in range(len(stamps) - 1):
        latest = max(latest, stamps[i][1], stamps[i + 1][1])  # the newest date up to the commit that replaced it
        later = earliest_after[i + 1]
        if latest >= cutoff and later < cutoff:
            keep.append(stamps[i][0])
            oldest = later if oldest is None else min(oldest, later)
    return keep, None if oldest is None else int(oldest)


def vacuum_keep(versions: list[int]) -> dict[str, Any]:
    """VACUUM's keep_versions, only when there are any: a log whose dates rise is vacuumed as before."""
    return {"keep_versions": versions} if versions else {}


# More history than this for a clock out of step is a date that can't be right (a commit dated 1970, say):
# Fabric's VACUUM would in effect stop removing anything, so X-ray refuses and says why instead.
MAX_SKEW_DAYS = 365


def skew_safe_days(table: DeltaTable, days: int, now: float | None = None) -> int:
    """``days``, or more when a commit dated out of step with the one before it would let a VACUUM keeping
    ``days`` delete files a version inside them still reads: enough that the removal dated earliest is kept,
    for an engine (Fabric's table maintenance job) that can't be told which versions to keep."""
    now = time.time() if now is None else now
    while True:
        keep, oldest = skewed_versions(table, retention_hours(days), now)
        need = math.ceil((now * 1000 - oldest) / 86_400_000) if keep and oldest is not None else days
        if need <= days:
            return days
        days = need


def fabric_retention(days: int) -> str:
    """Retention as Fabric's table maintenance job takes it: d.hh:mm:ss, an hour of margin. Fewer than
    MIN_RETENTION_DAYS is refused, as on this PC: that job's VACUUM can delete files other readers use."""
    check_retention(days)
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


def _with_own(table: DeltaTable, days: int) -> int:
    """``days``, or the table's own delta.deletedFileRetentionDuration when that keeps longer: it wins, as in
    a notebook's VACUUM and X-ray's."""
    own = kept_days(table.metadata().configuration)
    return own if own and own > days else days


def vacuum_preview(uri: str, days: int, options: dict[str, str] | None = None,
                   allow_shorter: bool = False) -> list[str]:
    """The files (relative to the table folder) VACUUM keeping ``days`` of history would delete.
    A dry run: nothing is deleted. Fewer than MIN_RETENTION_DAYS needs ``allow_shorter``. The table's own longer
    retention wins, and a version inside it keeps its files whatever a later commit's clock says."""
    check_retention(days, allow_shorter)
    table = DeltaTable(uri, storage_options=options)
    days = _with_own(table, days)
    keep, _oldest = skewed_versions(table, retention_hours(days))
    paths = table.vacuum(retention_hours=retention_hours(days), dry_run=True, enforce_retention_duration=False,
                         **vacuum_keep(keep))
    # Named as compact_local names what its VACUUM removed, so the two lists agree however delta-rs gives them.
    return [relative_to_table(uri, p) for p in paths]


def set_retention(uri: str, days: int, options: dict[str, str] | None = None, allow_shorter: bool = False) -> bool:
    """Write the retention properties onto the table if they differ. True when it changed. Fewer than
    MIN_RETENTION_DAYS is refused, unless ``allow_shorter``: every engine's VACUUM would then keep that little."""
    check_retention(days, allow_shorter)
    table = DeltaTable(uri, storage_options=options)
    wanted = retention_properties(days)
    config = table.metadata().configuration or {}
    own = str(config.get("delta.logRetentionDuration") or "")
    if _own_seconds(own) > _interval_seconds(wanted["delta.logRetentionDuration"]):
        # The table's own longer one stays, in words delta-rs reads as that long too (it reads "INTERVAL 100 DAYS"
        # as its default 30 days: _delta_rs_seconds).
        wanted["delta.logRetentionDuration"] = own if _delta_rs_seconds(own) == _own_seconds(own) \
            else f"interval {math.ceil(_own_seconds(own) / 86400)} days"
    if all(config.get(k) == v for k, v in wanted.items()):
        return False
    # A commit like any other: on Windows it can lose the staged-commit race to a writer (retrying.local_commit).
    retrying.local_commit(lambda: DeltaTable(uri, storage_options=options).alter.set_table_properties(
        wanted, raise_if_not_exists=False, post_commithook_properties=KEEP_LOG), "this table's next version")
    return True


def _interval_seconds(text: str) -> float:
    """``interval 30 days`` (or hours, weeks...) in seconds, read leniently as Spark might (any case, decimals,
    "interval" left out); 0 for anything else, the shortest there is."""
    m = _INTERVAL.fullmatch(text.strip())
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()] if m else 0.0


def _delta_rs_seconds(text: str) -> float | None:
    """An interval as delta-rs 1.6.6 reads it, in seconds, or None when it can't (it then keeps Delta's default 30
    days of log, whatever the words meant): whitespace-separated words, the first exactly ``interval``, then a whole
    number (a leading + allowed) and a lower-case unit, singular or plural; later words are ignored. So
    ``INTERVAL 100 DAYS``, ``100 days`` and ``interval 1.5 days`` are 30 days to it, and ``interval +100 days``
    100. Anything it might read otherwise (a control character Python splits on and Rust doesn't, a number past
    any real duration) counts as unreadable."""
    if re.search(r"[\x1c-\x1f]", text):
        return None
    words = text.split()
    if len(words) < 3 or words[0] != "interval" or not re.fullmatch(r"\+?[0-9]{1,15}", words[1]):
        return None
    unit = words[2][:-1] if words[2].endswith("s") else words[2]
    return int(words[1]) * _RS_UNIT_SECONDS[unit] if unit in _RS_UNIT_SECONDS else None


def _own_seconds(text: str) -> float:
    """The longest a table's own delta.logRetentionDuration may be meant to keep the log, as delta-rs or Spark
    reads it: never shortened by a property this app writes."""
    return max(_delta_rs_seconds(text) or 0.0, _interval_seconds(text))


def log_seconds(config: dict[str, str] | None) -> float:
    """How long delta-rs's log cleanup keeps a log file, in seconds: the table's delta.logRetentionDuration as
    delta-rs reads it (_delta_rs_seconds), Delta's 30 days when it sets none, and for one delta-rs can't read the
    shorter of 30 days (its default then) and what it seems to say (0 when that can't be read either); infinite
    only when delta.enableExpiredLogCleanup is exactly ``false``: delta-rs cleans at any other value ("False" too)."""
    config = config or {}
    if config.get("delta.enableExpiredLogCleanup") == "false":
        return math.inf
    text = config.get("delta.logRetentionDuration")
    if text is None:
        return LOG_RETENTION_DAYS * 86400.0
    read = _delta_rs_seconds(str(text))
    return read if read is not None else min(_interval_seconds(str(text)), LOG_RETENTION_DAYS * 86400.0)


def keep_log(uri: str, hours: float, options: dict[str, str] | None = None, now: float | None = None) -> bool:
    """Before an OPTIMIZE or VACUUM commit, which can clean the log (KEEP_LOG): raise the table's
    delta.logRetentionDuration until every log file is kept that a version current in the last ``hours`` is
    read from, the last checkpoint at or before the oldest such version on. True when it changed."""
    from deltalake.fs import DeltaStorageHandler
    from pyarrow.fs import FileSelector

    table = DeltaTable(uri, storage_options=options)
    retention = log_seconds(table.metadata().configuration)
    if math.isinf(retention):
        return False
    now = time.time() if now is None else now
    start = now - hours * 3600
    commits: dict[int, float] = {}
    checkpoints: dict[int, float] = {}
    listing = DeltaStorageHandler(table.table_uri, options).get_file_info_selector(FileSelector("_delta_log"))
    for info in listing:
        m = _LOG_FILE.fullmatch(info.base_name)
        if m and info.mtime is not None:
            found = commits if m.group(2) == "json" else checkpoints
            v = int(m.group(1))
            found[v] = min(found.get(v, math.inf), info.mtime.timestamp())  # a checkpoint in parts: its oldest
    if not commits:
        return False
    # A version's date is the later of its file's and its commitInfo's, so the oldest version inside the history
    # kept is found by either clock; the one before it was current at its start.
    dated = dict(commits)
    for h in table.history():
        if "version" in h and h.get("timestamp") is not None and int(h["version"]) in dated:
            dated[int(h["version"])] = max(dated[int(h["version"])], float(h["timestamp"]) / 1000)
    inside = [v for v, when in dated.items() if when > start]
    first = min(commits)
    current = max(first, min(inside) - 1) if inside else max(commits)
    base = max((v for v in checkpoints if v <= current), default=None)
    needed = [when for v, when in commits.items() if v >= (first if base is None else base)]
    if base is not None:
        needed.append(checkpoints[base])
    oldest = min(needed)
    if now - oldest + 86400 < retention:  # a day to spare for the commit that follows
        return False
    # Whole multiples of Delta's 30 days, so a table written seldom isn't raised again at every run; never shorter
    # than the table's own (one delta-rs reads as its 30-day default, like "INTERVAL 100 DAYS", becomes 100 days).
    days = math.ceil((now - oldest + 86400) / 86400 / LOG_RETENTION_DAYS) * LOG_RETENTION_DAYS
    days = max(days, math.ceil(_own_seconds(str((table.metadata().configuration or {}).get(
        "delta.logRetentionDuration") or "")) / 86400))
    retrying.local_commit(lambda: DeltaTable(uri, storage_options=options).alter.set_table_properties(
        {"delta.logRetentionDuration": f"interval {days} days"}, post_commithook_properties=KEEP_LOG,
        # named in the table's history, so a property change nobody typed can be told apart from a person's
        commit_properties=CommitProperties(custom_metadata={"fabric_desktop.operation": "keep_log"})),
        "this table's next version")
    return True


def checkpoints_between(config: dict[str, str] | None, before: int | None, after: int) -> bool:
    """Whether a commit after version ``before`` (None: the table is new) up to ``after`` is one delta-rs writes a
    checkpoint at: version + 1 a multiple of delta.checkpointInterval (100 when the table sets none). One this
    can't read, or that isn't over 0, counts as checkpointing: the log is then looked after, never left."""
    text = (config or {}).get("delta.checkpointInterval")
    try:
        interval = 100 if text is None else int(str(text).strip())
    except ValueError:
        return True
    if interval <= 0:
        return True
    first = -1 if before is None else int(before)
    return (int(after) + 1) // interval * interval >= first + 2  # a multiple of it in [first + 2, after + 1]


def after_write(uri: str, before: int | None, table: DeltaTable, options: dict[str, str] | None = None,
                days: int | None = None) -> bool:
    """After an ordinary write's commit, made with WRITE_HOOKS (a checkpoint as usual, no log cleanup): when it, or
    a commit since ``before``, is one delta-rs checkpoints at, keep the log a version current in the history kept
    reads (keep_log), and only then clean the expired log as the commit would have. The history kept is the longest
    of ``days`` (a layer's Keep history), the table's own delta.deletedFileRetentionDuration and MIN_RETENTION_DAYS.
    ``table`` is the table as committed; the log is listed only after a checkpoint, so other writes cost nothing
    more. True when the log was cleaned. Nothing here fails the write, which is in: a step that fails leaves the
    log as it is until the next checkpoint (with a warning). keep_log runs before cleanup_metadata because the
    cleanup deletes by the retention keep_log may have to raise first; the other way round the log is already gone."""
    try:
        config = table.metadata().configuration or {}
        if not checkpoints_between(config, before, table.version()) or math.isinf(log_seconds(config)):
            return False
        keep_log(uri, retention_hours(max(days or 0, kept_days(config) or 0, MIN_RETENTION_DAYS)), options)
        DeltaTable(uri, storage_options=options).cleanup_metadata()
        return True
    except Exception as exc:  # noqa: BLE001 - the write is committed; the log waits for the next checkpoint
        from .redact import describe
        _log.warning("The Delta log of %s is left as it is until its next checkpoint: %s", uri, describe(exc))
        return False


def compact_local(uri: str, days: int = 7, allow_shorter: bool = False) -> dict[str, Any]:
    """OPTIMIZE (compaction, no V-Order) and VACUUM for a table on this PC, keeping ``days`` of history.
    Fewer than MIN_RETENTION_DAYS is refused before anything changes, unless ``allow_shorter``. The table's own
    longer retention wins (and stays), and a version inside it keeps its files whatever a later commit's clock says."""
    check_retention(days, allow_shorter)
    first = DeltaTable(uri)
    before, files_before = first.version(), len(first.file_uris())
    days = _with_own(first, days)
    set_retention(uri, days, allow_shorter=allow_shorter)
    keep_log(uri, retention_hours(days))  # before OPTIMIZE and VACUUM, whose commits can clean the log
    # On Windows a writer committing the same next version at the same moment makes this commit fail to open
    # its staged log file, or another process holds a log file (retrying.windows_log_race); nothing was committed,
    # so compacting again is safe.
    # One that still loses after its tries fails as delta-rs's CommitFailedError (retrying.local_commit).
    metrics = retrying.local_commit(lambda: DeltaTable(uri).optimize.compact(), "this table's next version")
    table = DeltaTable(uri)
    present = table_files(uri)
    keep, _oldest = skewed_versions(table, retention_hours(days))
    # VACUUM commits too (its start and end), so it can lose the same race; deleting again what is already
    # gone is a no-op, so it is safe to repeat on the table's latest version. A file it deletes may also be
    # open for a moment in a virus scanner or the indexer (os error 32 or 33 on Windows); that is waited out
    # the same way, and a file still held after that gets a plain message rather than delta-rs's.
    try:
        listed = retrying.local_commit(lambda: DeltaTable(uri).vacuum(
            retention_hours=retention_hours(days), dry_run=False, enforce_retention_duration=False, **vacuum_keep(keep)),
            "this table's next version", when=lambda e: retrying.windows_file_held(e) or retrying.windows_log_race(e))
    except OSError as exc:
        if not retrying.windows_file_held(exc):
            raise
        raise OSError(retrying.held_file_message("Compact")) from exc
    # Counted from what this VACUUM actually removed: delta-rs also lists a file already gone meanwhile.
    listed = [relative_to_table(uri, p) for p in listed]
    removed = [p for p in listed if path_key(p) in present and not (Path(uri) / p).exists()]
    # A data file no commit names (a write stopped before its commit) goes once it's as old as the
    # history kept, the age at which VACUUM removes any file: a write still under way never loses one.
    unnamed = _remove(uri, _unnamed(uri, table, retention_hours(days)))
    staged = _remove(uri, _stale_staged(uri))
    after = DeltaTable(uri)
    durable.sync_commits(uri, before + 1, after.version())  # the compacted files and their commits
    # files_before and files_after are counted from the Delta log, before and after: measured, not estimated.
    return {"files_added": metrics.get("numFilesAdded", 0), "files_removed": metrics.get("numFilesRemoved", 0),
            "files_before": files_before, "files_after": len(after.file_uris()),
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


def path_key(path: str) -> str:
    """A path relative to a table as this PC's disk compares it: forward slashes, and on Windows lower case
    (os.path.normcase, which there also turns every slash into a backslash, so they're turned back)."""
    return os.path.normcase(path.replace("\\", "/")).replace("\\", "/")


def table_files(uri: str) -> set[str]:
    """Every data file in a table folder on this PC (deletion vectors included, the log and linked folders
    left out), relative to the table, as path_key compares them (Windows paths ignore case)."""
    return {path_key(Path(e.path).relative_to(uri).as_posix()) for e in _data_folder_files(uri)}


def relative_to_table(uri: str, path: str) -> str:
    """``path`` as delta-rs lists it (whole, a percent-encoded file:// URL, or relative; either slash), relative
    to the table folder ``uri`` with forward slashes. Only a URL is decoded: a path is the file's name on disk,
    where a partition value is already escaped (region=North%20America is that folder's name)."""
    text = unquote(path[len("file://"):]) if path.startswith("file://") else path
    text = text.replace("\\", "/")
    root = uri.rstrip("/\\").replace("\\", "/") + "/"
    if os.path.normcase(text).startswith(os.path.normcase(root)):
        return text[len(root):]
    if text.startswith("/") and os.path.normcase(text[1:]).startswith(os.path.normcase(root)):  # file:///C:/...
        return text[1 + len(root):]
    return text


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
    the table folder: data files, log commits and checkpoint files a stopped write never put in place."""
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
    # Every spelling of a named file counts (as written, and decoded), compared as the disk does (on Windows,
    # ignoring case): a second spelling can only keep a file, never delete one, however a writer escaped it.
    named: set[str] = set()
    for p in cast(list[str], table.get_add_actions(flatten=True).column("path").to_pylist()):
        named |= {p, unquote(p)}
    # VACUUM lists a file by its name on disk (relative_to_table); its decoded spelling is kept too, so a
    # file a commit removed is never taken for a leftover however delta-rs spells it.
    for p in table.vacuum(retention_hours=0, dry_run=True, enforce_retention_duration=False):
        named |= {relative_to_table(uri, p), unquote(p)}
    named = {path_key(p) for p in named}
    out = []
    for e in _data_folder_files(uri):
        rel = Path(e.path).relative_to(uri).as_posix()
        if _DATA_FILE.fullmatch(e.name) and path_key(rel) not in named and _older_than(e, hours):
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
    file only an older version reads (time travel) counts as named too. Each in every spelling (as
    written, and decoded), as path_key compares them, as _unnamed counts them: a second spelling can only
    leave a file out of the report, never put one in."""
    named: set[str] = set()
    if table is not None:
        for p in cast(list[str], table.get_add_actions(flatten=True).column("path").to_pylist()):
            named |= {p, unquote(p)}
        # VACUUM lists a file by its name on disk (relative_to_table); decoding that name names another file.
        root = uri.rstrip("/") + "/"
        for p in table.vacuum(retention_hours=0, dry_run=True, enforce_retention_duration=False):
            named |= {relative_to_table(uri, p), unquote(p[len(root):] if p.startswith(root) else p)}
    log = Path(uri) / "_delta_log"
    if not log.is_dir():
        return {path_key(p) for p in named}
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
                        named |= {str(action[kind]["path"]), unquote(str(action[kind]["path"]))}
    return {path_key(p) for p in named}


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
        if (_DATA_FILE.fullmatch(e.name) or _STAGED_DATA.fullmatch(e.name)) and path_key(rel) not in named \
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
