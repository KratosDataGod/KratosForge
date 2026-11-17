"""One data-integrity test per row of the Delta reader matrix (docs/delta-reader-matrix.md).

Each case writes a real table that has the row's feature, then checks that the profile refuses
it, that the refusal names the feature (and, where the matrix knows them, the readers that can't
read it), and that a write to it commits nothing. A case's id starts with its matrix row, so a
failure says which row regressed. Each reason is asserted on its own, so removing any refusal
from delta_profile.py fails at least one case here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, TableFeatures, write_deltalake

from fabric_desktop import delta_profile as P
from fabric_desktop.meta.store import LocalStore


@pytest.fixture(autouse=True)
def _conservative(monkeypatch):
    """These cases pin the conservative (Fabric-safe) profile's rules. The default is the modern profile
    since 2026-11-17; test_delta_profile_modern.py covers it."""
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)


MATRIX = Path(__file__).resolve().parents[2] / "docs" / "delta-reader-matrix.md"

DV = "deletion vectors, which Fabric Python notebooks, pipelines and eventstreams can't read"
CM_NAME = "column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read"
CM_ID = ("column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, "
         "KQL databases, Python notebooks, pipelines and eventstreams can't read")
V2 = ("V2 checkpoints, which users report Fabric's SQL analytics endpoint refusing "
      "(Microsoft's documentation doesn't say)")


@dataclass(frozen=True)
class Case:
    row: str  # the matrix row, word for word
    how: str  # how the table gets the feature
    config: dict[str, str] | None = None  # table settings it is created with
    feature: str | None = None  # or a table feature added after it is created (a TableFeatures name)
    reasons: tuple[str, ...] = ()  # each must be in problems(); a reason ending in "[" is a prefix
    readers: str | None = None  # the readers the refusal names; None: none (the matrix says "depends")


CASES = [
    Case("Deletion vectors", "setting", config={"delta.enableDeletionVectors": "true"},
         reasons=("reader version 3 > 1", "writer version 7 > 2", "reader features [", "writer features [",
                  "delta.enableDeletionVectors=true"), readers=DV),
    Case("Deletion vectors", "feature", feature="DeletionVectors",
         reasons=("reader version 3 > 1", "writer version 7 > 2", "reader features [", "writer features ["),
         readers=DV),
    Case("Column mapping by name", "setting", config={"delta.columnMapping.mode": "name"},
         reasons=("reader version 2 > 1", "writer version 5 > 2", "delta.columnMapping.mode=name"), readers=CM_NAME),
    Case("Column mapping by ID", "setting", config={"delta.columnMapping.mode": "id"},
         reasons=("reader version 2 > 1", "writer version 5 > 2", "delta.columnMapping.mode=id"), readers=CM_ID),
    Case("V2 checkpoints", "setting", config={"delta.checkpointPolicy": "v2"},
         reasons=("delta.checkpointPolicy=v2",), readers=V2),
    Case("V2 checkpoints", "feature", feature="V2Checkpoint",
         reasons=("reader version 3 > 1", "writer version 7 > 2", "reader features [", "writer features ["),
         readers=V2),
    Case("Reader version above 1", "version only", config={"delta.minReaderVersion": "2", "delta.minWriterVersion": "5"},
         reasons=("reader version 2 > 1", "writer version 5 > 2")),
    Case("Reader version above 1", "another reader feature", feature="TimestampWithoutTimezone",
         reasons=("reader version 3 > 1", "writer version 7 > 2", "reader features [", "writer features [")),
    Case("Writer version above 2", "version only", config={"delta.minWriterVersion": "3"},
         reasons=("writer version 3 > 2",)),
    Case("Writer version above 2", "change data feed", config={"delta.enableChangeDataFeed": "true"},
         reasons=("writer version 4 > 2",)),
    Case("Writer version above 2", "a writer feature alone", feature="AppendOnly",
         reasons=("writer version 7 > 2", "writer features [")),
]
IDS = [f"{c.row} - {c.how}" for c in CASES]


def _matrix_rows() -> list[str]:
    """The first cell of each row of the reader matrix's own table."""
    text = MATRIX.read_text(encoding="utf-8")
    section = text.split("## The matrix", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|")[1].strip() for line in section.splitlines() if line.startswith("|")]
    return [r for r in rows[2:] if r]  # past the header and its --- line


def _make(path: Path, case: Case) -> DeltaTable:
    uri = str(path)
    write_deltalake(uri, pa.table({"id": [1], "v": ["a"]}), configuration=case.config)
    if case.feature:
        DeltaTable(uri).alter.add_feature(getattr(TableFeatures, case.feature), allow_protocol_versions_increase=True)
    return DeltaTable(uri)


def test_every_matrix_row_has_a_case_and_every_case_a_row():
    rows = _matrix_rows()
    assert rows, f"no rows found in {MATRIX}"
    assert sorted(rows) == sorted({c.row for c in CASES})


def test_every_refusal_rule_in_the_profile_has_a_case():
    """A rule added to delta_profile.py needs a case here too."""
    settings = {(k, v.lower()) for c in CASES for k, v in (c.config or {}).items()}
    for key, bad in P._FORBIDDEN_CONFIG.items():
        assert {(key, b) for b in bad} <= settings, key
    assert {(k, v) for k, v in P._UNREADABLE} <= settings
    features = {c.feature for c in CASES if c.feature}
    names = {"deletionVectors": "DeletionVectors", "v2Checkpoint": "V2Checkpoint"}
    assert {names[n] for n in P._FEATURE_SETTING} <= features


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_table_is_refused_and_the_refusal_names_the_feature_and_its_readers(tmp_path, case):
    table = _make(tmp_path / "t", case)
    found = P.problems(table)
    for reason in case.reasons:
        assert any(f.startswith(reason) if reason.endswith("[") else f == reason for f in found), (reason, found)
    with pytest.raises(P.ProfileViolation) as refused:
        P.check_table(table)
    expected = "Table is outside the Fabric-safe Delta profile: " + "; ".join(found)
    if case.readers is None:
        assert str(refused.value) == expected  # the matrix names no reader for this alone
    else:
        assert str(refused.value) == f"{expected}. It uses {case.readers}"


@pytest.mark.parametrize("mode", ["append", "overwrite"])
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_write_to_the_table_commits_nothing(tmp_path, case, mode):
    store = LocalStore(tmp_path, "dev")
    uri = store.uri("LH_Bronze", "dbo", "events")
    before = _make(Path(uri), case)
    version, files = before.version(), sorted(before.file_uris())
    with pytest.raises(P.ProfileViolation):
        store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2], "v": ["b"]}), mode=mode)
    after = DeltaTable(uri)
    assert after.version() == version  # nothing committed
    assert sorted(after.file_uris()) == files  # and no data file added to the table


def test_a_table_inside_the_profile_is_still_written(tmp_path):
    store = LocalStore(tmp_path, "dev")
    uri = store.uri("LH_Bronze", "dbo", "events")
    _make(Path(uri), Case("", "plain"))
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2], "v": ["b"]}), mode="append")
    assert sorted(DeltaTable(uri).to_pyarrow_table().column("id").to_pylist()) == [1, 2]


def test_the_matrix_rows_are_read_from_the_doc():
    """The row parser reads the matrix's own table, not the other Fabric readers' table under it."""
    rows = _matrix_rows()
    assert "Deletion vectors" in rows and "Python notebooks" not in rows
    assert all(not re.fullmatch(r"-+", r) for r in rows)
