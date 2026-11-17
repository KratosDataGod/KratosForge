"""delta_profile.MATRIX says the same as the tables in docs/delta-reader-matrix.md, cell for cell, so the
"why" a refusal gives can't drift from the page people read."""

from __future__ import annotations

from pathlib import Path

from fabric_desktop import delta_profile as P

DOC = Path(__file__).resolve().parents[2] / "docs" / "delta-reader-matrix.md"
MAIN = ["sql_endpoint", "direct_lake", "fabric_spark", "duckdb", "databricks", "snowflake"]
OTHER = {"Python notebooks": "python_notebooks", "Pipelines": "pipelines", "Eventstreams": "eventstreams",
         "Lakehouse explorer and preview": "lakehouse_explorer", "Dataflows Gen2": "dataflows",
         "KQL databases": "kql"}
ROWS = {"Deletion vectors": P.DELETION_VECTORS, "Column mapping by name": P.CM_NAME,
        "Column mapping by ID": P.CM_ID, "V2 checkpoints": P.V2_CHECKPOINTS}


def _table(heading: str) -> list[list[str]]:
    section = DOC.read_text(encoding="utf-8").split(heading, 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[c.strip() for c in line.strip("|").split("|")] for line in lines[2:]]


def _cell(text: str) -> str:
    if text.startswith("Doesn't read"):
        return P.CANT
    if text.startswith("Unknown"):
        return P.UNKNOWN
    if text.startswith(("Reads", "Runtimes")):
        return P.READS
    raise AssertionError(f"a matrix cell this test can't read: {text!r}")


def test_the_main_table_matches_the_code():
    seen = set()
    for row in _table("## The matrix"):
        if row[0] not in ROWS:
            continue  # the version rows: "depends on the feature"
        feature = ROWS[row[0]]
        seen.add(feature)
        for reader, text in zip(MAIN, row[1:], strict=True):
            assert P.MATRIX[feature][reader] == _cell(text), (row[0], reader, text)
    assert seen == set(P.MATRIX)


def test_the_other_fabric_readers_table_matches_the_code():
    header = ["Deletion vectors", "Column mapping by name", "Column mapping by ID"]
    rows = _table("## Other Fabric readers")
    assert sorted(r[0] for r in rows) == sorted(OTHER)
    for row in rows:
        for column, text in zip(header, row[1:], strict=True):
            assert P.MATRIX[ROWS[column]][OTHER[row[0]]] == _cell(text), (row[0], column, text)


def test_every_reader_has_a_cell_for_every_feature():
    for feature, cells in P.MATRIX.items():
        assert set(cells) == set(P.READERS), feature
        assert set(cells.values()) <= {P.READS, P.CANT, P.UNKNOWN}
        assert feature in P.SOURCES
