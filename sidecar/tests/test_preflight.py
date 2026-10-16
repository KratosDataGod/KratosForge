"""Before a run reads a Delta source: an estimate from the Delta log, and a memory guard."""

import datetime as dt

import polars as pl
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop.meta import Engine, Project
from fabric_desktop.meta.preflight import Estimate, available_memory, estimate, too_big
from test_large_tables import DAY1, DAY2, Setup, entity, rows


def source(tmp_path, days=3, per_day=1000):
    path = tmp_path / "src"
    for i in range(days):
        write_deltalake(str(path), rows(range(i * per_day, (i + 1) * per_day), DAY1 + dt.timedelta(days=i)).to_arrow(),
                        mode="append")
    return DeltaTable(str(path))


def test_the_estimate_counts_only_files_after_the_watermark(tmp_path):
    table = source(tmp_path)
    schema = dict(pl.scan_delta(table).collect_schema())
    everything = estimate(table, list(schema), schema)
    assert (everything.files, everything.files_total, everything.rows) == (3, 3, 3000) and everything.exact_rows
    assert everything.memory_bytes == 3000 * (8 + 32 + 8 + 4)  # id, name, amount, modified
    late = estimate(table, ["id", "amount"], schema, ("modified", DAY1 + dt.timedelta(days=1)))
    assert (late.files, late.rows, late.memory_bytes) == (1, 1000, 1000 * 16)
    assert late.bytes_on_disk < everything.bytes_on_disk / 3
    assert late.text().startswith("about 1,000 rows in 1 of 3 files (")


def test_the_guard_explains_what_to_do_instead():
    est = Estimate(files=40, files_total=40, rows=20_000_000, bytes_on_disk=2 << 30, memory_bytes=6 << 30)
    assert too_big(est, None) == ""  # can't tell: no guard
    assert too_big(est, 20 << 30) == ""
    why = too_big(est, 8 << 30)
    assert "needs about 18.0 GB" in why and "8.0 GB free" in why and "memory_guard = false" in why


def test_free_memory_is_known_on_linux_and_windows():
    free = available_memory()
    assert free is None or free > 0


def test_a_run_shows_the_estimate_and_stops_when_it_cannot_fit(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(1000), DAY1))
    res = s.run()
    assert "about 1,000 rows in 1 of 1 file (" in next(st.detail for st in res.steps if st.name == "read")
    s.source(rows(range(1000, 1010), DAY2))
    engine = s.engine(DAY2)
    engine.available_memory = lambda: 1000  # a machine with 1 KB free
    res = engine.run("bronze.events")
    assert res.status != "ok" and "this machine has" in res.message and "rows in 1 of 2 files" in res.message
    assert s.table().height == 1000  # nothing was written


def test_an_entity_can_turn_the_guard_off(tmp_path):
    s = Setup(tmp_path, True)
    text = (s.root / "metadata" / "entities" / "bronze.events.toml").read_text(encoding="utf-8")
    entity(s.root, "bronze.events", text.replace('watermark = "modified"', 'watermark = "modified"\nmemory_guard = false'))
    s.source(rows(range(10), DAY1))
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.available_memory = lambda: 1000
    assert engine.run("bronze.events").status == "ok"


@pytest.mark.parametrize("dtype, width", [(pl.Boolean, 1), (pl.Int32, 4), (pl.Datetime("us"), 8), (pl.String, 32)])
def test_column_widths(dtype, width):
    from fabric_desktop.meta.preflight import _width
    assert _width(dtype) == width


def test_the_run_dialog_can_ask_before_running(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(1000), DAY1))
    s.run()
    s.source(rows(range(1000, 1100), DAY2))
    engine = s.engine(DAY2)
    engine.available_memory = lambda: 1000
    ahead = engine.preflight("bronze.events")
    assert ahead["estimate"]["rows"] == 100 and ahead["estimate"]["files"] == 1 and ahead["guard"]
    assert "this machine has" in ahead["stop"] and "after 2026-10-01" in ahead["detail"]
    assert engine.preflight("bronze.events", backfill="full")["estimate"]["rows"] == 1100
    assert s.table().height == 1000  # asking ran nothing
