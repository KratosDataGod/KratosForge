"""Mutation testing batch 3: pins for a local data project's engine (localdata/engine.py) that its own
tests left open. See docs/quality/mutation-testing.md."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from pathlib import Path

import duckdb
import polars as pl
import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_profile, durable
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("Mut")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id, 10 AS amount")
    return e


def _rounded(seconds):
    return isinstance(seconds, float) and seconds >= 0 and seconds == round(seconds, 3)


# Running statements


def test_a_limit_of_zero_returns_no_rows(engine):
    out = engine.execute("SELECT 1 AS a UNION ALL SELECT 2", limit=0)
    assert out["rows"] == [] and out["truncated"] is True and out["row_count"] == 2


def test_each_statement_is_numbered_from_one_with_rounded_seconds(engine):
    out = engine.execute("CREATE TEMP TABLE t AS SELECT 1 AS a; SELECT * FROM t")
    assert [s["statement"] for s in out["statements"]] == [1, 2]
    assert all(_rounded(s["seconds"]) for s in out["statements"])


def test_a_statement_duckdb_cant_read_in_a_script_is_a_plain_value_error(engine):
    with pytest.raises(ValueError) as caught:
        engine.execute("SELECT 1; SELEC 2")
    assert not isinstance(caught.value, duckdb.Error)
    assert "syntax error" in str(caught.value).lower()


def test_make_table_and_change_delta_round_their_seconds(engine):
    made = engine.make_table("dbo.plain", "SELECT 1 AS a")
    assert made["rows"] == 1 and _rounded(made["seconds"])
    changed = engine.change_delta("UPDATE sales SET amount = 11 WHERE id = 1")
    assert changed["rows_updated"] == 1 and _rounded(changed["seconds"])


# Delta writes and changes


def test_a_write_whose_query_fails_in_duckdb_raises_duckdbs_own_error(engine):
    with pytest.raises(duckdb.Error) as caught:
        engine.write_delta("dbo.bad", "SELECT CAST('x' AS INTEGER) AS a")
    assert "Writing dbo.bad failed" not in str(caught.value)


def test_a_change_to_a_table_fabric_couldnt_read_is_refused(engine, monkeypatch):
    def refuse(table):
        raise delta_profile.ProfileViolation("it uses a feature Fabric doesn't read")

    monkeypatch.setattr(delta_profile, "check_table", refuse)
    with pytest.raises(ValueError, match=r"dbo\.sales isn't changed here: it uses a feature"):
        engine.change_delta("UPDATE sales SET amount = 11 WHERE id = 1")
    monkeypatch.undo()
    assert engine.execute("SELECT amount FROM sales")["rows"] == [[10]]


def test_a_change_flushes_exactly_the_versions_it_committed(engine, monkeypatch):
    seen = []
    real = durable.sync_commits

    def spy(uri, first, last):
        seen.append((first, last))
        return real(uri, first, last)

    monkeypatch.setattr(durable, "sync_commits", spy)
    before = engine.delta_history("dbo.sales")["version"]
    engine.change_delta("UPDATE sales SET amount = 12 WHERE id = 1")
    assert (before + 1, before + 1) in seen


def test_history_takes_at_least_one_version_and_says_the_table_is_its_own(engine):
    engine.execute("INSERT INTO sales VALUES (2, 20)")
    h = engine.delta_history("sales", limit=0)
    assert len(h["versions"]) == 1 and h["versions"][0]["version"] == h["version"]
    assert h["own"] is True


def test_reading_as_of_a_time_before_the_first_version_says_when_that_was(engine):
    folder = engine.delta_history("sales")["folder"]
    first_ms = min(h["timestamp"] for h in DeltaTable(folder).history())
    before = dt.datetime.fromtimestamp(first_ms / 1000 - 1, dt.timezone.utc).isoformat()
    with pytest.raises(ValueError, match="its first is from"):
        eng._load(folder, None, before, "dbo.sales")


# Registered folders


@pytest.fixture
def files(local, engine, tmp_path):
    root = tmp_path / "files"
    for name, n in (("big", 51), ("fifty", 50)):
        (root / name).mkdir(parents=True)
        for i in range(n):
            pl.DataFrame({"id": [i]}).write_parquet(root / name / f"part-{i:03d}.parquet")
    local.add_source(engine.project.id, str(root))
    return root


def test_a_folder_table_lists_up_to_fifty_paths(engine, files):
    by_name = {t["table"]: t for t in engine.tables() if t.get("source")}
    assert by_name["fifty"]["files"] == 50 and len(by_name["fifty"]["paths"]) == 50
    assert by_name["big"]["files"] == 51 and len(by_name["big"]["paths"]) == 1


def test_a_folder_of_parquet_files_has_no_versions(engine, files):
    with pytest.raises(ValueError, match=r"isn't a Delta table \(parquet\), so it has no versions"):
        engine.delta_history("big")


def test_an_automatic_look_doesnt_force_a_full_refresh(engine, files, monkeypatch):
    forced = []
    monkeypatch.setattr(engine, "_refresh_own", lambda: forced.append(1))
    engine._last_check = -1e9  # long ago
    engine.refresh_if_changed()
    assert forced == []
    engine.refresh()
    assert forced == [1]


# Small helpers


def test_spill_leftovers_skip_folders_and_links(tmp_path, caplog):
    (tmp_path / "duckdb_temp_storage-0.tmp").write_text("x", encoding="utf-8")
    (tmp_path / "duckdb_temp_dir").mkdir()
    keep = tmp_path / "elsewhere.bin"
    keep.write_text("keep", encoding="utf-8")
    link = tmp_path / "duckdb_temp_link"
    if os.name != "nt":
        link.symlink_to(keep)
    caplog.set_level(logging.INFO, logger=eng.__name__)
    eng._remove_leftover_spill(tmp_path)
    assert not (tmp_path / "duckdb_temp_storage-0.tmp").exists()
    assert (tmp_path / "duckdb_temp_dir").is_dir()
    assert not [r for r in caplog.records if "Couldn't remove" in r.getMessage()]
    if os.name != "nt":
        assert link.is_symlink() and keep.read_text(encoding="utf-8") == "keep"


def test_room_for_data_file_paths_counts_64_and_three_per_partition(monkeypatch):
    import fabric_desktop.support_bundle as sb
    monkeypatch.setattr(sb, "long_paths_enabled", lambda: False)
    monkeypatch.setattr(eng.os.path, "abspath", lambda p: str(p))
    base = "/" + "a" * 189  # 190 characters, + 64, + 5 for the partition "ab": 259
    eng._check_path_room("t", Path(base), ["ab"])
    with pytest.raises(ValueError, match="about 260 characters"):
        eng._check_path_room("t", Path(base + "x"), ["ab"])
    with pytest.raises(ValueError, match="about 260 characters"):
        eng._check_path_room("t", Path(base[:-4]), ["ab", "cd"])  # 186 + 64 + 5 + 5


def test_an_unreadable_log_counts_as_maybe_committed(tmp_path):
    assert eng._committed(tmp_path / "no-table", 0, "q") is True


def test_the_fabric_check_lists_twenty_findings_at_most(monkeypatch):
    import fabric_desktop.sqlcompat as sc
    report = {"target": {"id": "warehouse"}, "outcome": "warn", "counts": {"warning": 25},
              "diagnostics": [{"severity": "warning", "rule": f"R{i}", "title": "t", "line": i, "col": 1}
                              for i in range(25)]}
    monkeypatch.setattr(sc, "analyze", lambda sql, target: report)
    out = eng._fabric_check("SELECT 1")
    assert len(out["findings"]) == 20 and out["findings"][-1]["rule"] == "R19"


def test_collecting_no_result():
    assert eng._collect(None, 10, True) == {"columns": [], "rows": [], "row_count": 0, "truncated": False}


def test_short_messages_stop_at_a_thousand_characters():
    assert eng._short(ValueError("x" * 1500)) == "x" * 1000
    assert eng._short(ValueError("")) == "ValueError"


# Explain


def test_a_plan_renders_estimates_and_indents_children():
    raw = [{"name": "PROJECTION", "extra_info": {"Estimated Cardinality": "~5", "Projections": "a"},
            "children": [{"name": "SCAN", "children": []}]}]
    out = eng._explained(raw, "plan")
    assert out["plan"][0]["estimated_rows"] == 5 and out["plan"][0]["details"] == {"Projections": "a"}
    assert out["plan"][0]["children"][0]["details"] == {} and out["plan"][0]["children"][0]["estimated_rows"] is None
    assert out["text"] == "PROJECTION  ~5 rows\n  SCAN"


def test_an_analyzed_plan_keeps_its_totals_counts_and_shares():
    raw = {"latency": 0.5, "cpu_time": 0.25, "cumulative_rows_scanned": 10, "system_peak_buffer_memory": 2048,
           "system_peak_temp_dir_size": 4096, "total_bytes_read": 99,
           "children": [{"operator_name": "PROJECTION", "operator_timing": 0, "operator_cardinality": 3,
                         "operator_rows_scanned": 7, "extra_info": {"Estimated Cardinality": "5"},
                         "children": [{"operator_name": "SCAN", "children": []}]}]}
    out = eng._explained(raw, "analyze")
    assert out["totals"]["peak_memory_bytes"] == 2048 and out["totals"]["spilled_bytes"] == 4096
    assert out["hints"] and "spilled 4,096 bytes" in out["hints"][0]
    top, scan = out["plan"][0], out["plan"][0]["children"][0]
    assert (top["rows"], top["rows_scanned"], top["estimated_rows"]) == (3, 7, 5)
    assert (scan["rows"], scan["rows_scanned"]) == (0, 0)
    assert all(s["share"] == 0.0 for s in out["slowest"])  # no time spent: no division by zero
    assert out["text"] == "PROJECTION  rows=3  0.0000s\n  SCAN  rows=0  0.0000s"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in the stand-in")
def test_path_room_is_not_checked_when_long_paths_are_on_or_unknown(monkeypatch):
    import fabric_desktop.support_bundle as sb
    for answer in (True, None):
        monkeypatch.setattr(sb, "long_paths_enabled", lambda a=answer: a)
        eng._check_path_room("t", Path("/" + "a" * 400), [])


def test_a_script_with_a_delta_write_is_checked_before_anything_runs(engine):
    with pytest.raises(ValueError) as caught:
        engine.execute("CREATE TABLE early USING DELTA AS SELECT 1 AS a; SELEC 2")
    assert not isinstance(caught.value, duckdb.Error) and "syntax error" in str(caught.value).lower()
    assert "early" not in {t["table"] for t in engine.tables()}  # the write before it never ran


def test_a_dry_run_says_which_value_doesnt_fit_in_its_own_words(engine):
    engine.execute("CREATE TABLE tiny USING DELTA AS SELECT 1::TINYINT AS a")
    with pytest.raises(ValueError) as caught:
        engine.write_delta("dbo.tiny", "SELECT 1000 AS a", mode="append", dry_run=True)
    assert str(caught.value).startswith("a value in column a doesn't fit its type int8")


def test_write_delta_rounds_its_seconds(engine):
    out = engine.write_delta("dbo.fresh", "SELECT 1 AS a")
    assert out["rows"] == 1 and _rounded(out["seconds"])


def test_a_script_mixing_a_delta_write_and_a_query_runs_both(engine):
    out = engine.execute("CREATE TABLE mixed USING DELTA AS SELECT 7 AS a; SELECT a * 2 AS b FROM mixed")
    assert out["rows"] == [[14]] and [s["statement"] for s in out["statements"]] == [1, 2]
