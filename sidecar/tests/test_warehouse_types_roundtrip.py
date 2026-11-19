"""WR-242: every Fabric Warehouse data type, out through the SQL page's CSV, Excel and Parquet exports and back in
through a local data folder, value by value.

The types are the ones Microsoft Learn's "Data types in Fabric Data Warehouse" page lists for tables, as it read on
2026-11-18 (last updated 2026-06-11): bit, smallint, int, bigint, decimal/numeric, float, real, date, time(n) and
datetime2(n) with at most 6 digits of a second, char(n), varchar(n | MAX), varbinary(n | MAX) and
uniqueidentifier. A value either comes back exactly, or it comes back changed in the one way that
docs/sql-compatibility.md ("Exporting and reading back results") names, and that the export's notes or the folder's
issues say. Nothing changes without a word.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import io
import math
import re
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from fabric_desktop import frames, sql_exec
from fabric_desktop.frames import ExportNotes, Frames, write_xlsx
from fabric_desktop.localdata import LocalData

D = decimal.Decimal
UUID = "A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11"

TABLE = """CREATE TABLE dbo.w (
  id int, b bit, si smallint, i int, bi bigint,
  d decimal(38, 10), d0 decimal(38, 0), dfrac decimal(38, 38), n numeric(18, 4),
  f float, r real, dt date, tm time(6), ts datetime2(6),
  c char(5), v varchar(50), vm varchar(max), vb varbinary(16), vbm varbinary(max), u uniqueidentifier)"""

ROWS = [
    # the smallest values each type holds
    f"""(1, 1, -32768, -2147483648, -9223372036854775808,
       -9999999999999999999999999999.9999999999, -99999999999999999999999999999999999999,
       -0.99999999999999999999999999999999999999, -99999999999999.9999,
       -1.7976931348623157E308, -3.4028235E38, '0001-01-01', '00:00:00', '0001-01-01 00:00:00',
       'ab', 'a,"b"', '=1+2', 0x00, CONVERT(varbinary(max), '0x', 1), '{UUID}')""",
    # the largest
    """(2, 0, 32767, 2147483647, 9223372036854775807,
       9999999999999999999999999999.9999999999, 99999999999999999999999999999999999999,
       0.99999999999999999999999999999999999999, 99999999999999.9999,
       1.7976931348623157E308, 3.4028235E38, '9999-12-31', '23:59:59.999999', '9999-12-31 23:59:59.999999',
       NULL, '', 'line1
line2 é 😀 中文', 0x00FF10, 0xDEADBEEF00, NULL)""",
    # zero, the float edges, Excel's 1900 edges
    """(3, NULL, 0, 0, 0, 0.0000000001, 0, 0.00000000000000000000000000000000000001, 0,
       CAST(-0.0e0 AS float), 1.4E-45, '1899-12-31', '12:00:00.0001', '1900-02-28 12:00:00.123',
       ' x ', NULL, '-5', NULL, 0x0000, '00000000-0000-0000-0000-000000000000')""",
    """(4, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, CAST('NaN' AS float), CAST('Infinity' AS real),
       '1900-03-01', '08:30:00.250', '2026-11-18 08:30:00.250', 'abcde', '@SUM(A1)', '+1', NULL, NULL, NULL)""",
    """(5, NULL, -1, -1, 1234567890123456789, 12345.6789, 123456789012345, 0.5, 0.1,
       CAST('-Infinity' AS float), 0.1, '2026-11-18', '23:59:59', '2026-11-18 23:59:59', NULL,
       'tab	in', 'he said "hi", then left', 0xFF, 0x41, NULL)""",
]

# T-SQL's own answers, written out: what each export must keep (or say it changed).
TEXT = ["a,\"b\"", "", None, "@SUM(A1)", "tab\tin"]
LONG = ["=1+2", "line1\nline2 é 😀 中文", "-5", "+1", 'he said "hi", then left']
BYTES = [b"\x00", b"\x00\xff\x10", None, None, b"\xff"]
BYTES_MAX = [b"", b"\xde\xad\xbe\xef\x00", b"\x00\x00", None, b"A"]

L = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30, "sql_max_concurrent": 2,
     "sql_result_retention_hours": 24, "sql_temp_dir": ""}


def _wait(seconds: float, done: Any) -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = done()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("still running")


class World:
    def __init__(self, root: Path):
        self.root = root
        self.local = LocalData(root / "local-data")
        self.pid = self.local.create("Types")["id"]
        self.engine = self.local.engine(self.pid)
        self.runs = sql_exec.SqlExecutions(root / "data", token=lambda: "", limits=lambda: L,
                                           downloads=lambda: root / "Downloads", local_engine=self.local.engine)
        self.tsql(TABLE)
        self.tsql("INSERT INTO dbo.w VALUES " + ",\n".join(ROWS))
        self.run = self.query("SELECT * FROM dbo.w ORDER BY id")
        self.source = self.runs.table(self.run)[1]

    def tsql(self, sql: str) -> dict[str, Any]:
        return self.engine.execute(sql, dialect="tsql")

    def query(self, sql: str) -> str:
        """A T-SQL run on the SQL page, finished; its id."""
        run = self.runs.start(sql, target="local", project=self.pid, dialect="tsql")["id"]
        status = _wait(60, lambda: (s := self.runs.status(run))["state"] in sql_exec.FINISHED and s)
        assert status["state"] == "completed", status.get("error")
        return run

    def export(self, fmt: str, run: str | None = None, raw: bool = False) -> dict[str, Any]:
        started = self.runs.export(run or self.run, fmt, name="types", raw=raw)
        status = _wait(60, lambda: (s := self.runs.export_status(started["id"]))["state"] != "running" and s)
        assert status["state"] == "completed", status.get("error")
        return status

    def read_back(self, path: str, name: str) -> dict[str, Any]:
        """The exported file in a folder of its own, added to the project as a local data folder (as the app's
        Add folder does), so it becomes the table ``dbo.<name>``."""
        folder = self.root / "back" / name
        folder.mkdir(parents=True)
        shutil.copy(path, folder / f"{name}{Path(path).suffix}")
        return self.local.add_source(self.pid, str(folder))

    def rows(self, sql: str) -> pa.Table:
        return self.runs.table(self.query(sql))[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = World(tmp_path_factory.mktemp("wr242"))
    yield w
    w.runs.shutdown()
    w.local.close_all()


def col(table: pa.Table, name: str) -> list[Any]:
    return table.column(name).to_pylist()


def same(a: Any, b: Any) -> bool:
    """Equal, with NaN equal to NaN and -0.0 not equal to 0.0."""
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return a == b and math.copysign(1, a) == math.copysign(1, b)
    return type(a) is type(b) and a == b


def assert_same(got: list[Any], want: list[Any], what: str) -> None:
    assert len(got) == len(want), what
    for k, (g, w) in enumerate(zip(got, want, strict=True)):
        assert same(g, w), f"{what} row {k + 1}: {g!r} != {w!r}"


def test_the_source_holds_every_edge_value_as_tsql_does(world):
    s = world.source
    assert s.schema.field("d").type == pa.decimal128(38, 10) and s.schema.field("dfrac").type == pa.decimal128(38, 38)
    assert s.schema.field("tm").type == pa.time64("us") and s.schema.field("ts").type == pa.timestamp("us")
    assert col(s, "d")[:3] == [D("-9999999999999999999999999999.9999999999"), D("9999999999999999999999999999.9999999999"),
                               D("1E-10")]
    assert col(s, "d0")[1] == D("9" * 38) and col(s, "dfrac")[1] == D("0." + "9" * 38)
    assert_same(col(s, "f")[2:5], [-0.0, float("nan"), float("-inf")], "f")
    assert col(s, "r")[3] == float("inf") and col(s, "r")[0] == -3.4028234663852886e38
    assert col(s, "dt")[:3] == [dt.date(1, 1, 1), dt.date(9999, 12, 31), dt.date(1899, 12, 31)]
    assert col(s, "tm")[:3] == [dt.time(0), dt.time(23, 59, 59, 999999), dt.time(12, 0, 0, 100)]
    assert col(s, "ts")[1] == dt.datetime(9999, 12, 31, 23, 59, 59, 999999)
    assert col(s, "v") == TEXT and col(s, "vm") == LONG
    assert col(s, "vb") == BYTES and col(s, "vbm") == BYTES_MAX  # CONVERT(varbinary(max), '0x', 1) is no bytes
    # A uniqueidentifier is written in lower case here; T-SQL writes it in upper case (the docs say so).
    assert col(s, "u")[0] == UUID.lower()


# ---- Parquet: every value and type comes back

def test_parquet_keeps_every_value_and_type(world):
    out = world.export("parquet")
    assert out["notes"] == []
    added = world.read_back(out["path"], "back_parquet")
    assert added["issues"] == []
    back = world.rows("SELECT * FROM dbo.back_parquet ORDER BY id")
    assert back.schema == world.source.schema
    for name in world.source.schema.names:
        assert_same(col(back, name), col(world.source, name), name)


# ---- CSV

@pytest.fixture(scope="module")
def csv_back(world):
    out = world.export("csv")
    added = world.read_back(out["path"], "back_csv")
    return out, added, world.rows("SELECT * FROM dbo.back_csv ORDER BY id")


def test_csv_keeps_these_types_exactly(csv_back, world):
    _, _, back = csv_back
    for name in ("b", "bi", "f", "dt", "tm", "ts", "c"):
        assert back.schema.field(name).type == world.source.schema.field(name).type, name
        assert_same(col(back, name), col(world.source, name), name)
    # smallint and int come back as bigint, every value the same
    for name in ("si", "i"):
        assert back.schema.field(name).type == pa.int64() and col(back, name) == col(world.source, name)


def test_csv_keeps_empty_text_apart_from_null(csv_back):
    # Arrow writes "" for an empty text and nothing for NULL; the folder reads them that way (WR-242).
    _, _, back = csv_back
    assert col(back, "v")[1:3] == ["", None]


def test_csv_text_keeps_quotes_commas_newlines_and_emoji_and_protects_formulas(csv_back, world):
    out, _, back = csv_back
    # =, +, -, @ and a tab at the start get an apostrophe, and the export says how many.
    assert col(back, "v") == ["a,\"b\"", "", None, "'@SUM(A1)", "tab\tin"]
    assert col(back, "vm") == ["'=1+2", "line1\nline2 é 😀 中文", "'-5", "'+1", 'he said "hi", then left']
    assert out["protected"] == 4 and out["notes"][-1].startswith("4 text cells started with =, +, -, @")
    raw = world.export("csv", raw=True)
    world.read_back(raw["path"], "back_raw")
    again = world.rows("SELECT v, vm FROM dbo.back_raw ORDER BY id")
    assert col(again, "v") == TEXT and col(again, "vm") == LONG and raw["notes"] == []


def test_csv_decimals_beyond_a_double_are_named_and_come_back_exact_with_their_type(csv_back, world):
    _, added, back = csv_back
    problems = [i["problem"] for i in added["issues"]]
    assert ("dbo.back_csv: column d has numbers with more digits than a DOUBLE, the type read for it, holds (such "
            "as -9999999999999999999999999999.9999999999), so they read rounded. Set its type to DECIMAL(38, 10) to "
            "keep every digit.") in problems
    if back.schema.field("d0").type == pa.float64():  # DuckDB 1.x guesses DOUBLE for 38 whole digits
        assert any(p.startswith("dbo.back_csv: column d0 ") and "DECIMAL(38, 0)" in p for p in problems)
    else:  # DuckDB 2.0 guesses HUGEINT, which holds every one of them
        assert back.schema.field("d0").type == pa.decimal128(38, 0) and col(back, "d0") == col(world.source, "d0")
        assert not any(p.startswith("dbo.back_csv: column d0 ") for p in problems)
    assert any(p.startswith("dbo.back_csv: column dfrac ") and "DECIMAL(38, 38)" in p for p in problems)
    assert any(p.startswith("dbo.back_csv: column n ") and "DECIMAL(18, 4)" in p for p in problems)
    # Read as the DOUBLE DuckDB guesses, a 38-digit decimal is rounded: the named loss.
    assert back.schema.field("d").type == pa.float64() and col(back, "d")[1] == 1e28
    # The type the issue names reads the text itself, so every digit is kept.
    world.engine.set_override("dbo.back_csv", {"d": "DECIMAL(38,10)", "d0": "DECIMAL(38,0)",
                                               "dfrac": "DECIMAL(38,38)", "n": "DECIMAL(18,4)", "r": "REAL",
                                               "vb": "VARCHAR", "u": "UUID", "si": "SMALLINT", "i": "INTEGER"})
    typed = world.rows("SELECT * FROM dbo.back_csv ORDER BY id")
    for name in ("d", "d0", "dfrac", "n", "si", "i", "r"):
        assert typed.schema.field(name).type == world.source.schema.field(name).type, name
        assert_same(col(typed, name), col(world.source, name), name)
    assert col(typed, "u") == col(world.source, "u")
    issues = world.engine.project.refreshed[added["changed"][0]]["issues"]
    assert issues == [], "a column whose type was set isn't named again"


def test_csv_real_comes_back_as_float_with_the_digits_it_was_written_with(csv_back):
    # A real is written with the fewest digits that read back as the same real (-3.4028235e+38); read as a
    # float that is a different, shorter number than the real's own value (-3.4028234663852886e+38).
    _, _, back = csv_back
    assert back.schema.field("r").type == pa.float64()
    assert_same(col(back, "r"), [-3.4028235e38, 3.4028235e38, 1e-45, float("inf"), 0.1], "r")


def test_csv_binary_is_hex_text_named_and_back_to_bytes_with_convert_style_1(csv_back, world):
    _, added, back = csv_back
    problems = [i["problem"] for i in added["issues"]]
    # varbinary(16) holds only short hex, which DuckDB guesses is a whole number: 0x00FF10 would be 65,296.
    assert ("dbo.back_csv: column vb holds hex text (such as 0x00), which is read as a whole number. Set its type to "
            "VARCHAR to keep the text; in T-SQL, CONVERT(varbinary(max), vb, 1) makes it bytes again.") in problems
    assert col(back, "vb")[1] == 0x00FF10
    # varbinary(max) has 0x alone, so it's text already
    assert col(back, "vbm") == ["0x", "0xDEADBEEF00", "0x0000", None, "0x41"]
    # with vb set to VARCHAR (the test above), T-SQL makes both bytes again, exactly
    bytes_back = world.rows("SELECT CONVERT(varbinary(max), vb, 1) AS vb, CONVERT(varbinary(max), vbm, 1) AS vbm "
                            "FROM dbo.back_csv ORDER BY id")
    assert col(bytes_back, "vb") == BYTES and col(bytes_back, "vbm") == BYTES_MAX


def test_csv_uniqueidentifier_is_lower_case_text(csv_back, world):
    _, _, back = csv_back
    assert back.schema.field("u").type == pa.string()
    assert col(back, "u") == [UUID.lower(), None, "00000000-0000-0000-0000-000000000000", None, None]


# ---- Excel: the app doesn't read a workbook, so the file's cells are read here

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def cells(path: str) -> dict[str, tuple[str, str]]:
    """Each cell of the sheet by its reference: ("n" number | "b" | "s" text, the value as written), and its style."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out = {}
    for c in root.iter(f"{{{NS['m']}}}c"):
        kind = c.get("t")
        if kind == "inlineStr":
            out[c.get("r")] = ("s", "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t")), c.get("s"))
        else:
            out[c.get("r")] = ("b" if kind == "b" else "n", c.find("m:v", NS).text, c.get("s"))
    return out


def column(sheet: dict[str, tuple[str, str]], letter: str) -> list[tuple[str, str] | None]:
    return [(sheet[f"{letter}{r}"][0], sheet[f"{letter}{r}"][1]) if f"{letter}{r}" in sheet else None
            for r in range(2, 7)]


LETTERS = {name: frames._col(i) for i, name in enumerate(
    ["id", "b", "si", "i", "bi", "d", "d0", "dfrac", "n", "f", "r", "dt", "tm", "ts", "c", "v", "vm", "vb", "vbm", "u"])}


@pytest.fixture(scope="module")
def xlsx(world):
    out = world.export("xlsx")
    return out, cells(out["path"])


def test_excel_files_are_found_but_refused_by_name(xlsx, world):
    out, _ = xlsx
    added = world.read_back(out["path"], "back_xlsx")
    entry = next(t for t in added["tables"] if t["table"] == "back_xlsx")
    assert entry["status"] == "error" and entry["error"].startswith("Excel workbooks are found but can't be read yet")


def test_excel_numbers_are_exact_up_to_15_digits_and_text_with_every_digit_past_them(xlsx):
    _, sheet = xlsx
    assert column(sheet, LETTERS["b"]) == [("b", "1"), ("b", "0"), None, None, None]
    assert column(sheet, LETTERS["si"]) == [("n", "-32768"), ("n", "32767"), ("n", "0"), None, ("n", "-1")]
    assert column(sheet, LETTERS["bi"]) == [("s", "-9223372036854775808"), ("s", "9223372036854775807"), ("n", "0"),
                                            None, ("s", "1234567890123456789")]
    assert column(sheet, LETTERS["d"])[:3] == [("s", "-9999999999999999999999999999.9999999999"),
                                               ("s", "9999999999999999999999999999.9999999999"), ("n", "1E-10")]
    assert column(sheet, LETTERS["d0"])[4] == ("n", "123456789012345")  # 15 digits: a number
    assert column(sheet, LETTERS["dfrac"])[4] == ("n", "0.50000000000000000000000000000000000000")
    assert column(sheet, LETTERS["d"])[4] == ("n", "12345.6789000000")  # 9 digits, as decimal(38, 10) has them
    assert column(sheet, LETTERS["n"])[4] == ("n", "0.1000")


def test_excel_floats_keep_their_digits_and_nan_and_infinity_are_named_text(xlsx):
    _, sheet = xlsx
    # -0.0 is written as it is; Excel has no negative zero and shows 0 (the docs say so).
    assert column(sheet, LETTERS["f"]) == [("n", "-1.7976931348623157e+308"), ("n", "1.7976931348623157e+308"),
                                           ("n", "-0.0"), ("s", "nan"), ("s", "-inf")]
    # a real is written as the float it is: 0.1 as a real is 0.10000000149011612
    assert column(sheet, LETTERS["r"]) == [("n", "-3.4028234663852886e+38"), ("n", "3.4028234663852886e+38"),
                                           ("n", "1.401298464324817e-45"), ("s", "inf"), ("n", "0.10000000149011612")]


def test_excel_dates_and_times_are_serials_unless_excel_cant_hold_them(xlsx):
    _, sheet = xlsx
    assert column(sheet, LETTERS["dt"]) == [("s", "0001-01-01"), ("n", "2958465"), ("s", "1899-12-31"), ("n", "61"),
                                            ("n", "46344")]
    # a time of day is a fraction of a day; one with digits below the millisecond is text
    assert column(sheet, LETTERS["tm"]) == [("n", "0.0"), ("s", "23:59:59.999999"), ("s", "12:00:00.000100"),
                                            ("n", repr(30600.25 / 86400)), ("n", repr(86399 / 86400))]
    assert sheet[f"{LETTERS['tm']}5"][2] == "6" and sheet[f"{LETTERS['tm']}6"][2] == "5"  # hh:mm:ss.000, hh:mm:ss
    assert column(sheet, LETTERS["ts"])[:3] == [("s", "0001-01-01 00:00:00"), ("s", "9999-12-31 23:59:59.999999"),
                                                ("n", repr((dt.datetime(1900, 2, 28, 12, 0, 0, 123000)
                                                            - dt.datetime(1899, 12, 30)).total_seconds() / 86400 - 1))]


def test_excel_text_bytes_and_uniqueidentifiers_are_text_as_written(xlsx):
    _, sheet = xlsx
    # never a formula: a cell of text that starts with = stays text, so nothing gets an apostrophe
    assert column(sheet, LETTERS["vm"]) == [("s", v) for v in LONG]
    assert column(sheet, LETTERS["v"]) == [("s", "a,\"b\""), ("s", ""), None, ("s", "@SUM(A1)"), ("s", "tab\tin")]
    assert column(sheet, LETTERS["c"]) == [("s", "ab"), None, ("s", " x "), ("s", "abcde"), None]
    assert column(sheet, LETTERS["vb"]) == [("s", "0x00"), ("s", "0x00FF10"), None, None, ("s", "0xFF")]
    assert column(sheet, LETTERS["vbm"]) == [("s", "0x"), ("s", "0xDEADBEEF00"), ("s", "0x0000"), None, ("s", "0x41")]
    assert column(sheet, LETTERS["u"])[0] == ("s", UUID.lower())


def test_excel_export_says_what_it_changed(xlsx, world):
    out, _ = xlsx
    # the page shows three notes and how many more there are
    assert len(out["notes"]) == 4 and out["notes"][-1].startswith("And ")
    assert out["notes"][0] == ("bi: 3 numbers have more than 15 digits, which Excel can't hold, so they're saved as "
                               "text with every digit.")
    # every note, column by column, through the same writer
    said = {}
    for name in world.source.schema.names:
        notes = ExportNotes(world.source.select([name]).schema)
        write_xlsx(world.source.select([name]), world.root / f"{name}.xlsx", notes)
        said[name] = notes.sentences()
    assert said["f"] == ["f: 2 numbers are NaN or infinite, which Excel can't hold, so they're saved as text."]
    assert said["r"] == ["r: 1 number is NaN or infinite, which Excel can't hold, so it's saved as text."]
    assert said["dt"] == ["dt: 2 dates are before 1900, which Excel can't hold, so they're saved as text."]
    assert said["tm"] == [("tm: 2 times have digits below the millisecond, which Excel can't show, so they're saved "
                           "as text.")]
    assert said["ts"] == ["ts: 1 date is before 1900, which Excel can't hold, so it's saved as text.",
                          "ts: 1 time has digits below the millisecond, which Excel can't show, so it's saved as text."]
    assert said["v"] == ["v: Excel shows an empty text and a NULL the same way, as an empty cell."]
    assert said["d0"] == [("d0: 2 numbers have more than 15 digits, which Excel can't hold, so they're saved as text "
                           "with every digit.")]
    for name in ("id", "b", "si", "i", "c", "vm", "vb", "vbm", "u"):
        assert said[name] == [], name


def test_the_notebook_download_to_excel_says_what_it_changed_too(world, tmp_path):
    store = Frames()
    target = tmp_path / "r.xlsx"
    target.touch()
    got = store.download(store.keep(world.source.select(["id", "f", "dt"])), "xlsx", str(target))
    assert got["notes"] == ["f: 2 numbers are NaN or infinite, which Excel can't hold, so they're saved as text.",
                            "dt: 2 dates are before 1900, which Excel can't hold, so they're saved as text."]
    assert cells(str(target))["B5"][:2] == ("s", "nan")


# ---- the way back for bytes, in T-SQL

@pytest.mark.parametrize("sql,want", [
    ("SELECT CONVERT(varbinary(max), '0x00ff10', 1) AS a", b"\x00\xff\x10"),
    ("SELECT CONVERT(varbinary(max), '00FF10', 2) AS a", b"\x00\xff\x10"),
    ("SELECT CONVERT(varbinary(2), '0x00FF10', 1) AS a", b"\x00\xff"),  # at most n bytes, as in T-SQL
    ("SELECT CONVERT(varbinary(max), '0x', 1) AS a", b""),
    ("SELECT CONVERT(varbinary(max), NULL, 1) AS a", None),
    ("SELECT TRY_CONVERT(varbinary(max), '0x0', 1) AS a", None),
    ("SELECT CONVERT(varbinary(max), 'AB') AS a", b"AB"),  # style 0: the text's own bytes
    ("SELECT CONVERT(varchar(max), 0x00FF, 1) AS a", "0x00FF"),
    ("SELECT CONVERT(varchar(max), 0x00ff, 2) AS a", "00FF"),
    ("SELECT CONVERT(varchar(max), 0x4142, 0) AS a", "AB"),
    ("SELECT CONVERT(varchar(max), CONVERT(varbinary(max), '0xdead', 1), 1) AS a", "0xDEAD"),
])
def test_convert_reads_and_writes_hex_with_styles_1_and_2(world, sql, want):
    assert world.tsql(sql)["rows"] == [[repr(want) if isinstance(want, bytes) else want]]


@pytest.mark.parametrize("sql,message", [
    ("SELECT CONVERT(varbinary(max), '0x0', 1) AS a", "Error converting data type varchar to varbinary."),
    ("SELECT CONVERT(varbinary(max), '00FF', 1) AS a", "Error converting data type varchar to varbinary."),
    ("SELECT CONVERT(varbinary(max), '0x00', 2) AS a", "Error converting data type varchar to varbinary."),
    ("SELECT CONVERT(varchar(max), 0x41, 3) AS a", "CONVERT style 3 isn't a style for binary"),
])
def test_convert_refuses_what_tsql_refuses(world, sql, message):
    with pytest.raises(Exception, match=message.replace("(", r"\(").replace(")", r"\)")):
        world.tsql(sql)


def test_csv_writes_decimals_as_their_digits(csv_back):
    # Arrow writes 1E-10, and 0E-10 for a zero, which a DECIMAL(38, 38) can't read and a spreadsheet takes as a float.
    out, _, _ = csv_back
    text = Path(out["path"]).read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[3][5:9] == ["0.0000000001", "0", "0.00000000000000000000000000000000000001", "0.0000"]
    assert rows[2][5:7] == ["9999999999999999999999999999.9999999999", "9" * 38]
    # More than 6 places go out as quoted digits, which readers take as the number; fewer stay as Arrow writes them.
    assert '"0.0000000001",0,"0.00000000000000000000000000000000000001",0.0000,' in text and "E-" not in text


def test_the_notebook_download_to_csv_writes_decimals_as_their_digits_too(tmp_path):
    store = Frames()
    target = tmp_path / "r.csv"
    target.touch()
    t = pa.table({"d": pa.array([D("1E-10"), D("0"), D("-5")], pa.decimal128(38, 10)), "s": ["-5", "a", "b"]})
    got = store.download(store.keep(t), "csv", str(target))
    assert target.read_text().splitlines() == ['"d","s"', '"0.0000000001","\'-5"', '"0.0000000000","a"',
                                               '"-5.0000000000","b"']
    assert got["protected"] == 1  # the text -5 got an apostrophe; the decimal -5 didn't


# ---- 100 nanoseconds: a warehouse table keeps 6 digits of a second; a query's datetime2(7) has the 7th

def test_a_table_column_with_7_digits_of_a_second_is_refused_as_a_warehouse_refuses_it(world):
    for kind, use in (("datetime2(7)", "datetime2(6)"), ("time", "time(6)")):
        with pytest.raises(ValueError, match=rf"a Fabric Warehouse table keeps at most 6 .* Use {re.escape(use)}\."):
            world.tsql(f"CREATE TABLE dbo.seven (a {kind})")


@pytest.fixture(scope="module")
def seven(world):
    run = world.query("SELECT 1 AS id, CAST('2026-01-01 00:00:00.0000001' AS datetime2(7)) AS a, "
                      "CAST('2026-01-01 00:00:00.1234567' AS datetime2(7)) AS b, "
                      "CAST('12:00:00.1234567' AS time(7)) AS t")
    return run, world.runs.table(run)[1]


def test_a_query_keeps_the_7th_digit_of_a_datetime2_and_cuts_a_times(seven):
    _, table = seven
    assert table.schema.field("a").type == pa.timestamp("ns")
    assert [str(v) for v in table.column("a").cast(pa.string()).to_pylist()] == ["2026-01-01 00:00:00.000000100"]
    assert col(table, "t") == [dt.time(12, 0, 0, 123456)]  # time(7) is kept to the microsecond (docs: data types)


def test_parquet_keeps_100_nanoseconds(seven, world):
    run, table = seven
    out = world.export("parquet", run)
    world.read_back(out["path"], "seven_parquet")
    back = world.rows("SELECT * FROM dbo.seven_parquet")
    assert back.schema == table.schema and back.equals(table)


def test_csv_names_100_nanoseconds_a_timestamp_would_drop_and_keeps_them_as_timestamp_ns(seven, world):
    run, table = seven
    out = world.export("csv", run)
    added = world.read_back(out["path"], "seven_csv")
    problems = [i["problem"] for i in added["issues"]]
    assert problems == [
        f"dbo.seven_csv: column {c} has times with digits below the microsecond (such as 2026-01-01 00:00:00.{f}), "
        "which TIMESTAMP, the type read for it, drops. Set its type to TIMESTAMP_NS to keep them."
        for c, f in (("a", "000000100"), ("b", "123456700"))]
    assert col(world.rows("SELECT a FROM dbo.seven_csv"), "a") == [dt.datetime(2026, 1, 1)]  # the named loss
    world.engine.set_override("dbo.seven_csv", {"a": "TIMESTAMP_NS", "b": "TIMESTAMP_NS"})
    back = world.rows("SELECT a, b FROM dbo.seven_csv")
    assert back.equals(table.select(["a", "b"]))


def test_excel_writes_100_nanoseconds_as_text_and_says_so(seven, world):
    run, _ = seven
    out = world.export("xlsx", run)
    sheet = cells(out["path"])
    assert sheet["B2"][:2] == ("s", "2026-01-01 00:00:00.000000100")  # not 46023.0, midnight
    assert sheet["C2"][:2] == ("s", "2026-01-01 00:00:00.123456700")
    assert out["notes"] == [
        "a: 1 time has digits below the millisecond, which Excel can't show, so it's saved as text.",
        "b: 1 time has digits below the millisecond, which Excel can't show, so it's saved as text.",
        "t: 1 time has digits below the millisecond, which Excel can't show, so it's saved as text."]
