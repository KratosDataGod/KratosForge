"""Delta change data feed reads on this PC: ``spark.read.option("readChangeFeed", "true")`` (WR-317).

A batch read of a table's changes between two versions or times, as Databricks and Fabric read them: each
changed row with ``_change_type`` (insert, update_preimage, update_postimage, delete), ``_commit_version`` and
``_commit_timestamp``, read from the table's own Delta log by delta-rs. What this PC can't read the same way
is refused by name: a table with column mapping (delta-rs reads its changes without the mapped names), and
time travel in the same read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pyarrow as pa

_OPTIONS = ("startingversion", "startingtimestamp", "endingversion", "endingtimestamp")
_STOP = "so nothing was read"


def asked(options: dict[str, Any]) -> bool:
    """Whether the reader's options (lower-case names) ask for the change data feed."""
    value = options.get("readchangefeed", options.get("readchangedata"))
    return str(value).strip().lower() == "true" if value is not None else False


def bounds(options: dict[str, Any], instant: Callable[[str], datetime] | None = None) -> dict[str, Any]:
    """delta-rs' load_cdf arguments for the reader's options. Spark's own errors where it has one. ``instant``
    reads a time with no zone in the session time zone (spark.sql.session.timeZone); without it, UTC."""
    given = {k: options[k] for k in _OPTIONS if options.get(k) is not None}
    if "startingversion" not in given and "startingtimestamp" not in given:
        raise ValueError("[DELTA_NO_START_FOR_CDC_READ] No startingVersion or startingTimestamp provided for CDC read, "
                         f"{_STOP}. Add .option(\"startingVersion\", 0) or .option(\"startingTimestamp\", ...).")
    for version, stamp in (("startingversion", "startingtimestamp"), ("endingversion", "endingtimestamp")):
        if version in given and stamp in given:
            side = version.removesuffix("version")
            raise ValueError(f"Give {side}Version or {side}Timestamp for the change data feed, not both, {_STOP}.")
    out: dict[str, Any] = {}
    for key in ("startingversion", "endingversion"):
        if key in given:
            out[key.replace("version", "_version")] = _version(key, given[key])
    for key in ("startingtimestamp", "endingtimestamp"):
        if key in given:
            out[key.replace("timestamp", "_timestamp")] = _timestamp(key, given[key], instant)
    if "starting_version" in out and "ending_version" in out and out["ending_version"] < out["starting_version"]:
        raise ValueError(f"[DELTA_INVALID_CDC_RANGE] CDC range from start {out['starting_version']} to end "
                         f"{out['ending_version']} was invalid. End cannot be before start.")
    return out


def _version(key: str, value: Any) -> int:
    name = {"startingversion": "startingVersion", "endingversion": "endingVersion"}[key]
    if str(value).strip().lower() == "latest" and key == "startingversion":
        raise ValueError(f"startingVersion latest is for a stream of changes, {_STOP}. A batch read starts at a "
                         "version number.")
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {value!r}, {_STOP}.") from None
    if number < 0:
        raise ValueError(f"{name} can't be negative, {_STOP}.")
    return number


def _timestamp(key: str, value: Any, instant: Callable[[str], datetime] | None) -> str:
    """An RFC 3339 time in UTC. A time with no zone is in the session time zone (``instant``), as Spark reads
    it; UTC, a Databricks or Fabric session's default, when no session is given."""
    name = {"startingtimestamp": "startingTimestamp", "endingtimestamp": "endingTimestamp"}[key]
    if isinstance(value, datetime) and value.tzinfo:
        when = value
    else:
        text = value.isoformat(sep=" ") if isinstance(value, datetime) else str(value).strip()
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))  # a date or a date and time, as Spark takes
            when = instant(text) if instant is not None else _utc(text)
        except ValueError:
            raise ValueError(f"{name} must be a date or a date and time like 2026-10-20 13:00:00, not {value!r}, "
                             f"{_STOP}.") from None
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc(text: str) -> datetime:
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read(opened: Any, name: str, options: dict[str, Any],
         instant: Callable[[str], datetime] | None = None) -> pa.Table:
    """The changes to ``opened`` (a deltalake DeltaTable, ``name`` for messages) the options ask for, as an Arrow
    table with the table's columns, then _change_type, _commit_version (a long) and _commit_timestamp (UTC)."""
    if options.get("versionasof") is not None or options.get("timestampasof") is not None:
        raise ValueError(f"A change data feed read of {name} can't take versionAsOf or timestampAsOf, because the "
                         f"feed is read between startingVersion and endingVersion instead, {_STOP}.")
    arguments = bounds(options, instant)
    configuration = {k.lower(): str(v).lower() for k, v in (opened.metadata().configuration or {}).items()}
    if configuration.get("delta.columnmapping.mode", "none") != "none":
        raise NotImplementedError(f"The change data feed of {name} can't be read on this PC the way Databricks and "
                                  f"Fabric read it, because the table uses column mapping, which this PC's Delta "
                                  f"reader doesn't apply to changes, {_STOP}. Read it in Fabric or on Databricks.")
    try:
        reader = opened.load_cdf(**arguments)
        table = pa.table(reader.read_all())
    except Exception as exc:  # noqa: BLE001 - delta-rs' own errors, said plainly
        raise ValueError(_said(str(exc), name, arguments, opened)) from None
    return _spark_types(table)


def _said(text: str, name: str, arguments: dict[str, Any], opened: Any) -> str:
    start = arguments.get("starting_version", arguments.get("starting_timestamp"))
    end = arguments.get("ending_version", arguments.get("ending_timestamp", "latest"))
    if "Invalid table version" in text:
        try:
            latest = f"; its latest version is {opened.version()}"
        except Exception:  # noqa: BLE001  # silent-ok: the message only leaves out the latest version
            latest = ""
        return (f"[DELTA_INVALID_CDC_RANGE] CDC range from start {start} to end {end} was invalid: {name} has no "
                f"version {start}{latest}, {_STOP}. DESCRIBE HISTORY {name} lists its versions.")
    if "does not have change data enabled" in text:
        return (f"[DELTA_MISSING_CHANGE_DATA] Error getting change data for range [{start} , {end}] as change data "
                f"was not recorded for a version in it, {_STOP}. Turn it on with ALTER TABLE {name} SET "
                "TBLPROPERTIES (delta.enableChangeDataFeed = true); changes are recorded from then on.")
    return f"Nothing was read from the change data feed of {name} from {start} to {end}: {text.strip()}"


def _spark_types(table: pa.Table) -> pa.Table:
    """Plain strings for delta-rs' string views, a long version and a microsecond timestamp, as Spark gives them."""
    fields = []
    for field in table.schema:
        kind = field.type
        if pa.types.is_string_view(kind):
            kind = pa.string()
        elif field.name == "_commit_version":
            kind = pa.int64()
        elif field.name == "_commit_timestamp" and pa.types.is_timestamp(kind):
            kind = pa.timestamp("us", tz="UTC")  # a Spark TIMESTAMP, as a Delta table's own timestamp columns read
        fields.append(pa.field(field.name, kind, field.nullable))
    return table.cast(pa.schema(fields))
