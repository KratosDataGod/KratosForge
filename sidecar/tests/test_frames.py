"""The result viewer behind a notebook table output: paging, search, sort and downloads (frames.py)."""

import datetime as dt
import sys
import zipfile
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop import frames
from fabric_desktop.frames import FrameGone, Frames
from fabric_desktop.notebook import PREVIEW_ROWS, Kernel


def _run(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "ok", out
    return out["outputs"]


def test_table_output_points_at_the_whole_result():
    k = Kernel()
    (out,) = _run(k, "import polars as pl\npl.DataFrame({'n': range(5000), 's': [f'r{i}' for i in range(5000)]})")
    assert out["type"] == "table" and len(out["rows"]) == PREVIEW_ROWS and out["types"] == ["integer", "string"]
    page = k.frame_page(out["frame"], offset=4900, limit=100)
    assert page["total"] == 5000 and page["rows"][0] == [4900, "r4900"] and len(page["rows"]) == 100


def test_search_and_sort_cover_every_row_not_just_the_preview():
    k = Kernel()
    (out,) = _run(k, "import polars as pl\npl.DataFrame({'n': range(3000), 's': [f'r{i}' for i in range(3000)]})")
    hit = k.frame_page(out["frame"], q="r2999")
    assert hit["total"] == 1 and hit["rows"] == [[2999, "r2999"]]
    top = k.frame_page(out["frame"], sort=0, desc=True, limit=1)
    assert top["rows"] == [[2999, "r2999"]]
    # LIKE wildcards in the search are literal text
    assert k.frame_page(out["frame"], q="%")["total"] == 0
    assert k.frame_page(out["frame"], q="r_9")["total"] == 0


def test_odd_and_duplicate_column_names_are_safe():
    store = Frames()
    t = pa.table([pa.array([1, None, 3]), pa.array(["a", "b", "c"])], names=['x"; drop', 'x"; drop'])
    page = store.page(store.keep(t), sort=0)
    assert page["columns"] == ['x"; drop', 'x"; drop'] and page["rows"][-1] == [None, "b"]


def test_restart_and_eviction_forget_results():
    k = Kernel()
    (out,) = _run(k, "import polars as pl\npl.DataFrame({'a': [1]})")
    k.restart()
    with pytest.raises(FrameGone):
        k.frame_page(out["frame"])
    store = Frames()
    first = store.keep(pa.table({"a": [1]}))
    for _ in range(frames.MAX_FRAMES):
        store.keep(pa.table({"a": [2]}))
    with pytest.raises(FrameGone):
        store.table(first)


def test_sql_cell_and_display_results_are_pageable():
    k = Kernel()
    outs = _run(k, "import polars as pl\ndisplay(pl.DataFrame({'a': [1, 2]}), summary=True)")
    assert outs[0]["summary"] and k.frame_page(outs[0]["frame"])["total"] == 2


@pytest.mark.parametrize("fmt", ["csv", "parquet", "xlsx"])
def test_downloads_write_every_row_to_the_reserved_name(tmp_path, fmt):
    store = Frames()
    df = pl.DataFrame({"n": list(range(2500)), "when": [dt.date(2026, 1, 1)] * 2500, "t": ["=1+1"] * 2500})
    target = tmp_path / f"r.{fmt}"
    target.touch()  # the engine reserves the name
    got = store.download(store.keep(df), fmt, str(target))
    assert got["rows"] == 2500 and not (tmp_path / f"r.{fmt}.partial").exists()
    if fmt == "csv":
        assert target.read_text().count("\n") == 2501
    elif fmt == "parquet":
        assert pq.read_table(target).num_rows == 2500
    else:
        with zipfile.ZipFile(target) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        assert sheet.count("<row ") == 2501 and 't="inlineStr"' in sheet and "<f>" not in sheet


def test_parquet_download_renames_duplicate_columns(tmp_path):
    store = Frames()
    t = pa.table([pa.array([1]), pa.array([2])], names=["a", "a"])
    target = tmp_path / "r.parquet"
    target.touch()
    store.download(store.keep(t), "parquet", str(target))
    assert pq.read_table(target).schema.names == ["a", "a_2"]


def test_excel_refuses_more_rows_than_a_sheet_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(frames, "EXCEL_MAX_ROWS", 10)
    store = Frames()
    target = tmp_path / "r.xlsx"
    target.touch()
    with pytest.raises(ValueError, match="CSV or Parquet"):
        store.download(store.keep(pa.table({"a": list(range(11))})), "xlsx", str(target))
    assert target.stat().st_size == 0 and not (tmp_path / "r.xlsx.partial").exists()


def test_a_failed_download_leaves_no_partial_file(tmp_path, monkeypatch):
    store = Frames()
    fid = store.keep(pa.table({"a": [1]}))
    monkeypatch.setattr(frames, "write_xlsx", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    target = tmp_path / "r.xlsx"
    target.touch()
    with pytest.raises(OSError):
        store.download(fid, "xlsx", str(target))
    assert not (tmp_path / "r.xlsx.partial").exists()


def test_service_download_never_replaces_a_file(tmp_path, monkeypatch):
    from fabric_desktop.kernel_sessions import KernelSessions
    from fabric_desktop.service import Service
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    svc = Service.__new__(Service)
    svc.sessions = KernelSessions(lambda _process: Kernel(), lambda: False)
    svc.kernel = Kernel()
    (out,) = _run(svc.kernel, "import polars as pl\npl.DataFrame({'a': [1, 2]})")
    a = svc.notebook_frame_download(out["frame"], "csv", "Sales")
    b = svc.notebook_frame_download(out["frame"], "csv", "Sales")
    assert a["path"] != b["path"] and all(p.endswith(".csv") for p in (a["path"], b["path"]))
    assert sorted(p.name for p in (tmp_path / "Downloads").iterdir()) == sorted(
        [Path(a["path"]).name, Path(b["path"]).name])
    svc.kernel.restart()
    gone = svc.notebook_frame_download(out["frame"], "csv", "Sales")
    assert gone["gone"] and len(list((tmp_path / "Downloads").iterdir())) == 2
    assert svc.notebook_frame_page(out["frame"])["gone"]


def test_process_kernel_pages_and_downloads_in_its_own_process(tmp_path):
    from fabric_desktop.kernel_proc import ProcessKernel
    k = ProcessKernel()
    try:
        (out,) = _run(k, "import polars as pl\npl.DataFrame({'n': range(1500)})")
        assert k.frame_page(out["frame"], offset=1400)["rows"][0] == [1400]
        target = tmp_path / "r.csv"
        target.touch()
        assert k.frame_download(out["frame"], "csv", str(target))["rows"] == 1500
        k.stop()
        with pytest.raises(FrameGone):
            k.frame_page(out["frame"])
    finally:
        k.close()


# ---- the profile (Inspect over every row)

def test_profile_covers_every_row():
    store = Frames()
    df = pl.DataFrame({"n": list(range(10_000)), "x": [float("nan"), None] + [1.5] * 9998,
                       "s": ["a"] * 6000 + ["b"] * 3999 + [None], "d": [dt.date(2026, 1, 1 + i % 28) for i in range(10_000)]})
    p = store.profile(store.keep(df))
    n, x, s, d = p["columns"]
    assert p["rows"] == 10_000
    assert (n["kind"], n["min"], n["max"], n["median"], n["missing"]) == ("number", 0, 9999, 4999.5, 0)
    assert sum(n["histogram"]) == 10_000 and len(n["histogram"]) == frames.HISTOGRAM_BINS
    assert x["missing"] == 2 and x["min"] == x["max"] == 1.5 and x["histogram"][0] == 9998
    assert s["kind"] == "text" and s["missing"] == 1 and s["top"] == [["a", 6000], ["b", 3999]]
    assert d["kind"] == "date" and (d["min"], d["max"]) == ("2026-01-01", "2026-01-28")
    assert 25 <= d["distinct"] <= 31  # approximate, like Databricks' profile


def test_profile_of_an_empty_or_all_null_column():
    store = Frames()
    p = store.profile(store.keep(pa.table({"a": pa.array([None, None], pa.int64()), "b": pa.array([None, None], pa.string())})))
    a, b = p["columns"]
    assert a["missing"] == 2 and "histogram" not in a and b["missing"] == 2 and "top" not in b


def test_profile_caps_very_wide_results(monkeypatch):
    monkeypatch.setattr(frames, "PROFILE_COLUMNS", 3)
    store = Frames()
    p = store.profile(store.keep(pa.table({f"c{i}": [i] for i in range(5)})))
    assert len(p["columns"]) == 3 and p["more_columns"] == 2


def test_service_profile_after_restart_asks_to_run_again():
    from fabric_desktop.kernel_sessions import KernelSessions
    from fabric_desktop.service import Service
    svc = Service.__new__(Service)
    svc.sessions = KernelSessions(lambda _process: Kernel(), lambda: False)
    svc.kernel = Kernel()
    (out,) = _run(svc.kernel, "import polars as pl\npl.DataFrame({'a': [1, 2]})")
    assert svc.notebook_frame_profile(out["frame"])["columns"][0]["max"] == 2
    svc.kernel.restart()
    assert svc.notebook_frame_profile(out["frame"])["gone"]


# ---- the chart builder (aggregates over every row)

def _sales(n=3000):
    return pl.DataFrame({"region": [["East", "West", "North"][i % 3] for i in range(n)],
                         "product": [f"p{i % 9}" for i in range(n)], "amount": [float(i % 10) for i in range(n)],
                         "day": [i % 30 for i in range(n)]})


def test_bar_chart_aggregates_every_row_with_series_and_other():
    store = Frames()
    fid = store.keep(_sales())
    c = store.chart(fid, "bar", x=0, y=2, agg="sum")
    assert sorted(c["categories"]) == ["East", "North", "West"] and sum(c["series"][0]["values"]) == sum(float(i % 10) for i in range(3000))
    c = store.chart(fid, "stacked", x=0, y=2, agg="count", series=1)
    names = [s["name"] for s in c["series"]]
    assert len(names) == frames.CHART_SERIES + 1 and names[-1] == frames.OTHER
    total = sum(v or 0 for s in c["series"] for v in s["values"])
    assert total == 3000  # nothing lost when series fold into Other


def test_line_keeps_x_order_and_caps_categories(monkeypatch):
    monkeypatch.setattr(frames, "CHART_CATEGORIES", 10)
    store = Frames()
    c = store.chart(store.keep(_sales()), "line", x=3, y=2, agg="avg")
    assert c["categories"] == list(range(10)) and c["truncated"]


def test_null_x_and_nan_values():
    store = Frames()
    t = pa.table({"k": ["a", None, "a", None], "v": [1.0, 2.0, float("nan"), 4.0]})
    c = store.chart(store.keep(t), "bar", x=0, y=1, agg="sum")
    got = dict(zip(c["categories"], c["series"][0]["values"]))
    assert got == {"a": 1.0, None: 6.0}


def test_histogram_scatter_and_box():
    store = Frames()
    fid = store.keep(_sales())
    h = store.chart(fid, "histogram", y=2, bins=10)
    assert sum(h["counts"]) == 3000 and (h["min"], h["max"]) == (0.0, 9.0)
    s = store.chart(fid, "scatter", x=3, y=2, series=0)
    assert len(s["points"]) == 3000 and not s["sampled"] and s["points"][0][2] == "East"
    b = store.chart(fid, "box", x=0, y=2)
    assert {x["x"] for x in b["boxes"]} == {"East", "West", "North"} and b["boxes"][0]["max"] == 9.0


def test_scatter_samples_large_results(monkeypatch):
    monkeypatch.setattr(frames, "CHART_POINTS", 100)
    store = Frames()
    s = store.chart(store.keep(_sales()), "scatter", x=3, y=2)
    assert len(s["points"]) == 100 and s["sampled"] and s["total"] == 3000


def test_chart_refuses_unknown_kinds_and_columns():
    store = Frames()
    fid = store.keep(_sales(10))
    with pytest.raises(ValueError, match="Chart kinds"):
        store.chart(fid, "radar", x=0)
    with pytest.raises(ValueError, match="isn't in this result"):
        store.chart(fid, "bar", x=9)
    with pytest.raises(ValueError, match="Aggregates"):
        store.chart(fid, "bar", x=0, y=2, agg="drop table")


def test_a_cells_chart_is_saved_by_column_name_and_read_back():
    from fabric_desktop.notebook import from_ipynb, merge_saved, to_ipynb
    cells = [{"type": "code", "source": "df", "cell_id": "c1",
              "meta": {"chart": {"kind": "stacked", "x": "region", "y": "amount", "agg": "sum", "series": "product",
                                 "title": "Sales", "evil": {"x": 1}}}}]
    nb = to_ipynb(cells, None)
    assert nb["cells"][0]["metadata"]["fabric_desktop"]["chart"] == {
        "kind": "stacked", "x": "region", "y": "amount", "agg": "sum", "series": "product", "title": "Sales"}
    back = from_ipynb(nb)["cells"][0]["meta"]["chart"]
    assert back["series"] == "product" and "evil" not in back
    # Back to Table: the saved chart goes, other tools' cell metadata stays.
    before = json_roundtrip(nb)
    before["cells"][0]["metadata"]["fabric_desktop"]["other"] = 1
    cells[0]["meta"] = {}
    merged = merge_saved(to_ipynb(cells, None), before)
    assert merged["cells"][0]["metadata"]["fabric_desktop"] == {"other": 1}


def test_a_cells_further_charts_are_saved_after_the_first_and_capped():
    from fabric_desktop.notebook import (
        MAX_EXTRA_CHARTS,
        from_ipynb,
        merge_saved,
        to_ipynb,
    )
    first = {"kind": "bar", "x": "region", "y": "amount", "agg": "sum"}
    more = [{"kind": "line", "x": "month", "y": "orders", "agg": "avg", "title": "Orders"}, "junk", {"x": "no kind"}]
    cells = [{"type": "code", "source": "df", "cell_id": "c1", "meta": {"chart": first, "charts": more}}]
    nb = to_ipynb(cells, None)
    fd = nb["cells"][0]["metadata"]["fabric_desktop"]
    assert fd["chart"] == first and fd["charts"] == [more[0]]
    assert from_ipynb(nb)["cells"][0]["meta"]["charts"] == [more[0]]
    cells[0]["meta"] = {"chart": first, "charts": [first] * 20}
    assert len(to_ipynb(cells, None)["cells"][0]["metadata"]["fabric_desktop"]["charts"]) == MAX_EXTRA_CHARTS
    # Removing them all drops the key; other tools' keys stay.
    before = json_roundtrip(nb)
    before["cells"][0]["metadata"]["fabric_desktop"]["other"] = 1
    cells[0]["meta"] = {"chart": first}
    merged = merge_saved(to_ipynb(cells, None), before)
    assert merged["cells"][0]["metadata"]["fabric_desktop"] == {"chart": first, "other": 1}
    # A notebook from a build that only knew one chart opens with just that one.
    assert "charts" not in from_ipynb(to_ipynb([{"type": "code", "source": "", "meta": {"chart": first}}], None))["cells"][0]["meta"]


def json_roundtrip(v):
    import json
    return json.loads(json.dumps(v))

def test_time_zone_timestamps_page_sort_and_search_without_pytz(monkeypatch):
    # Delta's default timestamp has a time zone; DuckDB's fetchall() would need pytz for it.
    monkeypatch.setitem(sys.modules, "pytz", None)
    store = Frames()
    # The grid shows each time in the PC's time zone, with its offset; seconds read the same in every zone.
    at = [dt.datetime(2026, 10, 20, h, 0, s, tzinfo=dt.timezone.utc) for h, s in ((0, 11), (1, 22), (2, 33))]
    fid = store.keep(pa.table({"at": pa.array(at, pa.timestamp("us", tz="UTC")), "n": [1, 2, 3]}))
    page = store.page(fid, sort=0, desc=True)
    assert page["types"][0] == "timestamp" and page["rows"][0][1] == 3
    assert dt.datetime.fromisoformat(page["rows"][0][0]) == at[2]
    assert store.page(fid, q=":22")["total"] == 1


def test_profile_counts_infinite_values_and_leaves_them_out_of_the_statistics():
    store = Frames()
    p = store.profile(store.keep(pa.table({"v": [1.0, 3.0, float("inf"), float("-inf"), None]})))
    (v,) = p["columns"]
    assert (v["infinite"], v["missing"], v["min"], v["max"], v["mean"]) == (2, 1, 1.0, 3.0, 2.0)
    assert sum(v["histogram"]) == 2


def test_profile_of_time_zone_timestamps_without_pytz(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytz", None)
    store = Frames()
    ts = pa.array([dt.datetime(2026, 1, d, tzinfo=dt.timezone.utc) for d in (1, 9)], pa.timestamp("us", tz="UTC"))
    (c,) = store.profile(store.keep(pa.table({"at": ts})))["columns"]
    # Shown in the PC's time zone, with its offset (midnight UTC is the day before in the Americas).
    assert c["kind"] == "date"
    assert (dt.datetime.fromisoformat(c["min"]), dt.datetime.fromisoformat(c["max"])) == tuple(ts.to_pylist())


def test_one_column_that_cant_be_profiled_doesnt_hide_the_others(monkeypatch):
    store = Frames()
    real = Frames._profile_column

    def flaky(con, c, f, n):
        if f.name == "bad":
            raise ValueError("Out of Range Error")
        return real(con, c, f, n)
    monkeypatch.setattr(Frames, "_profile_column", staticmethod(flaky))
    p = store.profile(store.keep(pa.table({"bad": [1], "good": [2]})))
    assert "Out of Range" in p["columns"][0]["error"] and p["columns"][1]["max"] == 2


def test_a_large_result_is_profiled_from_a_random_sample(monkeypatch):
    monkeypatch.setattr(frames, "PROFILE_ROWS", 100)
    store = Frames()
    p = store.profile(store.keep(pa.table({"n": list(range(1000))})))
    assert p["sampled"] and (p["rows"], p["profiled_rows"]) == (1000, 100)
    (n,) = p["columns"]
    assert n["min"] < 50 and n["max"] > 950
    # Data that repeats every 4 rows: an every-10th-row sample would only ever see 0 and 2.
    p = store.profile(store.keep(pa.table({"k": ["abcd"[i % 4] for i in range(1000)]})))
    assert p["columns"][0]["distinct"] == 4
    assert store.profile(store.keep(pa.table({"n": list(range(1000))})))["columns"][0]["min"] == n["min"], "the same sample each time"


def test_charting_by_a_time_zone_timestamp_without_pytz(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytz", None)
    store = Frames()
    ts = pa.array([dt.datetime(2026, 1, 1 + i % 3, tzinfo=dt.timezone.utc) for i in range(9)], pa.timestamp("us", tz="UTC"))
    c = store.chart(store.keep(pa.table({"at": ts, "v": list(range(9))})), "line", x=0, y=1, agg="sum")
    assert len(c["categories"]) == 3 and sum(c["series"][0]["values"]) == 36
