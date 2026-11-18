"""The Delta profiles KratosForge writes to, and which readers can read each Delta feature.

A profile is the set of Delta features a table may use for KratosForge to write to it:

* ``CONSERVATIVE`` ("Fabric-safe"): reader version 1 and writer version 2 at most, no table features.
  Every Fabric item reads it.
* ``MODERN``: also deletion vectors and column mapping by name. Fabric's SQL analytics endpoint and
  Direct Lake read both; Fabric Python notebooks, pipelines and eventstreams don't.

``DEFAULT_PROFILE`` is the one switch for which profile applies when a write names none. Chris chose
the modern profile on 2026-11-17 ("new delta features should be on by default").

A table is also checked against its readers (``DEFAULT_READERS`` unless the write names them): a
feature that one of them can't read, or that no source says it reads, refuses the write, and the
refusal says which reader and why. The reader matrix is ``MATRIX``, from Microsoft's Delta Lake
interoperability page and the other sources in docs/delta-reader-matrix.md. See
https://learn.microsoft.com/fabric/fundamentals/delta-lake-interoperability
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

# The conservative profile's limits, under the names the rest of the engine and its tests use.
MAX_READER = 1
MAX_WRITER = 2

# Table properties that turn on features outside the conservative profile.
_FORBIDDEN_CONFIG = {
    "delta.enableDeletionVectors": {"true"},
    "delta.columnMapping.mode": {"name", "id"},
    "delta.checkpointPolicy": {"v2"},
}


# Who can't read each feature, from Microsoft's Delta Lake interoperability page (updated
# 2026-10-07) and the other sources in docs/delta-reader-matrix.md. Only readers a source names.
_UNREADABLE = {
    ("delta.enableDeletionVectors", "true"): ("deletion vectors, which Fabric Python notebooks, pipelines "
                                              "and eventstreams can't read"),
    ("delta.columnMapping.mode", "name"): ("column mapping by name, which Fabric Python notebooks, pipelines "
                                           "and eventstreams can't read"),
    ("delta.columnMapping.mode", "id"): ("column mapping by ID, which Fabric's SQL analytics endpoint, "
                                         "lakehouse explorer, Dataflows Gen2, KQL databases, Python notebooks, "
                                         "pipelines and eventstreams can't read"),
    ("delta.checkpointPolicy", "v2"): ("V2 checkpoints, which users report Fabric's SQL analytics endpoint "
                                       "refusing (Microsoft's documentation doesn't say)"),
}
# A feature named in the protocol, as the setting that turns it on. Column mapping isn't here: its mode
# (name or ID) decides who can't read it, and that is only in the table's settings.
_FEATURE_SETTING = {
    "deletionVectors": ("delta.enableDeletionVectors", "true"),
    "v2Checkpoint": ("delta.checkpointPolicy", "v2"),
}

# ---- The reader matrix

READERS = {
    "sql_endpoint": "Fabric's SQL analytics endpoint",
    "direct_lake": "Power BI Direct Lake",
    "fabric_spark": "Fabric Spark",
    "duckdb": "DuckDB",
    "databricks": "Databricks",
    "snowflake": "Snowflake",
    "python_notebooks": "Fabric Python notebooks",
    "pipelines": "Fabric pipelines",
    "eventstreams": "Fabric eventstreams",
    "lakehouse_explorer": "the lakehouse explorer",
    "dataflows": "Dataflows Gen2",
    "kql": "KQL databases",
}
# The readers a write checks when it names none: the ones the modern default is chosen for.
DEFAULT_READERS = ("sql_endpoint", "direct_lake")

READS, CANT, UNKNOWN = "reads", "can't read", "unknown"
DELETION_VECTORS = "deletion vectors"
CM_NAME = "column mapping by name"
CM_ID = "column mapping by ID"
V2_CHECKPOINTS = "V2 checkpoints"

# Each feature, per reader: READS, CANT or UNKNOWN (no source says). The same cells as the tables in
# docs/delta-reader-matrix.md (tests/test_delta_profile_readers_matrix.py checks they agree).
MATRIX: dict[str, dict[str, str]] = {
    DELETION_VECTORS: {
        "sql_endpoint": READS, "direct_lake": READS, "fabric_spark": READS, "duckdb": READS, "databricks": READS,
        "snowflake": READS, "python_notebooks": CANT, "pipelines": CANT, "eventstreams": CANT,
        "lakehouse_explorer": READS, "dataflows": READS, "kql": READS},
    CM_NAME: {
        "sql_endpoint": READS, "direct_lake": READS, "fabric_spark": READS, "duckdb": READS, "databricks": READS,
        "snowflake": UNKNOWN, "python_notebooks": CANT, "pipelines": CANT, "eventstreams": CANT,
        "lakehouse_explorer": READS, "dataflows": READS, "kql": READS},
    CM_ID: {
        "sql_endpoint": CANT, "direct_lake": READS, "fabric_spark": READS, "duckdb": READS, "databricks": READS,
        "snowflake": UNKNOWN, "python_notebooks": CANT, "pipelines": CANT, "eventstreams": CANT,
        "lakehouse_explorer": CANT, "dataflows": CANT, "kql": CANT},
    V2_CHECKPOINTS: {
        "sql_endpoint": UNKNOWN, "direct_lake": UNKNOWN, "fabric_spark": UNKNOWN, "duckdb": UNKNOWN,
        "databricks": READS, "snowflake": UNKNOWN, "python_notebooks": UNKNOWN, "pipelines": UNKNOWN,
        "eventstreams": UNKNOWN, "lakehouse_explorer": UNKNOWN, "dataflows": UNKNOWN, "kql": UNKNOWN},
}
# What a cell rests on, said in a refusal.
SOURCES = {
    DELETION_VECTORS: "Microsoft's Delta Lake interoperability page",
    CM_NAME: "Microsoft's Delta Lake interoperability page",
    CM_ID: "Microsoft's Delta Lake interoperability page and the SQL analytics endpoint's limitations",
    V2_CHECKPOINTS: "user reports; Microsoft's documentation doesn't say",
}


def features_used(table: DeltaTable) -> list[str]:
    """The matrix features the table uses, from its settings and its protocol's feature lists."""
    protocol = table.protocol()
    config = {k: str(v).lower() for k, v in (table.metadata().configuration or {}).items()}
    listed = set(protocol.reader_features or []) | set(protocol.writer_features or [])
    used = []
    if config.get("delta.enableDeletionVectors") == "true" or "deletionVectors" in listed:
        used.append(DELETION_VECTORS)
    mode = config.get("delta.columnMapping.mode", "")
    if mode == "name":
        used.append(CM_NAME)
    elif mode == "id":
        used.append(CM_ID)
    if config.get("delta.checkpointPolicy") == "v2" or "v2Checkpoint" in listed:
        used.append(V2_CHECKPOINTS)
    return used


def _names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def why(table: DeltaTable, readers: Iterable[str] = DEFAULT_READERS) -> list[str]:
    """For each feature the table uses that one of ``readers`` can't read (or that no source says it
    reads), one sentence that says which reader and on what source. Empty when every reader reads it."""
    return why_features(features_used(table), readers)


def why_features(features: Iterable[str], readers: Iterable[str] = DEFAULT_READERS, lead: str = "It uses") -> list[str]:
    """why() for a list of matrix features rather than a table's. ``lead`` starts each sentence: "It uses",
    or "This choice allows" for a table's setting that only allows them."""
    readers = list(dict.fromkeys(readers))  # a reader named twice is named once, in the order first given
    unknown = [r for r in readers if r not in READERS]
    if unknown:
        raise ValueError(f"Unknown Delta reader {unknown[0]!r}. Use one of {', '.join(READERS)}.")
    out = []
    for feature in features:
        cells = MATRIX[feature]
        cant = [READERS[r] for r in readers if cells[r] == CANT]
        unsure = [READERS[r] for r in readers if cells[r] == UNKNOWN]
        if cant:
            out.append(f"{lead} {feature}, which {_names(cant)} can't read ({SOURCES[feature]}).")
        if unsure:
            out.append(f"{lead} {feature}, and no source we found says whether {_names(unsure)} can read it "
                       f"({SOURCES[feature]}).")
    return out


def broken_by(features: Iterable[str], readers: Iterable[str]) -> list[str]:
    """The reader keys in ``readers`` that can't read one of ``features``, or that no source says read it."""
    features = list(features)
    return [r for r in readers if any(MATRIX[f][r] != READS for f in features)]


# ---- Profiles


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    max_reader: int
    max_writer: int
    features: frozenset[str]  # protocol table features a table may list
    forbidden: dict[str, set[str]]  # table settings it may not have


CONSERVATIVE = Profile("conservative", "Fabric-safe", MAX_READER, MAX_WRITER, frozenset(), _FORBIDDEN_CONFIG)
# appendOnly and invariants are the writer features a writer version 7 table lists for the legacy
# behaviour it keeps (Spark lists them on every such table); delta-rs honours both when it writes.
MODERN = Profile("modern", "modern", 3, 7,
                 frozenset({"deletionVectors", "columnMapping", "appendOnly", "invariants"}),
                 {"delta.columnMapping.mode": {"id"}, "delta.checkpointPolicy": {"v2"}})
PROFILES = {p.name: p for p in (CONSERVATIVE, MODERN)}

# The one switch: the profile a write gets when it names none.
DEFAULT_PROFILE = MODERN


# ---- Each table's own choice (delta_features in a metadata project's entity file, Preview)

# A table that allows only one of the modern profile's two features.
DELETION_VECTORS_ONLY = Profile("deletion_vectors", "deletion vectors only", 3, 7,
                                frozenset({"deletionVectors", "appendOnly", "invariants"}),
                                {"delta.columnMapping.mode": {"name", "id"}, "delta.checkpointPolicy": {"v2"}})
COLUMN_MAPPING_ONLY = Profile("column_mapping", "column mapping only", 3, 7,
                              frozenset({"columnMapping", "appendOnly", "invariants"}),
                              {"delta.enableDeletionVectors": {"true"}, "delta.columnMapping.mode": {"id"},
                               "delta.checkpointPolicy": {"v2"}})
# Each delta_features value and the profile its table is written with. No value means DEFAULT_PROFILE.
TABLE_CHOICES = {"every_reader": CONSERVATIVE, "deletion_vectors": DELETION_VECTORS_ONLY,
                 "column_mapping": COLUMN_MAPPING_ONLY}
TABLE_KEYS = ("delta_features", "delta_readers", "delta_accepted")
# The protocol feature a profile lists, as the matrix feature it allows. Column mapping by ID never is.
_ALLOWS = {"deletionVectors": DELETION_VECTORS, "columnMapping": CM_NAME}


def allowed_features(p: Profile) -> list[str]:
    """The matrix features a table written with profile ``p`` may use."""
    return [feature for name, feature in _ALLOWS.items() if name in p.features]


def table_profile(choice: str | None) -> Profile:
    """The profile for a table's delta_features. None (no setting) is DEFAULT_PROFILE."""
    if choice is None:
        return DEFAULT_PROFILE
    if choice not in TABLE_CHOICES:
        raise ValueError(_unknown_choice(choice))
    return TABLE_CHOICES[choice]


def _unknown_choice(choice: Any) -> str:
    return (f"delta_features is {choice!r}, which isn't a choice KratosForge knows. Use one of "
            f"{', '.join(TABLE_CHOICES)}, or leave it out for the default.")


def table_readers(spec: dict[str, Any]) -> tuple[str, ...]:
    """The readers an entity file names for its table, or DEFAULT_READERS when it names none."""
    readers = spec.get("delta_readers")
    return tuple(readers) if readers else DEFAULT_READERS


def broken_readers(choice: str | None, readers: Iterable[str]) -> list[str]:
    """The readers a table's delta_features choice would break."""
    return broken_by(allowed_features(table_profile(choice)), readers)


def choice_reasons(choice: str | None, readers: Iterable[str]) -> list[str]:
    """Why a table's delta_features choice breaks one of ``readers``: one sentence per feature, as why() says it."""
    return why_features(allowed_features(table_profile(choice)), readers, "This choice allows")


def choice_problems(spec: dict[str, Any]) -> list[str]:
    """What is wrong with an entity file's delta_features, delta_readers and delta_accepted, in plain
    sentences: a value KratosForge doesn't know, or a choice that breaks a reader nobody confirmed.
    Empty when they are right, or absent as in a file from before them."""
    out = value_problems(spec)
    if out:
        return out
    choice = spec.get("delta_features")
    readers, accepted = table_readers(spec), list(spec.get("delta_accepted") or [])
    stray = [r for r in accepted if r not in readers]
    if stray:
        out.append(f"delta_accepted names {stray[0]}, which isn't one of this table's delta_readers. Take it out.")
    unconfirmed = [r for r in broken_readers(choice, readers) if r not in accepted]
    if unconfirmed:
        out.append(f"Its Delta table features would break {READERS[unconfirmed[0]]}, one of its readers, and nobody "
                   "confirmed that. Choose the setting again on the Environments tab and type the table's name "
                   "to allow it.")
    return out


def value_problems(spec: dict[str, Any]) -> list[str]:
    """choice_problems' first half: a delta_ setting whose value KratosForge doesn't know."""
    out = []
    choice = spec.get("delta_features")
    if choice is not None and (not isinstance(choice, str) or choice not in TABLE_CHOICES):
        out.append(_unknown_choice(choice))
    for key in ("delta_readers", "delta_accepted"):
        value = spec.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
            out.append(f'{key} is a list of reader names, like {key} = ["sql_endpoint", "direct_lake"].')
            continue
        unknown = [r for r in value if r not in READERS]
        if unknown:
            out.append(f"{key} names {unknown[0]!r}, which isn't a reader KratosForge knows. "
                       f"Use any of {', '.join(READERS)}.")
        elif key == "delta_readers" and not value:
            out.append("delta_readers names no reader. Name at least one, or leave it out for the SQL analytics "
                       "endpoint and Direct Lake.")
    return out


class ConfirmNeeded(ValueError):
    """A table's Delta feature choice breaks one of its readers, and the save didn't carry the table's
    name typed to allow it. ``reasons`` says which reader, which feature and on what source."""

    def __init__(self, message: str, name: str, reasons: list[str]):
        super().__init__(message)
        self.name, self.reasons = name, reasons


def confirm_choice(name: str, old: dict[str, Any], new: dict[str, Any], confirm_name: str | None) -> list[str] | None:
    """Check a save of entity ``name`` whose file goes from ``old`` to ``new``. None when its delta_
    settings didn't change; else the readers its choice breaks, which the save records as delta_accepted
    (empty when it breaks none). ValueError for a value KratosForge doesn't know, and ConfirmNeeded for a
    choice that breaks a reader without ``confirm_name`` equal to ``name``. Nothing is saved then."""
    problems = value_problems(new)
    if problems:
        raise ValueError(f"{name} wasn't saved, because " + problems[0][0].lower() + problems[0][1:])

    def key(spec: dict[str, Any]) -> tuple[Any, ...]:
        return spec.get("delta_features"), table_readers(spec), sorted(spec.get("delta_accepted") or [])
    if not value_problems(old) and key(old) == key(new):
        return None
    choice, readers = new.get("delta_features"), table_readers(new)
    broken = broken_readers(choice, readers)
    if not broken:
        return []
    reasons = choice_reasons(choice, readers)
    typed = (confirm_name or "").strip()
    if not typed:
        raise ConfirmNeeded(f"{name} wasn't saved, because its Delta table features would break one of its readers. "
                            + " ".join(reasons) + f" Type {name} to allow it.", name, reasons)
    if typed != name:
        raise ConfirmNeeded(f"{name} wasn't saved, because the name typed to allow it was {typed}, not {name}. "
                            + " ".join(reasons) + f" Type {name} exactly to allow it.", name, reasons)
    return broken


def table_policy(spec: dict[str, Any]) -> tuple[Profile, tuple[str, ...]]:
    """The profile and readers a write to an entity's table is checked with. A reader whose break was
    confirmed (delta_accepted) isn't checked again. A file with a wrong setting is written with the
    conservative profile, which every reader reads, and its save gate says what is wrong."""
    if choice_problems(spec):
        return CONSERVATIVE, DEFAULT_READERS
    accepted = set(spec.get("delta_accepted") or [])
    return table_profile(spec.get("delta_features")), tuple(r for r in table_readers(spec) if r not in accepted)


def profile(name: str | Profile | None) -> Profile:
    if name is None:
        return DEFAULT_PROFILE
    if isinstance(name, Profile):
        return name
    if name not in PROFILES:
        raise ValueError(f"Unknown Delta profile {name!r}. Use {' or '.join(PROFILES)}.")
    return PROFILES[name]


# What each legacy writer version past 2 adds, in order (Delta protocol): version 3 check constraints,
# 4 change data feed and generated columns, 5 column mapping, 6 identity columns.
_LEGACY_WRITER_FEATURES = (("check constraints",), ("change data feed", "generated columns"), ("column mapping",),
                           ("identity columns",))


class ProfileViolation(ValueError):
    pass


def problems(table: DeltaTable, within: str | Profile | None = None) -> list[str]:
    """What puts the table outside the profile; empty when it is inside."""
    p = profile(within)
    found: list[str] = []
    protocol = table.protocol()
    if protocol.min_reader_version > p.max_reader:
        found.append(f"reader version {protocol.min_reader_version} > {p.max_reader}")
    if protocol.min_writer_version > p.max_writer:
        found.append(f"writer version {protocol.min_writer_version} > {p.max_writer}")
    elif p.max_writer >= 7 and MAX_WRITER < protocol.min_writer_version < 7:
        # Writer versions 3 to 6 turn on features without listing them (check constraints, change data
        # feed, generated columns, column mapping, identity columns), so the feature check below can't
        # see them. Only version 7 lists its features.
        names = [f for step in _LEGACY_WRITER_FEATURES[: protocol.min_writer_version - MAX_WRITER] for f in step]
        implied = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        found.append(f"writer version {protocol.min_writer_version}, which turns on {implied} without listing them")
    for features, kind in ((protocol.reader_features, "reader"), (protocol.writer_features, "writer")):
        outside = sorted(set(features or []) - p.features)
        if outside:
            found.append(f"{kind} features {outside}")
    config = table.metadata().configuration or {}
    for key, bad in p.forbidden.items():
        if str(config.get(key, "")).lower() in bad:
            found.append(f"{key}={config[key]}")
    return found


def unreadable_by(table: DeltaTable) -> list[str]:
    """The table's features outside the conservative profile, each with the readers that can't read it."""
    protocol = table.protocol()
    config = table.metadata().configuration or {}
    settings = [(key, str(config.get(key, "")).lower()) for key, _ in _UNREADABLE]
    features = set(protocol.reader_features or []) | set(protocol.writer_features or [])
    settings += [setting for name, setting in _FEATURE_SETTING.items() if name in features]
    notes: list[str] = []
    for setting in settings:
        note = _UNREADABLE.get(setting)
        if note and note not in notes:
            notes.append(note)
    return notes


def check_table(table: DeltaTable, within: str | Profile | None = None,
                readers: Iterable[str] | None = None, committed: bool = False) -> None:
    """Refuse a table outside the profile, or one that uses a feature one of its readers can't read.

    ``committed`` says the check runs after the write's commit, so the refusal can't say nothing was written."""
    p = profile(within)
    found = problems(table, p)
    if found:
        message = f"Table is outside the {p.label} Delta profile: " + "; ".join(found)
        notes = unreadable_by(table)
        if notes:
            message += ". It uses " + "; and ".join(notes)
        raise ProfileViolation(message)
    if p is CONSERVATIVE:
        return  # it uses none of the matrix's features
    reasons = why(table, DEFAULT_READERS if readers is None else readers)
    if reasons:
        if committed:
            raise ProfileViolation("The write committed, but one of this table's readers can't read a feature the "
                                   "table now uses. " + " ".join(reasons) + " If it was an append, don't run it "
                                   "again, because that would add its rows twice. Check the table from Fabric Spark.")
        raise ProfileViolation("Nothing was written to this table, because one of its readers can't read a feature "
                               "it uses. " + " ".join(reasons) + " Keep writing this table from Fabric Spark.")


def check_target(uri: str, storage_options: dict[str, str] | None = None) -> None:
    """Refuse a write to an existing table outside the profile, before anything is committed.

    Checking only after the write would report a failure for rows that are already in the table,
    and a rerun would write them again. No table yet is fine: delta-rs creates one in the profile.
    """
    open_target(uri, storage_options)


def open_target(uri: str, storage_options: dict[str, str] | None = None,
                within: str | Profile | None = None, readers: Iterable[str] | None = None) -> DeltaTable | None:
    """check_target, handing back the table it loaded (None when there is none yet), so the write
    can go through it rather than load the whole log again: on OneLake every load is a round trip
    per commit file."""
    try:
        table = DeltaTable(uri, storage_options=storage_options)
    except TableNotFoundError:
        return None
    check_table(table, within, readers)
    return table


def note_after_write(table: DeltaTable, log: Any = None, within: str | Profile | None = None) -> list[str]:
    """After a commit, only report a table the write itself took outside the profile: the rows are
    committed, so raising would call a successful write failed."""
    p = profile(within)
    found = problems(table, p)
    if found:
        (log or logging.getLogger(__name__)).warning(
            "The write committed, but the table is now outside the %s Delta profile: %s", p.label, "; ".join(found))
    return found
