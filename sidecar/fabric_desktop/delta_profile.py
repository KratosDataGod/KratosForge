"""The conservative Delta profile Fabric readers handle reliably.

Fabric's SQL analytics endpoint and Direct Lake read plain Delta well, but
tables from external writers can carry features they misread. V2 checkpoints
also stop the lakehouse from listing a table. See
https://learn.microsoft.com/fabric/fundamentals/delta-lake-interoperability
"""

from __future__ import annotations

import logging
from typing import Any

from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

MAX_READER = 1
MAX_WRITER = 2

# Table properties that turn on features outside the profile.
_FORBIDDEN_CONFIG = {
    "delta.enableDeletionVectors": {"true"},
    "delta.columnMapping.mode": {"name", "id"},
    "delta.checkpointPolicy": {"v2"},
}


class ProfileViolation(ValueError):
    pass


def problems(table: DeltaTable) -> list[str]:
    """What puts the table outside the profile; empty when it is inside."""
    found: list[str] = []
    protocol = table.protocol()
    if protocol.min_reader_version > MAX_READER:
        found.append(f"reader version {protocol.min_reader_version} > {MAX_READER}")
    if protocol.min_writer_version > MAX_WRITER:
        found.append(f"writer version {protocol.min_writer_version} > {MAX_WRITER}")
    for features, kind in ((protocol.reader_features, "reader"), (protocol.writer_features, "writer")):
        if features:
            found.append(f"{kind} features {sorted(features)}")
    config = table.metadata().configuration or {}
    for key, bad in _FORBIDDEN_CONFIG.items():
        if str(config.get(key, "")).lower() in bad:
            found.append(f"{key}={config[key]}")
    return found


def check_table(table: DeltaTable) -> None:
    found = problems(table)
    if found:
        raise ProfileViolation("Table is outside the Fabric-safe Delta profile: " + "; ".join(found))


def check_target(uri: str, storage_options: dict[str, str] | None = None) -> None:
    """Refuse a write to an existing table outside the profile, before anything is committed.

    Checking only after the write would report a failure for rows that are already in the table,
    and a rerun would write them again. No table yet is fine: delta-rs creates one in the profile.
    """
    open_target(uri, storage_options)


def open_target(uri: str, storage_options: dict[str, str] | None = None) -> DeltaTable | None:
    """check_target, handing back the table it loaded (None when there is none yet), so the write
    can go through it rather than load the whole log again: on OneLake every load is a round trip
    per commit file."""
    try:
        table = DeltaTable(uri, storage_options=storage_options)
    except TableNotFoundError:
        return None
    check_table(table)
    return table


def note_after_write(table: DeltaTable, log: Any = None) -> list[str]:
    """After a commit, only report a table the write itself took outside the profile: the rows are
    committed, so raising would call a successful write failed."""
    found = problems(table)
    if found:
        (log or logging.getLogger(__name__)).warning(
            "The write committed, but the table is now outside the Fabric-safe Delta profile: %s",
            "; ".join(found))
    return found
