"""Mutation batch 154, a re-score of preview.py from batch 54 (docs/quality/mutation-testing.md): the
row count of a table with deletion vectors takes off the rows its vectors delete."""

from __future__ import annotations

import shutil
from pathlib import Path

from deltalake import DeltaTable

from fabric_desktop import preview

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"


def test_the_row_count_takes_off_the_rows_deletion_vectors_remove(tmp_path):
    # Written by Spark with 10 rows; a DELETE left 3 of them in deletion vectors.
    shutil.copytree(FIXTURES / "dv", tmp_path / "dv")
    table = DeltaTable(str(tmp_path / "dv"))
    assert preview.row_count(table) == 7
    assert preview.table_preview(table)["row_count"] == 7
