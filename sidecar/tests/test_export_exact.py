"""WR-221: an export keeps each value exactly, or says what it couldn't keep.

A result holding the values that lose digits most easily (decimal(38, s), times to the microsecond, datetimeoffset,
uniqueidentifier, varbinary, 64-bit whole numbers, NULL beside empty text) is exported to CSV, Excel, JSON and
Parquet through the SQL page's export, then read back and compared with the source, value by value. Where a format
can't hold a value as it is (Excel's 15 digits and milliseconds, say), the export's notes say so, by column.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pytest

from fabric_desktop import csv_cells, frames, sql_exec, warehouse
from fabric_desktop.frames import NOTE_KEY, ExportNotes

D = decimal.Decimal
UTC = dt.timezone.utc
NINES = D("99999999999999999999999999999999.999999")  # DECIMAL(38, 6)'s largest
LOWEST = D("-99999999999999999999999999999999.999999")  # written out: -NINES would round to 28 digits

SOURCE = {
    "amount": (pa.decimal128(38, 6), [NINES, LOWEST, D("0.000001"), D("12.500000"), None, D("0.000000")]),
    "whole": (pa.decimal128(38, 0), [D("12345678901234567890123456789012345678"), D("1"), None, D("-7"), D("0"),
                                     D("999999999999999")]),
    "at": (pa.timestamp("us"), [dt.datetime(2026, 11, 17, 10, 4, 5, 123456), dt.datetime(2026, 1, 1),
                                dt.datetime(2026, 1, 1, 0, 0, 0, 250000), dt.datetime(1899, 12, 31, 23, 59, 59, 1),
                                None, dt.datetime(9999, 12, 31, 23, 59, 59, 999999)]),
    "at_utc": (pa.timestamp("us", tz="UTC"), [dt.datetime(2026, 11, 17, 10, 4, 5, 123456, tzinfo=UTC), None,
                                              dt.datetime(2026, 3, 1, tzinfo=UTC), None, None, None]),
    "day": (pa.date32(), [dt.date(2026, 11, 17), dt.date(1, 1, 1), None, dt.date(1900, 2, 28), dt.date(1900, 3, 1),
                          dt.date(9999, 12, 31)]),
    "id": (pa.string(), [str(uuid.UUID(int=0)), "6F9619FF-8B86-D011-B42D-00C04FC964FF", None, "", None, None]),
    "bytes": (pa.binary(), [b"\x00\xff\xfe", b"", None, b'a,"b"\n', bytes(range(256)), b"\x80"]),
    "big": (pa.int64(), [9223372036854775807, -9223372036854775808, 2 ** 53 + 1, None, 0, 10 ** 15]),
    "text": (pa.string(), ["", None, 'a,"b"\nc', " ", "=1+1", "é漢"]),
    "ratio": (pa.float64(), [0.1, 1e300, -2.5e-300, None, 0.0, 1 / 3]),
}
ROWS = len(SOURCE["amount"][1])


def source_table() -> pa.Table:
    return pa.table({name: pa.array(values, kind) for name, (kind, values) in SOURCE.items()})


def export(table: pa.Table, fmt: str, tmp_path: Path, raw: bool = False) -> tuple[bytes, dict]:
    """Through SqlExecutions._write, the SQL page's own export, from a snapshot like a run's."""
    snapshot = tmp_path / f"snapshot-{fmt}.arrow"
    with ipc.new_file(str(snapshot), table.schema) as writer:
        writer.write_table(table)
    out = tmp_path / f"result.{fmt}"
    out.touch()  # the name the export reserved
    job = sql_exec.Export(id="e1", execution="r1", format=fmt, path=out, total=table.num_rows, raw=raw)
    object.__new__(sql_exec.SqlExecutions)._write(snapshot, job)
    status = job.status()
    assert status["state"] == "completed", status["error"]
    return out.read_bytes(), status


# ---- reading each file back


def csv_back(data: bytes) -> pa.Table:
    """With the source's types; quoted "" is empty text and an empty field NULL."""
    types = {n: (pa.string() if pa.types.is_binary(k) else k) for n, (k, _) in SOURCE.items()}
    table = pa_csv.read_csv(io.BytesIO(data), convert_options=pa_csv.ConvertOptions(
        column_types=types, strings_can_be_null=True, quoted_strings_can_be_null=False))
    hexed = table.column("bytes").to_pylist()
    return table.set_column(table.schema.get_field_index("bytes"), pa.field("bytes", pa.binary()),
                            pa.array([None if v is None else bytes.fromhex(v[2:]) for v in hexed], pa.binary()))


def json_back(data: bytes) -> dict[str, list]:
    rows = json.loads(data)
    readers = {
        "amount": D, "whole": D, "day": dt.date.fromisoformat, "bytes": base64.b64decode,
        "at": dt.datetime.fromisoformat, "at_utc": dt.datetime.fromisoformat,
    }
    return {name: [None if r[name] is None else readers.get(name, lambda v: v)(r[name]) for r in rows]
            for name in SOURCE}


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_EPOCH = dt.datetime(1899, 12, 30)


def excel_back(data: bytes) -> dict[str, list]:
    """Each cell as Excel reads it: a number to 15 digits, a date serial to the millisecond, text as text,
    and an empty cell as None. Serials below 60 are a day short of days since 1899-12-30, since Excel counts a
    29 February 1900 that never was."""
    sheet = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml"))
    names = list(SOURCE)
    out: dict[str, list] = {n: [None] * ROWS for n in names}
    for row in sheet.iterfind(".//m:row", _NS):
        r = int(row.get("r")) - 2
        if r < 0:
            continue
        for c in row.iterfind("m:c", _NS):
            col = names[_col_index(re.match(r"[A-Z]+", c.get("r"))[0])]
            if c.get("t") == "inlineStr":
                out[col][r] = c.find("m:is/m:t", _NS).text or ""
                continue
            raw = c.find("m:v", _NS).text
            if c.get("s") in ("2", "4"):
                ms = round(float(raw) * 86_400_000)
                out[col][r] = _EPOCH + dt.timedelta(milliseconds=ms + (86_400_000 if float(raw) < 60 else 0))
            elif c.get("s") == "3":
                days = int(raw)
                out[col][r] = (_EPOCH + dt.timedelta(days=days + (1 if days < 60 else 0))).date()
            else:
                out[col][r] = float(f"{float(raw):.15g}") if c.get("t") != "b" else raw == "1"
    return out


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


# ---- each format, value by value


def test_csv_gives_every_value_back_exactly(tmp_path):
    data, status = export(source_table(), "csv", tmp_path, raw=True)  # Keep raw values (WR-228)
    back = csv_back(data)
    for name, (_, values) in SOURCE.items():
        assert back.column(name).to_pylist() == values, name
    assert status["notes"] == [] and status["protected"] == 0


def test_csv_protects_a_text_cell_that_starts_like_a_formula_and_says_so(tmp_path):
    data, status = export(source_table(), "csv", tmp_path)
    back = csv_back(data)
    for name, (_, values) in SOURCE.items():
        want = ["'" + v if isinstance(v, str) and v.startswith(csv_cells.STARTS) else v for v in values]
        assert back.column(name).to_pylist() == want, name
    assert status["protected"] >= 1 and status["notes"] == [csv_cells.note(status["protected"])]


def test_csv_writes_binary_as_hex_and_a_byte_that_isnt_text_doesnt_stop_it(tmp_path):
    data, _ = export(pa.table({"b": pa.array([b"\x00\xff", b"", None], pa.binary())}), "csv", tmp_path)
    assert data.decode("ascii").splitlines() == ['"b"', '"0x00FF"', '"0x"', ""]


def test_csv_keeps_null_and_empty_text_apart(tmp_path):
    data, _ = export(pa.table({"t": pa.array(["", None, "x"], pa.string())}), "csv", tmp_path)
    assert data.decode().splitlines() == ['"t"', '""', "", '"x"']


def test_csv_writes_a_list_column_as_json_text(tmp_path):
    data, _ = export(pa.table({"l": pa.array([[1, 2], None], pa.list_(pa.int64()))}), "csv", tmp_path)
    assert data.decode().splitlines() == ['"l"', '"[1, 2]"', ""]


def test_json_gives_every_value_back_exactly(tmp_path):
    data, status = export(source_table(), "json", tmp_path)
    back = json_back(data)
    for name, (_, values) in SOURCE.items():
        assert back[name] == values, name
    assert status["notes"] == [("big: 3 whole numbers are beyond 9,007,199,254,740,991. They're exact in the file, "
                                "but JavaScript and Power Query read them rounded.")]


def test_parquet_gives_every_value_back_exactly(tmp_path):
    data, status = export(source_table(), "parquet", tmp_path)
    back = pq.read_table(io.BytesIO(data))
    assert back.schema.types == source_table().schema.types
    assert back.to_pylist() == source_table().to_pylist()
    assert status["notes"] == []


def test_excel_keeps_what_it_can_hold_as_values_and_the_rest_as_exact_text(tmp_path, monkeypatch):
    monkeypatch.setattr(frames, "MAX_NOTES", 50)  # every note, to check each one
    data, status = export(source_table(), "xlsx", tmp_path)
    back = excel_back(data)
    assert back["amount"] == [str(NINES), str(LOWEST), 1e-06, 12.5, None, 0.0]  # 38 digits as text
    assert back["whole"] == ["12345678901234567890123456789012345678", 1.0, None, -7.0, 0.0, 999999999999999.0]
    assert back["at"] == ["2026-11-17 10:04:05.123456", dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 1, 0, 0, 0, 250000),
                          "1899-12-31 23:59:59.000001", None, "9999-12-31 23:59:59.999999"]
    assert back["at_utc"] == ["2026-11-17 10:04:05.123456", None, dt.datetime(2026, 3, 1), None, None, None]
    assert back["day"] == [dt.date(2026, 11, 17), "0001-01-01", None, dt.date(1900, 2, 28), dt.date(1900, 3, 1),
                           dt.date(9999, 12, 31)]
    assert back["id"] == SOURCE["id"][1]
    assert back["bytes"] == ["0x00FFFE", "0x", None, "0x612C2262220A", "0x" + bytes(range(256)).hex().upper(), "0x80"]
    assert back["big"] == ["9223372036854775807", "-9223372036854775808", "9007199254740993", None, 0.0,
                           "1000000000000000"]
    assert back["text"] == ["", None, 'a,"b"\nc', " ", "=1+1", "é漢"]
    assert back["ratio"][:3] == [0.1, 1e300, -2.5e-300]
    assert status["notes"] == [
        "amount: 2 numbers have more than 15 digits, which Excel can't hold, so they're saved as text with every digit.",
        "whole: 1 number has more than 15 digits, which Excel can't hold, so it's saved as text with every digit.",
        "at: 2 times have digits below the millisecond, which Excel can't show, so they're saved as text.",
        "at: 1 date is before 1900, which Excel can't hold, so it's saved as text.",
        "at_utc: Excel has no time zones, so these times are saved as clock times in UTC.",
        "at_utc: 1 time has digits below the millisecond, which Excel can't show, so it's saved as text.",
        "day: 1 date is before 1900, which Excel can't hold, so it's saved as text.",
        "id: Excel shows an empty text and a NULL the same way, as an empty cell.",
        "big: 4 numbers have more than 15 digits, which Excel can't hold, so they're saved as text with every digit.",
        "text: Excel shows an empty text and a NULL the same way, as an empty cell.",
    ]


def test_excel_shows_milliseconds_and_whole_seconds_with_their_own_formats(tmp_path):
    data, _ = export(source_table(), "xlsx", tmp_path)
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    styles = zipfile.ZipFile(io.BytesIO(data)).read("xl/styles.xml").decode()
    assert re.search(r'<c r="C3" s="2">', sheet) and re.search(r'<c r="C4" s="4">', sheet)
    assert 'numFmtId="165" formatCode="yyyy-mm-dd hh:mm:ss.000"' in styles


def test_excel_says_when_it_cuts_or_strips_text(tmp_path):
    long, odd = "x" * 32_768, "a\x01b"
    data, status = export(pa.table({"t": pa.array([long, odd, "ok"], pa.string())}), "xlsx", tmp_path)
    assert excel_back_column(data) == ["x" * 32_767, "ab", "ok"]
    assert status["notes"] == [
        "t: 1 text is longer than 32,767 characters, Excel's limit for a cell, so it's cut there.",
        "t: 1 text has control characters that an Excel file can't hold, so they're left out."]


def test_excel_says_a_time_in_another_zone_is_saved_as_its_clock_time(tmp_path):
    zone = dt.timezone(dt.timedelta(hours=-5))
    table = pa.table({"t": pa.array([dt.datetime(2026, 1, 1, 6, tzinfo=zone)], pa.timestamp("us", tz="-05:00"))})
    data, status = export(table, "xlsx", tmp_path)
    assert status["notes"] == [("t: Excel has no time zones, so these times are saved as their clock time, without "
                                "the zone.")]


def excel_back_column(data: bytes) -> list[str]:
    sheet = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml"))
    return [t.text or "" for t in sheet.iterfind(".//m:row/m:c/m:is/m:t", _NS)][1:]


def test_an_exact_result_has_no_notes_in_any_format(tmp_path):
    plain = pa.table({"n": pa.array([1, 2], pa.int64()), "t": pa.array(["a", None], pa.string()),
                      "d": pa.array([D("1.50"), None], pa.decimal128(10, 2))})
    for fmt in ("csv", "xlsx", "json", "parquet"):
        assert export(plain, fmt, tmp_path)[1]["notes"] == [], fmt


def test_notes_stop_at_three_and_a_count_of_the_rest(tmp_path):
    many = pa.table({f"c{i}": pa.array([10 ** 20 + 1], pa.decimal128(38, 0)) for i in range(20)})
    notes = export(many, "xlsx", tmp_path)[1]["notes"]
    assert len(notes) == 4 and notes[:3] == [
        f"c{i}: 1 number has more than 15 digits, which Excel can't hold, so it's saved as text with every digit."
        for i in range(3)] and notes[-1] == "And 17 more notes like these."


# ---- what the warehouse result itself doesn't keep, said with every export


def test_a_warehouse_seventh_digit_and_offset_are_said_with_the_export(tmp_path):
    description = [("at", dt.datetime, None, 27, 27, 7, True), ("at_tz", dt.datetime, None, 34, 34, 7, True),
                   ("plain", dt.datetime, None, 23, 23, 3, True), ("t", dt.time, None, 16, 16, 7, True)]
    aware = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    rows = [(dt.datetime(2026, 1, 1), aware, dt.datetime(2026, 1, 1), dt.time(1, 2, 3))]
    schema = warehouse._schema(description, rows)
    assert schema.field("plain").metadata is None
    assert warehouse._batch(schema, rows).column(1).to_pylist() == [aware.astimezone(UTC)]  # the same instant
    table = pa.Table.from_batches([warehouse._batch(schema, rows)])
    for fmt in ("csv", "xlsx", "json", "parquet"):
        notes = export(table, fmt, tmp_path)[1]["notes"]
        assert notes[0] == ("at: times are kept to the microsecond, so the seventh digit of their fraction isn't "
                            "kept. To keep it, convert the column to text in the query, such as "
                            "CAST(col AS VARCHAR(34)).")
        assert notes[1] == ("at_tz: times are kept to the microsecond and given in UTC, so the seventh digit of their "
                            "fraction and the offset each was written with aren't kept. To keep them, convert the "
                            "column to text in the query, such as CAST(col AS VARCHAR(34)).")
        assert notes[2].startswith("t: times are kept to the microsecond"), fmt
        assert not any(n.startswith("plain:") for n in notes)


@pytest.mark.parametrize("scale, aware, start", [
    (7, False, "times are kept to the microsecond, so the seventh digit of their fraction isn't kept. To keep it,"),
    (3, True, "datetimeoffset values are given in UTC, without the offset each was written with. To keep the offset,"),
    (None, True, "datetimeoffset values are given in UTC"),
    (7, True, ("times are kept to the microsecond and given in UTC, so the seventh digit of their fraction and the "
               "offset each was written with aren't kept. To keep them,")),
    (6, False, ""), (None, False, ""),
])
def test_each_time_note_is_one_sentence_with_one_tip(scale, aware, start):
    note = warehouse._time_note(scale, aware)
    assert note.startswith(start) and ("; " not in note)
    assert note.count("CAST(col AS VARCHAR(34))") == (1 if start else 0)


def test_a_field_note_reads_back_from_the_snapshot():
    schema = pa.schema([pa.field("a", pa.int64(), metadata={NOTE_KEY: b"said once"}), pa.field("b", pa.int64())])
    assert ExportNotes(schema).sentences() == ["a: said once."]


def test_the_status_carries_notes_only_once_the_file_is_done(tmp_path):
    job = sql_exec.Export(id="e", execution="r", format="csv", path=tmp_path / "x.csv")
    assert job.status()["notes"] == []


# ---- end to end, a local T-SQL result through the managed run and its export


@pytest.fixture
def local_runs(tmp_path):
    from fabric_desktop.localdata import LocalData

    local = LocalData(tmp_path / "local-data")
    pid = local.create("Proj")["id"]
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", downloads=lambda: tmp_path / "Downloads",
                                     local_engine=local.engine)
    manager.pid = pid  # type: ignore[attr-defined]
    yield manager
    manager.shutdown()
    local.close_all()


def _wait(check, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = check()
        if status["state"] not in ("queued", "running"):
            return status
        time.sleep(0.05)
    raise AssertionError("still running")


def _exported(manager, run_id, fmt):
    export_id = manager.export(run_id, fmt)["id"]  # started once, then waited on (not a new export each poll)
    return _wait(lambda: manager.export_status(export_id))


def test_a_local_tsql_result_exports_exactly(local_runs):
    query = ("SELECT CAST('99999999999999999999999999999999.999999' AS DECIMAL(38, 6)) AS amount, "
             "CAST('2026-11-17 10:04:05.123456' AS DATETIME2(6)) AS at, CAST(9223372036854775807 AS BIGINT) AS big, "
             "'' AS empty, CAST(NULL AS VARCHAR(10)) AS nothing")
    run = local_runs.start(query, target="local", project=local_runs.pid, dialect="tsql")
    assert _wait(lambda: local_runs.status(run["id"]))["state"] == "completed"
    csv = _exported(local_runs, run["id"], "csv")
    assert Path(csv["path"]).read_text().splitlines()[1] == \
        '99999999999999999999999999999999.999999,2026-11-17 10:04:05.123456,9223372036854775807,"",'
    xlsx = _exported(local_runs, run["id"], "xlsx")
    assert [n.split(":")[0] for n in xlsx["notes"]] == ["amount", "at", "big", "empty"]
    js = _exported(local_runs, run["id"], "json")
    row = json.loads(Path(js["path"]).read_text())[0]
    assert row == {"amount": "99999999999999999999999999999999.999999", "at": "2026-11-17T10:04:05.123456",
                   "big": 9223372036854775807, "empty": "", "nothing": None}
