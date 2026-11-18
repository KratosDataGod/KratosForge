"""Seeded fuzz tests for the readers of CSV, Parquet, JSON and Excel files: the Workspaces file preview
(preview.file_preview), a local data project's folders (localdata engine) and a notebook's spark.read on
this PC (spark_local). Every input is either read with the rows it holds or refused with an error, never
a crash, and no row is silently left out.

The generators use the random module with fixed seeds, so a failure names a case that can be run again.
The values in generated CSV files always start with a letter, so no reader infers a number or a date from
them and each one can be compared with what Python's csv module wrote.
"""

from __future__ import annotations

import csv
import io
import json
import random
import subprocess
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop import preview, spark_local
from fabric_desktop.localdata import LocalData

# Text that trips CSV readers: delimiters a sniffer might pick, quotes, escapes, line breaks, comment
# marks, null words and characters outside ASCII.
ATOMS = ["a", "b c", "x,y", 'q"t', '""', "line\nbreak", "cr\r\nlf", " lead", "trail ", "é", "日本", "\t", "#c",
         "\\", '\\"', "'s", "NULL", "null", "NA", "z0", "k=v", "😀", ";", "|", ";;", "||"]
CASES = 120


def _value(r: random.Random) -> str:
    return "v" + "".join(r.choice(ATOMS) for _ in range(r.randint(1, 3)))


def _csv(r: random.Random) -> tuple[list[str], list[list[str]], str]:
    ncol = r.randint(1, 6)
    header = [f"c{i}" for i in range(ncol)]
    rows = [[_value(r) for _ in range(ncol)] for _ in range(r.randint(1, 30))]
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=r.choice(["\n", "\r\n"])).writerows([header, *rows])
    text = buf.getvalue()
    if r.random() < 0.3:  # no line break after the last row
        text = text.rstrip("\r\n")
    return header, rows, text


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture
def local(tmp_path):
    return LocalData(tmp_path / "app" / "local-data")


def _engine_with(local, folder: Path):
    engine = local.engine(local.create("Fuzz")["id"])
    local.add_source(engine.project.id, str(folder))
    return engine


def _status(engine, table: str) -> dict:
    return next(t for t in engine.tables() if t["table"] == table)


# ---- CSV: every row comes back as it was written


def test_generated_csv_files_read_back_the_same_in_the_preview_and_spark_read(tmp_path, spark):
    for case in range(CASES):
        header, rows, text = _csv(random.Random(1000 + case))
        path = tmp_path / f"c{case}.csv"
        path.write_text(text, encoding="utf-8", newline="")
        got = preview.file_preview(path, path.name, 1000)
        assert [c["name"] for c in got["columns"]] == header, (case, text)
        assert got["rows"] == rows, (case, text)
        df = spark.read.csv(str(path), header=True)
        assert df.columns == header, (case, text)
        assert [list(r) for r in df.collect()] == rows, (case, text)


def test_generated_csv_files_read_back_the_same_as_local_data_tables(tmp_path, local):
    folder = tmp_path / "files"
    folder.mkdir()
    written = {}
    for case in range(CASES):
        header, rows, text = _csv(random.Random(2000 + case))
        (folder / f"t{case}.csv").write_text(text, encoding="utf-8", newline="")
        written[f"t{case}"] = header, rows, text
    engine = _engine_with(local, folder)
    for table, (header, rows, text) in written.items():
        out = engine.execute(f"SELECT * FROM dbo.{table}", limit=10_000)
        assert [c["name"] if isinstance(c, dict) else c for c in out["columns"]] == header, (table, text)
        assert [list(r) for r in out["rows"]] == rows, (table, text)


# DuckDB's sniffer took these for a file with a different delimiter whose first lines are a preamble, and
# left those lines out with no error (the header and every row but one, in the first). Found by the
# generator above (seeds 1163, 1071).
SKIPPED_BY_THE_SNIFFER = {
    "one_column_with_pipes": ("c0\nv|\\\nvé\nva\nv\\\nv#c\t😀\nv||b c\n", 6),
    "one_column_with_tabs": (('c0\r\nvNULLb c\r\n"vk=vb c\\"""\r\nv lead\r\nvNULL😀\r\nvk=v|a\r\nv日本\r\n'
                             '"v|日本line\nbreak"\r\n"vtrail x,y"\r\n"vq""t"\r\nv\t\r\nv\t日本\r\n'), 11),
}


@pytest.mark.parametrize("name", SKIPPED_BY_THE_SNIFFER)
def test_a_csv_file_the_sniffer_would_cut_keeps_every_row(tmp_path, local, spark, name):
    text, n = SKIPPED_BY_THE_SNIFFER[name]
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "t.csv").write_text(text, encoding="utf-8", newline="")
    engine = _engine_with(local, folder)
    assert engine.execute("SELECT count(*) FROM dbo.t")["rows"] == [[n]]
    assert spark.read.csv(str(folder / "t.csv"), header=True).count() == n


# A first row shorter than the rest, and a title above the header: DuckDB's sniffer dropped the lines
# before the ones it liked. Now each line is a row (local data) or the read stops (spark.read, which
# always splits at commas, as Spark does).
CUT_AT_THE_TOP = {
    "short_first_row": ("a\n1\n2,3\n4,5\n6,7\n", 4),
    "title_above_the_header": ("exported by tool\n\nid,name\n1,a\n2,b\n", 4),
}


@pytest.mark.parametrize("name", CUT_AT_THE_TOP)
def test_lines_above_the_rows_the_sniffer_likes_arent_dropped(tmp_path, local, spark, name):
    text, lines = CUT_AT_THE_TOP[name]
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "t.csv").write_text(text, encoding="utf-8", newline="")
    engine = _engine_with(local, folder)
    if _status(engine, "t")["status"] == "ok":
        assert engine.execute("SELECT count(*) FROM dbo.t")["rows"][0][0] >= lines
    for header in (True, False):
        with pytest.raises(duckdb.Error, match="sniffing file"):
            spark.read.csv(str(folder / "t.csv"), header=header).collect()


def test_a_read_that_fails_after_a_count_leaves_the_session_working(tmp_path, spark):
    """DuckDB 1.x's DataFrame.count() left its result open, and the next failing statement (here a CSV the
    sniffer refuses, then a file that isn't there) left every later one failing with "Current transaction
    is aborted" until the notebook restarted."""
    good = tmp_path / "good.csv"
    good.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n3\n4,5\n", encoding="utf-8")
    for path in (bad, tmp_path / "missing.csv"):
        assert spark.read.csv(str(good), header=True).count() == 2
        with pytest.raises(duckdb.Error):
            spark.read.csv(str(path), header=True).collect()
        assert [tuple(r) for r in spark.read.csv(str(good), header=True).collect()] == [("1", "a"), ("2", "b")]


def _mangle(r: random.Random, data: bytes) -> bytes:
    """A damaged copy: cut short, bytes flipped, NULs or invalid UTF-8 put in, a byte order mark added."""
    b = bytearray(data)
    kind = r.choice(["cut", "flip", "nul", "utf8", "bom", "noise"])
    if kind == "cut":
        return bytes(b[:r.randrange(len(b) + 1)])
    if kind == "flip":
        for _ in range(r.randint(1, 6)):
            b[r.randrange(len(b))] ^= 1 << r.randrange(8)
    elif kind == "nul":
        b.insert(r.randrange(len(b) + 1), 0)
    elif kind == "utf8":
        b.insert(r.randrange(len(b) + 1), r.choice([0xFF, 0xC3, 0x80, 0xED]))
    elif kind == "bom":
        return b"\xef\xbb\xbf" + bytes(b)
    else:
        return bytes(r.randrange(256) for _ in range(r.randint(0, 80)))
    return bytes(b)


# The errors a reader gives for a file it can't read: its own, never a bug in this code (a KeyError,
# an IndexError, a TypeError, an AttributeError) escaping.
REFUSALS = (pa.ArrowException, OSError, duckdb.Error, UnicodeDecodeError, ValueError, NotImplementedError)


def test_damaged_csv_files_are_read_or_refused(tmp_path, local, spark):
    folder = tmp_path / "files"
    folder.mkdir()
    for case in range(CASES):
        r = random.Random(3000 + case)
        _, _, text = _csv(r)
        path = folder / f"d{case}.csv"
        path.write_bytes(_mangle(r, text.encode("utf-8")))
        for read in (lambda path=path: preview.file_preview(path, path.name, 1000),
                     lambda path=path: spark.read.csv(str(path), header=True).collect()):
            try:
                read()
            except REFUSALS:
                pass
    engine = _engine_with(local, folder)  # one file it can't read doesn't stop the others
    for case in range(CASES):
        entry = _status(engine, f"d{case}")
        assert entry["status"] in ("ok", "error")
        if entry["status"] == "error":
            assert entry["error"], case
            continue
        try:
            engine.execute(f"SELECT * FROM dbo.d{case}", limit=10_000)
        except REFUSALS:
            pass
    assert engine.execute("SELECT 1")["rows"] == [[1]]


# ---- Parquet: a damaged file is refused, or read; the reader goes on working


def _parquet() -> tuple[pa.Table, bytes]:
    table = pa.table({"id": list(range(50)), "name": [f"n{i}" for i in range(50)], "x": [i / 3 for i in range(50)]})
    buf = io.BytesIO()
    pq.write_table(table, buf, row_group_size=10)
    return table, buf.getvalue()


def _damaged_parquet(r: random.Random, good: bytes) -> tuple[str, bytes]:
    b = bytearray(good)
    kind = r.choice(["cut", "flip", "zero", "noise"])
    if kind == "cut":
        return kind, bytes(b[:r.randrange(len(b))])
    if kind == "flip":
        for _ in range(r.randint(1, 8)):
            b[r.randrange(len(b))] ^= 1 << r.randrange(8)
    elif kind == "zero":
        i = r.randrange(len(b))
        n = min(r.randint(1, 64), len(b) - i)
        b[i:i + n] = bytes(n)
    else:
        return kind, bytes(r.randrange(256) for _ in range(r.randint(0, 64)))
    return kind, bytes(b)


def test_damaged_parquet_files_are_refused_or_read_whole(tmp_path, spark):
    """A file cut short or replaced by noise never gives rows: it is refused. A flipped bit inside a value
    can give a different value, as Parquet written without page checksums can't tell; those are only
    checked to be read or refused. Either way the reader still reads a good file afterwards."""
    table, good = _parquet()
    expect = [list(r) for r in zip(*(table.column(c).to_pylist() for c in table.column_names), strict=True)]
    good_path = tmp_path / "good.parquet"
    good_path.write_bytes(good)
    for case in range(CASES):
        kind, data = _damaged_parquet(random.Random(4000 + case), good)
        path = tmp_path / f"p{case}.parquet"
        path.write_bytes(data)
        for name, read in (("preview", lambda path=path: preview.file_preview(path, path.name, 1000)["rows"]),
                           ("spark", lambda path=path: [list(x) for x in spark.read.parquet(str(path)).collect()])):
            try:
                rows = read()
            except REFUSALS:
                continue
            if kind in ("cut", "noise"):
                assert [list(x) for x in rows] == expect, (name, case, kind)
    assert spark.read.parquet(str(good_path)).count() == 50


def test_a_local_data_project_keeps_working_after_any_damaged_parquet_file(tmp_path, local):
    """DuckDB 2.0 stops the whole database after an internal error reading some damaged Parquet files
    (case 241 of seed 20000 is one); the project opens it again, so the next query works."""
    table, good = _parquet()
    folder = tmp_path / "files"
    folder.mkdir()
    pq.write_table(pa.table({"a": [1, 2]}), folder / "good.parquet")
    damaged = [_damaged_parquet(random.Random(20000 + case), good)[1] for case in range(242)]
    picks = [241, *range(0, 240, 6)]
    for case in picks:
        (folder / f"p{case}.parquet").write_bytes(damaged[case])
    engine = _engine_with(local, folder)
    for case in picks:
        try:
            engine.execute(f"SELECT * FROM dbo.p{case}", limit=100)
        except (*REFUSALS, KeyError):  # KeyError: no view, the table was reported as unreadable
            pass
        assert engine.execute("SELECT count(*) FROM dbo.good")["rows"] == [[2]], case


# ---- JSON preview: every record and every key shows, and the count is the file's


def test_a_json_array_preview_counts_every_record_and_shows_every_key(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps([{"id": i} for i in range(150)]), encoding="utf-8")
    got = preview.file_preview(path, "a.json", 100)
    assert (len(got["rows"]), got["row_count"]) == (100, 150)
    path.write_text(json.dumps([{"a": 1}, {"b": 2}, 5]), encoding="utf-8")
    got = preview.file_preview(path, "a.json", 100)
    assert [c["name"] for c in got["columns"]] == ["a", "b", "value"]
    assert got["rows"] == [[1, None, None], [None, 2, None], [None, None, 5]]


@pytest.mark.parametrize("text", ["null", "null\n", " null", "\ufeffnull", 'null\n{"a": 1}', "\r\n\tnull"])
def test_a_json_file_that_starts_with_null_is_previewed_without_stopping_the_engine(tmp_path, text):
    """pyarrow's JSON Lines reader stops the process with a segmentation fault on these, so the preview runs
    in a child process here: a crash is a failed test, not a stopped test run."""
    path = tmp_path / "n.json"
    path.write_text(text, encoding="utf-8")
    script = ("import json, sys\nfrom pathlib import Path\nfrom fabric_desktop import preview\n"
              "try:\n    out = preview.file_preview(Path(sys.argv[1]), 'n.json')\n"
              "except ValueError as exc:\n    out = {'refused': str(exc)}\nprint(json.dumps(out))")
    done = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=120,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout)
    assert "refused" in out or out["row_count"] == len(out["rows"]) >= 1


def _json_value(r: random.Random, depth: int = 0):
    pick = r.randrange(8 if depth < 3 else 5)
    if pick == 0:
        return None
    if pick == 1:
        return r.choice([True, False])
    if pick == 2:
        return r.randint(-10**6, 10**6)
    if pick == 3:
        return r.choice([0.5, -1.25, 1e300, 3.0])
    if pick == 4:
        return _value(r)
    if pick in (5, 6):
        return {f"k{r.randrange(5)}": _json_value(r, depth + 1) for _ in range(r.randint(0, 4))}
    return [_json_value(r, depth + 1) for _ in range(r.randint(0, 4))]


def test_generated_json_documents_preview_every_record_or_are_refused(tmp_path):
    for case in range(CASES):
        r = random.Random(5000 + case)
        doc = [_json_value(r) for _ in range(r.randint(0, 12))] if r.random() < 0.7 else _json_value(r)
        path = tmp_path / f"j{case}.json"
        lines = r.random() < 0.3 and isinstance(doc, list) and all(isinstance(d, dict) and d for d in doc)
        text = "\n".join(json.dumps(d) for d in doc) if lines else json.dumps(doc)
        if r.random() < 0.3:
            path.write_bytes(_mangle(r, text.encode("utf-8")))
        else:
            path.write_text(text, encoding="utf-8")
        try:
            got = preview.file_preview(path, path.name, 1000)
        except REFUSALS:
            continue
        # A record with no keys ({}) is counted but has no value to show.
        assert len(got["rows"]) == (got["row_count"] if got["columns"] else 0), (case, text)
        width = len(got["columns"])
        assert all(len(row) == width for row in got["rows"]), (case, text)


# ---- Excel: found, and refused by name


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_an_excel_workbook_in_a_folder_is_refused_by_name(tmp_path, local, suffix):
    folder = tmp_path / "files"
    folder.mkdir()
    for case in range(10):
        r = random.Random(6000 + case)
        (folder / f"book{case}{suffix}").write_bytes(b"PK\x03\x04" + bytes(r.randrange(256) for _ in range(200)))
    engine = _engine_with(local, folder)
    for case in range(10):
        entry = _status(engine, f"book{case}")
        assert entry["status"] == "error"
        assert "Excel workbooks are found but can't be read yet" in entry["error"]
