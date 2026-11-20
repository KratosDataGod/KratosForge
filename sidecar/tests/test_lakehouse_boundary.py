"""The attached lakehouse's table maintenance at its boundaries (mutation batch 205, a boundary re-score of
batch 2, docs/quality/mutation-testing.md): a VACUUM keeping exactly seven days of history is allowed, both
before the guard and against the table's own retention, and a VACUUM that commits no new version writes no
history line; RESTORE goes to version 0 but not to the current version, and a table the notebook creates
is recorded for a replay as having no version before it."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from tests.test_spark_writer import lake  # noqa: F401 - a fixture


@pytest.fixture
def guarded(lake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, table: None)
    return lake


def test_a_vacuum_keeping_exactly_seven_days_is_allowed(guarded):
    with pytest.raises(ValueError, match="at least 7 days"):
        lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=167.5, dry_run=True)
    done = lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=168, dry_run=True)
    assert done.metrics["retentionHours"] == 168 and done.metrics["dryRun"] is True


def test_a_vacuum_that_commits_nothing_writes_no_history_line(guarded, tmp_path):
    history = tmp_path / "history.jsonl"
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path, history_file=history))
    before = DeltaTable(str(guarded / "dbo" / "sales")).version()
    done = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert done.delta_version == before
    assert not history.exists() or history.read_text(encoding="utf-8").strip() == ""
    for i in range(3):  # small files, so OPTIMIZE has something to commit
        write_deltalake(str(guarded / "dbo" / "sales"), pa.table({"id": [10 + i], "region": ["EU"]}), mode="append")
    assert lakehouse.maintain_table("dbo.sales", "optimize").delta_version == before + 4
    [line] = history.read_text(encoding="utf-8").splitlines()  # a run that commits writes one
    assert json.loads(line)["mode"] == "optimize"


def test_restore_goes_to_version_0_but_not_to_the_current_version(guarded):
    path = str(guarded / "dbo" / "sales")
    write_deltalake(path, pa.table({"id": [7], "region": ["US"]}), mode="append")
    with pytest.raises(ValueError, match="has versions 0 to 1; RESTORE goes to an earlier one, not 1"):
        lakehouse.maintain_table("dbo.sales", "restore", version=1)
    assert lakehouse.maintain_table("dbo.sales", "restore", version=0).delta_version == 2
    assert sorted(r["id"] for r in DeltaTable(path).to_pyarrow_table().to_pylist()) == [1, 2, 3]


def test_a_table_the_notebook_creates_has_no_version_before_it_for_a_replay(lake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)  # these writes are a cell's own
    lakehouse.reads_reset()
    try:
        assert lakehouse.write_table(pa.table({"id": [1]}), "dbo.fresh").delta_version == 0
        assert lakehouse.write_table(pa.table({"id": [5], "region": ["US"]}), "dbo.sales").delta_version == 1
        assert lakehouse.reads["tables"]["lh1/dbo.fresh"]["version"] is None
        assert lakehouse.reads["tables"]["lh1/dbo.sales"]["version"] == 0
    finally:
        lakehouse.reads_reset()
