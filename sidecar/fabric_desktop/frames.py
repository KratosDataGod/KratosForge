"""Whole results behind a notebook table output: paging, search, sort, a profile and downloads.

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
import json
import logging
import math
import random
import threading
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pyarrow as pa

from .redact import describe
from .duckdb_rows import for_arrow, one_row
from . import jsonfile

_log = logging.getLogger(__name__)

MAX_FRAMES = 20
PAGE_ROWS = 100
MAX_PAGE_ROWS = 1000
EXCEL_MAX_ROWS = 1_048_575  # a sheet's 1,048,576 rows less the header
DOWNLOAD_FORMATS = ("csv", "xlsx", "parquet")
PROFILE_COLUMNS = 200
CHART_KINDS = ("bar", "stacked", "line", "area", "scatter", "pie", "histogram", "box")
CHART_AGGS = ("sum", "avg", "count", "min", "max")
CHART_CATEGORIES = 60
CHART_SERIES = 7  # the categorical palette's slots; the rest fold into Other
CHART_POINTS = 5000
OTHER = "Other"
PROFILE_ROWS = 250_000  # above this, a random sample: about 5 s for 50 columns on a laptop
HISTOGRAM_BINS = 12
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
        return pandas_table(value)
    raise TypeError(f"{type(value).__name__} isn't a table.")


def pandas_plan(df: Any) -> tuple[list[str] | None, list[str] | None]:
    """How a pandas frame shows as a table: (names for its index columns, or None to leave the index
    out; flat column names, or None to keep them).

    A named index (a groupby or set_index result) is data and shows as leading columns, as Jupyter
    shows it; an unnamed one is row numbers and is left out. An index name that a column already has
    gets "_index" added, so both show. Multi-level columns (``agg({'v': ['sum', 'max']})``) join their
    levels with "_" (``v_sum``). A repeated column name gets "_2", "_3" so every column shows."""
    multi = getattr(df.columns, "nlevels", 1) > 1
    names = ["_".join(str(p) for p in c if str(p) != "") if multi else str(c) for c in df.columns]
    seen: set[str] = set()
    flat = []
    for base in names:  # a repeated name (columns=["x", "x"], or joined levels that meet) gets _2, _3
        name, k = base, 2
        while name in seen:
            name, k = f"{base}_{k}", k + 1
        seen.add(name)
        flat.append(name)
    cols = flat if multi or flat != names else None
    levels = list(getattr(df.index, "names", []) or [])
    if not any(n is not None for n in levels):
        return None, cols
    taken = set(flat)
    index = []
    for i, n in enumerate(levels):
        base = str(n) if n is not None else f"level_{i}"
        name, k = base, 1
        while name in taken:
            name = f"{base}_index" if k == 1 else f"{base}_index_{k}"
            k += 1
        taken.add(name)
        index.append(name)
    return index, cols


def pandas_frame(df: Any) -> Any:
    """``df`` as ``pandas_plan`` shows it."""
    index, cols = pandas_plan(df)
    if cols is not None:
        df = df.set_axis(cols, axis=1)
    return df.reset_index(names=index) if index is not None else df


def pandas_reader(df: Any, name: str) -> str:
    """The pandas code that turns the variable ``name`` into what ``pandas_frame`` gives."""
    index, cols = pandas_plan(df)
    code = name
    if cols is not None:
        code += f".set_axis({cols!r}, axis=1)"
    if index is not None:
        code += f".reset_index(names={index!r})"
    return code


def pandas_table(df: Any) -> pa.Table:
    return pa.Table.from_pandas(pandas_frame(df), preserve_index=False)


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


def _finite(c: str) -> str:
    """Rows a number column can be charted from: not null, and not NaN or infinite."""
    return f"({c} IS NOT NULL AND isfinite(TRY_CAST({c} AS DOUBLE)))"


def _kind(t: pa.DataType) -> str:
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "number"
    if pa.types.is_temporal(t):
        return "date"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "text"
    return "other"


def _num(v: Any) -> float | int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    f = float(v)
    return round(f, 6) if f == f else None


def _chart_key(v: Any) -> Any:
    """An x value as a dict key: NaN never equals itself and lists don't hash, so a NaN or list
    category would lose its value (or fail the chart)."""
    if isinstance(v, float) and math.isnan(v):
        return ("nan",)
    if isinstance(v, (list, dict)):
        return ("json", json.dumps(v, default=str, sort_keys=True))
    return v


def _rows(result: Any) -> list[list[Any]]:
    """A query's rows through Arrow: DuckDB's own fetchall() needs pytz for time zone-aware
    timestamps (Delta's default), and the engine doesn't ship pytz."""
    table = (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()
    cols = [c.to_pylist() for c in table.columns]
    return [list(r) for r in zip(*cols)] if cols else []


def _cell(value: Any) -> Any:
    """A result cell: JSON (and the shell) has no NaN or infinity, so those show as text."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
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
            total = one_row(con.execute(f"SELECT count(*) FROM t{where}", params))[0] if q else table.num_rows
            rows = _rows(for_arrow(con, f"SELECT * FROM t{where}{order} LIMIT {limit} OFFSET {offset}", params))
        finally:
            con.close()
        return {"frame": fid, "columns": table.schema.names, "types": [type_name(f.type) for f in table.schema],
                "rows": [[_cell(v) for v in r] for r in rows], "offset": offset, "total": total,
                "all_rows": table.num_rows}

    # ---- the profile (Inspect, display(df, summary=True))

    def query_profile(self, fid: str) -> dict[str, Any]:
        """How the query behind a ``spark`` or ``%%sql`` result ran, step by step (spark_local.query_profile)."""
        with self._lock:
            if fid not in self._values:
                raise FrameGone(GONE)
            value = self._values[fid]
        from . import spark_local
        return spark_local.query_profile(value)

    def profile(self, fid: str) -> dict[str, Any]:
        """A profile of every row, per column: missing, distinct (approximate), and for numbers min,
        max, mean, standard deviation, quartiles and a histogram; for dates their range; for
        everything else the most common values. NaN counts as missing, as in Fabric's summary;
        infinite values are counted but left out of the number statistics.

        A result over ``PROFILE_ROWS`` rows is profiled from a random sample of that many rows (the
        answer says so), so Inspect stays quick on a large result. A column that can't be
        profiled says why and the others still are."""
        import duckdb
        whole = self.table(fid)
        table = whole
        sampled = whole.num_rows > PROFILE_ROWS
        if sampled:
            # A random sample (the same one each time): an every-nth-row sample skews data that repeats.
            picks = sorted(random.Random(0).sample(range(whole.num_rows), PROFILE_ROWS))
            table = whole.take(pa.array(picks))
        fields = list(table.schema)[:PROFILE_COLUMNS]
        cols = [f"c{i}" for i in range(table.num_columns)]
        con = duckdb.connect()
        out = []
        try:
            con.register("t", table.rename_columns(cols))
            n = table.num_rows
            for i, f in enumerate(fields):
                try:
                    out.append(self._profile_column(con, cols[i], f, n))
                except Exception as exc:  # noqa: BLE001 - one odd column mustn't hide the others
                    _log.info("Couldn't profile column %s: %s", f.name, describe(exc))
                    out.append({"name": f.name, "type": type_name(f.type), "kind": _kind(f.type),
                                "error": f"Couldn't profile this column: {describe(exc)}"})
        finally:
            con.close()
        return {"frame": fid, "rows": whole.num_rows, "profiled_rows": n, "sampled": sampled, "columns": out,
                "more_columns": max(0, table.num_columns - PROFILE_COLUMNS)}
    # ---- Data Wrangler (Clean view): steps written as Polars code, previewed on every row

    def wrangle(self, fid: str, steps: list[dict[str, Any]], name: str = "df") -> dict[str, Any]:
        """The Polars code for ``steps`` and a preview of what it does to the result (wrangler.py)."""
        from . import wrangler
        table = self.table(fid)
        with self._lock:
            value = self._values.get(fid)
        out = wrangler.run(table, value, list(steps or []), name)
        out["frame"] = fid
        return out

    # ---- the chart builder (Chart view): aggregates over every row

    def chart(self, fid: str, kind: str = "bar", x: int | None = None, y: int | None = None, agg: str = "sum",
              series: int | None = None, bins: int = 20) -> dict[str, Any]:
        """What a chart of the whole result draws. ``x``, ``y`` and ``series`` are column positions.

        bar, line, area and pie: ``agg`` of ``y`` (or a row count) per ``x`` value, one list of
        values per ``series`` value; at most ``CHART_CATEGORIES`` x values (bar and pie keep the
        largest; line and area keep the first in x order) and ``CHART_SERIES`` series, the rest
        folded into "Other". scatter: up to ``CHART_POINTS`` (x, y, series) rows, sampled evenly.
        histogram: ``bins`` equal-width counts of ``y``. box: min, quartiles and max of ``y`` per x."""
        import duckdb
        if kind not in CHART_KINDS:
            raise ValueError(f"Chart kinds are {', '.join(CHART_KINDS)}.")
        if agg not in CHART_AGGS:
            raise ValueError(f"Aggregates are {', '.join(CHART_AGGS)}.")
        table = self.table(fid)
        cols = [f"c{i}" for i in range(table.num_columns)]

        def col(i: int | None, what: str) -> str | None:
            if i is None:
                return None
            if not 0 <= int(i) < len(cols):
                raise ValueError(f"The chart's {what} column isn't in this result.")
            return cols[int(i)]
        cx, cy, cs = col(x, "X axis"), col(y, "Values"), col(series, "Series")

        def number(i: int | None, c: str | None, what: str, boolean: bool = False) -> None:
            """Refused here with a plain message, not as a DuckDB binder error."""
            if c is None:
                return
            f = table.schema.field(int(i))
            if _kind(f.type) != "number" and not (boolean and _kind(f.type) == "boolean"):
                raise ValueError(f"{f.name!r} isn't a number column. Pick a number column for {what}"
                                 + (", or count rows." if what == "Values" and boolean else "."))
        if kind in ("histogram", "box", "scatter"):
            number(y, cy, "Values")
        elif agg != "count":
            number(y, cy, "Values", boolean=True)
        if kind == "scatter":
            number(x, cx, "the X axis")
        con = duckdb.connect()
        try:
            con.register("t", table.rename_columns(cols))
            out: dict[str, Any] = {"frame": fid, "kind": kind, "rows": table.num_rows}
            if kind == "histogram":
                if cy is None:
                    raise ValueError("Pick a number column for Values.")
                lo, hi = one_row(con.execute(f"SELECT min({cy})::DOUBLE, max({cy})::DOUBLE FROM t WHERE {_finite(cy)}"))
                n = max(1, min(int(bins), 100))
                counts = [0] * n
                if lo is not None:
                    span = (hi - lo) or 1.0
                    for b, k in con.execute(f"SELECT least({n - 1}, floor(({cy}::DOUBLE - ?) / ? * {n}))::INT AS b, count(*) "
                                            f"FROM t WHERE {_finite(cy)} GROUP BY b", [lo, span]).fetchall():
                        counts[int(b)] = k
                out.update(min=lo, max=hi, counts=counts)
                return out
            if cx is None:
                raise ValueError("Pick a column for the X axis.")
            if kind == "scatter":
                if cy is None:
                    raise ValueError("Pick a number column for Values.")
                total = one_row(con.execute(f"SELECT count(*) FROM t WHERE {_finite(cx)} AND {_finite(cy)}"))[0]
                every = max(1, -(-total // CHART_POINTS))
                pts = con.execute(
                    f"SELECT {cx}::DOUBLE, {cy}::DOUBLE, {('CAST(' + cs + ' AS VARCHAR)') if cs else 'NULL'} FROM "
                    f"(SELECT *, row_number() OVER () AS rn FROM t WHERE {_finite(cx)} AND {_finite(cy)}) WHERE rn % {every} = 0 "
                    f"LIMIT {CHART_POINTS}").fetchall()
                out.update(points=[[a, b, c] for a, b, c in pts], sampled=every > 1, total=total)
                return out
            if kind == "box":
                if cy is None:
                    raise ValueError("Pick a number column for Values.")
                got = con.execute(
                    f"SELECT CAST({cx} AS VARCHAR) AS k, min({cy})::DOUBLE, quantile_cont({cy}, 0.25), median({cy})::DOUBLE, "
                    f"quantile_cont({cy}, 0.75), max({cy})::DOUBLE, count(*) AS n FROM t WHERE {_finite(cy)} "
                    f"GROUP BY k ORDER BY n DESC LIMIT {CHART_CATEGORIES}").fetchall()
                out.update(boxes=[{"x": k, "min": a, "q1": b, "median": c, "q3": d, "max": e, "n": n} for k, a, b, c, d, e, n in got])
                return out
            # A true/false column charts as 1 and 0: sum counts the trues, avg is their share.
            ynum = f"{cy}::INTEGER" if cy is not None and pa.types.is_boolean(table.schema.field(int(y)).type) else cy
            value = "count(*)" if agg == "count" or cy is None else f"{agg}({ynum})::DOUBLE"
            where = f"WHERE {_finite(cy)}" if cy is not None and agg != "count" else ""
            skey = f"CAST({cs} AS VARCHAR)" if cs else "NULL"
            ordered = kind in ("line", "area")
            # The x values to keep: line and area in x order, bar and pie the largest.
            keep_order = f"min({cx})" if ordered else f"{value} DESC"
            cats = _rows(for_arrow(con, f"SELECT {cx}, {value} AS v FROM t {where} GROUP BY {cx} ORDER BY {keep_order} NULLS LAST "
                                     f"LIMIT {CHART_CATEGORIES + 1}"))
            truncated = len(cats) > CHART_CATEGORIES
            cats = cats[:CHART_CATEGORIES]
            if ordered:
                cats.sort(key=lambda r: (r[0] is None, r[0]))
            keys = [r[0] for r in cats]
            names: list[str | None] = [None]
            if cs:
                top = con.execute(f"SELECT {skey} AS s, {value} AS v FROM t {where} GROUP BY s ORDER BY v DESC NULLS LAST "
                                  f"LIMIT {CHART_SERIES + 1}").fetchall()
                names = [r[0] for r in top[:CHART_SERIES]]
                if len(top) > CHART_SERIES:
                    names.append(OTHER)
            index = {_chart_key(k): i for i, k in enumerate(keys)}
            values = {n: [None] * len(keys) for n in names}
            fold = (f"CASE WHEN {skey} IN (SELECT unnest(?)) THEN {skey} ELSE '{OTHER}' END" if cs and OTHER in names else skey)
            params = [[n for n in names if n not in (OTHER, None)]] if cs and OTHER in names else []
            # A join, not IN: it matches NULL keys too, and DuckDB needs pytz for IN over time zone timestamps.
            keep = f"(SELECT {cx} AS k FROM t {where} GROUP BY {cx} ORDER BY {keep_order} NULLS LAST LIMIT {CHART_CATEGORIES}) AS keep"
            for k, sname, v in _rows(for_arrow(con, 
                    f"SELECT t.{cx}, {fold} AS s, {value} FROM t JOIN {keep} ON t.{cx} IS NOT DISTINCT FROM keep.k {where} "
                    f"GROUP BY t.{cx}, s", params)):
                if sname in values and _chart_key(k) in index:
                    values[sname][index[_chart_key(k)]] = _num(v)
            out.update(categories=[_cell(k) for k in keys], series=[{"name": n, "values": values[n]} for n in names],
                       truncated=truncated)
            return out
        finally:
            con.close()

    @staticmethod
    def _profile_column(con: Any, c: str, f: pa.Field, n: int) -> dict[str, Any]:
        t = f.type
        kind = _kind(t)
        floating = pa.types.is_floating(t)
        missing_sql = f"{c} IS NULL" + (f" OR isnan({c})" if floating else "")
        present = f"NOT ({missing_sql})"
        missing, distinct = one_row(con.execute(
            f"SELECT count(*) FILTER (WHERE {missing_sql}), approx_count_distinct({c}) FILTER (WHERE {present}) FROM t"))
        col: dict[str, Any] = {"name": f.name, "type": type_name(t), "kind": kind, "missing": missing,
                               "distinct": distinct or 0}
        if floating:
            infinite = one_row(con.execute(f"SELECT count(*) FROM t WHERE isinf({c})"))[0]
            if infinite:
                col["infinite"] = infinite
                present = f"isfinite({c})"
        if kind == "number":
            if not one_row(con.execute(f"SELECT count(*) FROM t WHERE {present}"))[0]:
                return col
            lo, hi, mean, std, q1, med, q3 = one_row(con.execute(
                f"SELECT min({c}), max({c}), avg({c}::DOUBLE), stddev_samp({c}::DOUBLE), quantile_cont({c}, 0.25), "
                f"quantile_cont({c}, 0.5), quantile_cont({c}, 0.75) FROM t WHERE {present}"))
            col.update(min=_num(lo), max=_num(hi), mean=_num(mean), std=_num(std), q1=_num(q1), median=_num(med), q3=_num(q3))
            span = float(hi) - float(lo)
            bins = [0] * HISTOGRAM_BINS
            for b, count in con.execute(
                    f"SELECT least({HISTOGRAM_BINS - 1}, floor((CAST({c} AS DOUBLE) - ?) / ? * {HISTOGRAM_BINS}))::INT AS b, "
                    f"count(*) FROM t WHERE {present} GROUP BY b", [float(lo), span or 1.0]).fetchall():
                bins[int(b)] = count
            col["histogram"] = bins
        elif kind == "date" and n - missing:
            lo, hi = one_row(con.execute(f"SELECT CAST(min({c}) AS VARCHAR), CAST(max({c}) AS VARCHAR) FROM t WHERE {present}"))
            col.update(min=str(lo), max=str(hi))
        elif n - missing:
            col["top"] = [[r[0], r[1]] for r in _rows(for_arrow(con, 
                f"SELECT CAST({c} AS VARCHAR) AS v, count(*) AS k FROM t WHERE {present} GROUP BY v ORDER BY k DESC, v LIMIT 5"))]
        return col

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
                    pa_csv.write_csv(_csv_ready(table), sink)
                elif fmt == "parquet":
                    import pyarrow.parquet as pq
                    pq.write_table(table.rename_columns(_unique(table.schema.names)), sink)
                else:
                    write_xlsx(table, sink)
            jsonfile.replace(partial, target)  # over the empty file that reserved the name
        except BaseException:
            with_suppressed_unlink(partial)
            raise
        return {"path": str(target), "rows": table.num_rows, "format": fmt}


def _csv_ready(table: pa.Table) -> pa.Table:
    """Arrow's CSV writer refuses lists, structs and maps: those columns go out as JSON text."""
    for i, f in enumerate(table.schema):
        if pa.types.is_nested(f.type):
            text = pa.array([None if v is None else json.dumps(v, default=str) for v in table.column(i).to_pylist()],
                            pa.string())
            table = table.set_column(i, pa.field(f.name, pa.string()), text)
    return table


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
# Excel has no date before 1900-01-01, and counts a 29 February 1900 that never was, so its serials
# before 1 March 1900 are one less than days from _EPOCH. Earlier dates go out as text.
_EXCEL_FIRST = dt.datetime(1900, 1, 1)
_EXCEL_LEAP = dt.datetime(1900, 3, 1)


def _excel_exact(v: float | decimal.Decimal) -> bool:
    """Whether Excel, which keeps 15 significant digits, holds the number as written."""
    if isinstance(v, float):
        return True  # a float shows as Excel rounds it, as any spreadsheet does
    if isinstance(v, int):
        return abs(v) < 10 ** 15
    digits = v.normalize().as_tuple().digits
    return len(digits) <= 15


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
        if not _excel_exact(v):  # Excel keeps 15 digits: an ID like 1234567890123456789 would end in 000
            return _text(ref, str(v))
        return f'<c r="{ref}"><v>{v}</v></c>'
    if isinstance(v, dt.datetime):
        naive = v.replace(tzinfo=None) if v.tzinfo else v
        if naive < _EXCEL_FIRST:
            return _text(ref, str(v))
        serial = (naive - _EPOCH).total_seconds() / 86400 - (naive < _EXCEL_LEAP)
        return f'<c r="{ref}" s="2"><v>{serial}</v></c>'
    if isinstance(v, dt.date):
        day = dt.datetime(v.year, v.month, v.day)
        if day < _EXCEL_FIRST:
            return _text(ref, v.isoformat())
        return f'<c r="{ref}" s="3"><v>{(day - _EPOCH).days - (day < _EXCEL_LEAP)}</v></c>'
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
