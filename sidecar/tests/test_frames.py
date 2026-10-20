"""The result viewer behind a notebook table output: paging, search, sort and downloads (frames.py)."""

import datetime as dt
import sys
import zipfile

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
    from fabric_desktop.service import Service
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    svc = Service.__new__(Service)
    svc.kernel = Kernel()
    (out,) = _run(svc.kernel, "import polars as pl\npl.DataFrame({'a': [1, 2]})")
    a = svc.notebook_frame_download(out["frame"], "csv", "Sales")
    b = svc.notebook_frame_download(out["frame"], "csv", "Sales")
    assert a["path"] != b["path"] and all(p.endswith(".csv") for p in (a["path"], b["path"]))
    assert sorted(p.name for p in (tmp_path / "Downloads").iterdir()) == sorted(
        [a["path"].split("/")[-1], b["path"].split("/")[-1]])
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


def test_time_zone_timestamps_page_sort_and_search_without_pytz(monkeypatch):
    # Delta's default timestamp has a time zone; DuckDB's fetchall() would need pytz for it.
    monkeypatch.setitem(sys.modules, "pytz", None)
    store = Frames()
    ts = pa.array([dt.datetime(2026, 10, 20, h, tzinfo=dt.timezone.utc) for h in range(3)], pa.timestamp("us", tz="UTC"))
    fid = store.keep(pa.table({"at": ts, "n": [1, 2, 3]}))
    page = store.page(fid, sort=0, desc=True)
    assert page["types"][0] == "timestamp" and page["rows"][0][1] == 3 and "2026-10-20 02:00:00" in page["rows"][0][0]
    assert store.page(fid, q="01:00")["total"] == 1
