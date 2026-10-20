"""Whole results behind a notebook table output: paging, search, sort and downloads.

A cell's table output carries the first ``PREVIEW_ROWS`` rows. The value it came from is kept here
under the output's ``frame`` id, so the result viewer can page through every row, search and sort
all of them, and download the whole result. The kernel keeps the last ``MAX_FRAMES`` results; a
restart clears them, and the viewer then asks for the cell to be run again.

A result is turned into an Arrow table the first time the viewer asks for more than the preview,
and that table is reused, so paging a lakehouse query doesn't read OneLake again. Queries run on a
private DuckDB connection over that table, with columns renamed ``c0..cN`` so any column name
(duplicates, quotes) is safe in SQL.

Downloads are written by the kernel to a ``.partial`` file next to a name the engine reserved in
Downloads, then moved over it: a download never replaces an existing file, and one that fails
leaves nothing behind.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import threading
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pyarrow as pa

MAX_FRAMES = 20
PAGE_ROWS = 100
MAX_PAGE_ROWS = 1000
EXCEL_MAX_ROWS = 1_048_575  # a sheet's 1,048,576 rows less the header
DOWNLOAD_FORMATS = ("csv", "xlsx", "parquet")
class FrameGone(LookupError):
    """The result an output points at isn't held any more."""


GONE = "This result is no longer held by the kernel (it restarted, or newer results replaced it). Run the cell again."


def full_table(value: Any) -> pa.Table:
    """Every row of a data frame-like value as Arrow."""
    module = type(value).__module__
    if isinstance(value, pa.Table):
        return value
    if module.startswith("duckdb.experimental.spark"):
        rel = value.relation
        return (getattr(rel, "to_arrow_table", None) or rel.arrow)()
    if module.startswith("polars"):
        if hasattr(value, "collect"):
            value = value.collect()
        return value.to_arrow()
    if module.startswith("duckdb"):
        return (getattr(value, "to_arrow_table", None) or value.arrow)()
    if module.startswith("pandas"):
        return pa.Table.from_pandas(value, preserve_index=False)
    raise TypeError(f"{type(value).__name__} isn't a table.")


def type_name(t: pa.DataType) -> str:
    """A short type label for a column header, as Fabric and Databricks show them."""
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t):
        return "double"
    if pa.types.is_decimal(t):
        return f"decimal({t.precision},{t.scale})"
    if pa.types.is_timestamp(t):
        return "timestamp"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_time(t):
        return "time"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    if pa.types.is_binary(t) or pa.types.is_large_binary(t):
        return "binary"
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return "array"
    if pa.types.is_struct(t):
        return "struct"
    if pa.types.is_map(t):
        return "map"
    return str(t)


def _rows(result: Any) -> list[list[Any]]:
    """A query's rows through Arrow: DuckDB's own fetchall() needs pytz for time zone-aware
    timestamps (Delta's default), and the engine doesn't ship pytz."""
    table = (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()
    cols = [c.to_pylist() for c in table.columns]
    return [list(r) for r in zip(*cols)] if cols else []


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class Frames:
    """The results a kernel's table outputs point at. Thread-safe; one per kernel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: OrderedDict[str, Any] = OrderedDict()
        self._tables: dict[str, pa.Table] = {}

    def keep(self, value: Any) -> str:
        fid = uuid.uuid4().hex[:12]
        with self._lock:
            self._values[fid] = value
            while len(self._values) > MAX_FRAMES:
                old, _ = self._values.popitem(last=False)
                self._tables.pop(old, None)
        return fid

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._tables.clear()

    def table(self, fid: str) -> pa.Table:
        with self._lock:
            if fid not in self._values:
                raise FrameGone(GONE)
            table = self._tables.get(fid)
            value = self._values[fid]
            self._values.move_to_end(fid)
        if table is None:
            table = full_table(value)
            with self._lock:
                if fid in self._values:
                    self._tables[fid] = table
        return table

    # ---- the viewer

    def page(self, fid: str, offset: int = 0, limit: int = PAGE_ROWS, sort: int | None = None,
             desc: bool = False, q: str = "") -> dict[str, Any]:
        """One page of the result, searched and sorted over every row."""
        import duckdb
        table = self.table(fid)
        offset = max(0, int(offset))
        limit = min(max(1, int(limit)), MAX_PAGE_ROWS)
        cols = [f"c{i}" for i in range(table.num_columns)]
        con = duckdb.connect()
        try:
            con.register("t", table.rename_columns(cols))
            where, params = "", []
            if q:
                where = " WHERE " + " OR ".join(f"CAST({c} AS VARCHAR) ILIKE ? ESCAPE '\\'" for c in cols)
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                params = [like] * len(cols)
            order = ""
            if sort is not None and 0 <= int(sort) < len(cols):
                order = f" ORDER BY {cols[int(sort)]} {'DESC' if desc else 'ASC'} NULLS LAST"
            total = con.execute(f"SELECT count(*) FROM t{where}", params).fetchone()[0] if q else table.num_rows
            rows = _rows(con.execute(f"SELECT * FROM t{where}{order} LIMIT {limit} OFFSET {offset}", params))
        finally:
            con.close()
        return {"frame": fid, "columns": table.schema.names, "types": [type_name(f.type) for f in table.schema],
                "rows": [[_cell(v) for v in r] for r in rows], "offset": offset, "total": total,
                "all_rows": table.num_rows}

    # ---- downloads

    def download(self, fid: str, fmt: str, path: str) -> dict[str, Any]:
        """Write the whole result to ``path`` (a name the engine reserved, empty) in ``fmt``."""
        if fmt not in DOWNLOAD_FORMATS:
            raise ValueError(f"Download as {', '.join(DOWNLOAD_FORMATS)}.")
        table = self.table(fid)
        if fmt == "xlsx" and table.num_rows > EXCEL_MAX_ROWS:
            raise ValueError(f"Excel holds at most {EXCEL_MAX_ROWS:,} rows and this result has "
                             f"{table.num_rows:,}. Download it as CSV or Parquet instead.")
        target = Path(path)
        partial = target.with_name(target.name + ".partial")
        try:
            with open(partial, "xb") as sink:
                if fmt == "csv":
                    import pyarrow.csv as pa_csv
                    pa_csv.write_csv(table, sink)
                elif fmt == "parquet":
                    import pyarrow.parquet as pq
                    pq.write_table(table.rename_columns(_unique(table.schema.names)), sink)
                else:
                    write_xlsx(table, sink)
            os.replace(partial, target)  # over the empty file that reserved the name
        except BaseException:
            with_suppressed_unlink(partial)
            raise
        return {"path": str(target), "rows": table.num_rows, "format": fmt}


def with_suppressed_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:  # never written, or already gone
        return


def _unique(names: list[str]) -> list[str]:
    """Parquet can't be read back with two columns of one name: the second ``a`` becomes ``a_2``."""
    taken = {n.lower() for n in names}
    seen: dict[str, int] = {}
    out = []
    for name in names:
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 1:
            out.append(name)
            continue
        n = seen[key]
        while f"{name}_{n}".lower() in taken:
            n += 1
        taken.add(f"{name}_{n}".lower())
        out.append(f"{name}_{n}")
    return out


# ---- a small Excel writer: one sheet, a bold header, numbers as numbers and dates as dates

_EPOCH = dt.datetime(1899, 12, 30)


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_cell(ref: str, v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float, decimal.Decimal)) and not (isinstance(v, float) and v != v):
        if isinstance(v, float) and v in (float("inf"), float("-inf")):
            return _text(ref, str(v))
        return f'<c r="{ref}"><v>{v}</v></c>'
    if isinstance(v, dt.datetime):
        serial = ((v.replace(tzinfo=None) if v.tzinfo else v) - _EPOCH).total_seconds() / 86400
        return f'<c r="{ref}" s="2"><v>{serial}</v></c>'
    if isinstance(v, dt.date):
        return f'<c r="{ref}" s="3"><v>{(dt.datetime(v.year, v.month, v.day) - _EPOCH).days}</v></c>'
    return _text(ref, v if isinstance(v, str) else str(v))


_BAD_XML = dict.fromkeys(c for c in range(32) if c not in (9, 10, 13))


def _text(ref: str, s: str) -> str:
    # Excel would run text starting with = + - @ as a formula only if it were a formula cell; inline
    # strings are always text, so a value like "=HYPERLINK(...)" stays text.
    s = escape(s.translate(_BAD_XML)[:32767])
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{s}</t></is></c>'


def write_xlsx(table: pa.Table, sink: Any) -> None:
    names = table.schema.names
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Result" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="4"><xf/><xf fontId="1" applyFont="1"/>'
                   '<xf numFmtId="164" applyNumberFormat="1"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs>'
                   '</styleSheet>')
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as sheet:
            sheet.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        b'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews>'
                        b'<sheetData>')
            head = "".join(_text(f"{_col(i)}1", n).replace('t="inlineStr"', 't="inlineStr" s="1"') for i, n in enumerate(names))
            sheet.write(f'<row r="1">{head}</row>'.encode())
            r = 2
            for batch in table.to_batches(max_chunksize=8192):
                cols = [c.to_pylist() for c in batch.columns]
                for i in range(batch.num_rows):
                    cells = "".join(_xlsx_cell(f"{_col(j)}{r}", col[i]) for j, col in enumerate(cols))
                    sheet.write(f'<row r="{r}">{cells}</row>'.encode("utf-8"))
                    r += 1
            sheet.write(b"</sheetData></worksheet>")
