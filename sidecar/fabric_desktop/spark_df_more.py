"""The last DataFrame methods this PC's Spark lacked (Fabric notebook parity F3): ``freqItems``
(also ``df.stat.freqItems``), ``inputFiles``, ``to``, ``withMetadata`` and ``writeTo`` (Spark's
DataFrameWriterV2: create, replace, createOrReplace, append).

``writeTo`` writes through the same lakehouse table writer as ``saveAsTable`` (write lease, schema
rules, audit), so it can do nothing ``saveAsTable`` can't. ``freqItems`` is exact here, where Spark's
is approximate and may list extra items: the items listed are those at or above ``support``.
"""

from __future__ import annotations

import re
from typing import Any


def _df_class() -> Any:
    from duckdb.experimental.spark.sql.dataframe import DataFrame
    return DataFrame


def freq_items(self: Any, cols: Any, support: float | None = None) -> Any:
    """One row: for each column, ``<col>_freqItems``, the values in at least ``support`` (default 1%) of rows."""
    if isinstance(cols, str):
        cols = [cols]
    if not isinstance(cols, (list, tuple)) or not cols:
        raise TypeError("freqItems takes a list of column names.")
    support = 0.01 if support is None else float(support)
    if not 1e-4 <= support <= 1:
        raise ValueError(f"support must be between 1e-4 and 1, not {support}.")
    names = {c.lower(): c for c in self.columns}
    rel = self.relation
    selects = []
    for c in cols:
        real = names.get(str(c).lower())
        if real is None:
            raise ValueError(f"[UNRESOLVED_COLUMN] There is no column {c} (columns: {', '.join(self.columns)}).")
        q = '"' + real.replace('"', '""') + '"'
        selects.append(
            f"(SELECT coalesce(list({q} ORDER BY n DESC, {q}), []) FROM "
            f"(SELECT {q}, count(*) AS n FROM rel GROUP BY {q}) WHERE n >= {support} * (SELECT count(*) FROM rel)) "
            f"AS \"{str(c).replace(chr(34), chr(34) * 2)}_freqItems\"")
    view = f"fd_freq_{id(rel)}"
    rel.create_view(view, replace=True)
    try:
        out = self.session.conn.sql(f"WITH rel AS (SELECT * FROM {view}) SELECT " + ", ".join(selects))
        return _df_class()(self.session.conn.from_arrow(out.to_arrow_table()), self.session)
    finally:
        self.session.conn.execute(f"DROP VIEW IF EXISTS {view}")


_SCAN = re.compile(r"\b(?:parquet_scan|read_parquet|read_csv(?:_auto)?|read_json(?:_auto)?|delta_scan|read_text)\s*\(\s*(\[[^\]]*\]|'(?:[^']|'')*')",
                   re.I)
_QUOTED = re.compile(r"'((?:[^']|'')*)'")


def input_files(self: Any) -> list[str]:
    """The files this DataFrame reads, best effort as in Spark: the paths of the scans in its plan. A
    folder or glob is listed as its files; a DataFrame made from Python data reads none."""
    import glob
    import os
    try:
        sql = self.relation.sql_query()
    except Exception:  # noqa: BLE001  # silent-ok: a relation DuckDB can't write as SQL reads no files we can name
        return []
    out: list[str] = []
    for m in _SCAN.finditer(sql):
        for raw in _QUOTED.findall(m.group(1)):
            path = raw.replace("''", "'")
            if os.path.isdir(path):
                if os.path.isdir(os.path.join(path, "_delta_log")):
                    try:
                        from deltalake import DeltaTable
                        out += [os.path.join(path, f) if "://" not in f else f for f in DeltaTable(path).file_uris()]
                        continue
                    except Exception:  # noqa: BLE001  # silent-ok: an unreadable Delta log lists its data files instead
                        pass
                out += sorted(p for p in glob.glob(os.path.join(path, "**", "*"), recursive=True)
                              if os.path.isfile(p) and "_delta_log" not in p)
            elif any(ch in path for ch in "*?["):
                out += sorted(glob.glob(path, recursive=True))
            else:
                out.append(path)
    return list(dict.fromkeys(out))  # each file once, in the order found


def to(self: Any, schema: Any) -> Any:
    """The columns of ``schema``, in its order and types, matched by name (case-insensitive as in Spark);
    a column the schema has and the DataFrame doesn't is an error, extra columns are left out."""
    from duckdb.experimental.spark.sql import functions as F
    from duckdb.experimental.spark.sql import types as T
    if isinstance(schema, str):
        from .spark_local import ddl_schema
        schema = ddl_schema(schema)
    if not isinstance(schema, T.StructType):
        raise TypeError("to() takes a StructType or a DDL string.")
    names = {c.lower(): c for c in self.columns}
    cols = []
    for f in schema.fields:
        real = names.get(f.name.lower())
        if real is None:
            raise ValueError(f"[UNRESOLVED_COLUMN] The schema has a column {f.name} the DataFrame doesn't "
                             f"(columns: {', '.join(self.columns)}).")
        cols.append(F.col(real).cast(f.dataType).alias(f.name))
    return self.select(*cols)


def with_metadata(self: Any, columnName: str, metadata: dict[str, Any]) -> Any:  # noqa: N803
    """Spark keeps metadata on a column for its catalog; here it is kept on the DataFrame and shown in
    ``df.schema[col].metadata``. The data is unchanged."""
    if not isinstance(metadata, dict):
        raise TypeError("metadata is a dict.")
    real = next((c for c in self.columns if c == columnName), None)
    if real is None:
        raise ValueError(f"[UNRESOLVED_COLUMN] There is no column {columnName} (columns: {', '.join(self.columns)}).")
    out = _df_class()(self.relation, self.session)
    kept = dict(getattr(self, "_fd_metadata", {}))
    kept[real] = dict(metadata)
    out._fd_metadata = kept
    return out


def _schema_with_metadata(prop: Any) -> Any:
    def schema(self: Any) -> Any:
        s = prop.fget(self)
        kept = getattr(self, "_fd_metadata", None)
        if kept:
            for f in s.fields:
                if f.name in kept:
                    f.metadata = dict(kept[f.name])
        return s
    return property(schema)


class DataFrameWriterV2:
    """``df.writeTo("schema.table")``: Spark's DataFrameWriterV2 over the lakehouse table writer."""

    def __init__(self, df: Any, table: str) -> None:
        if not isinstance(table, str) or not table.strip():
            raise ValueError("writeTo takes a table name such as \"sales\" or \"dbo.sales\".")
        self._df = df
        self._table = table.strip()
        self._options: dict[str, Any] = {}

    def using(self, provider: str) -> DataFrameWriterV2:
        if str(provider).lower() != "delta":
            raise ValueError("Lakehouse tables are Delta tables. Leave out using(), or use using(\"delta\").")
        return self

    def option(self, key: str, value: Any) -> DataFrameWriterV2:
        self._options[str(key)] = value
        return self

    def options(self, **opts: Any) -> DataFrameWriterV2:
        self._options.update(opts)
        return self

    def tableProperty(self, prop: str, value: Any) -> DataFrameWriterV2:  # noqa: N802, ARG002
        print(f"Note: the table property {prop} is set in Fabric; on this PC it is ignored.")
        return self

    def partitionedBy(self, *_cols: Any) -> DataFrameWriterV2:  # noqa: N802
        print("Note: partitionedBy is ignored on this PC; the table is written without partitions.")
        return self

    def clusterBy(self, *_cols: Any) -> DataFrameWriterV2:  # noqa: N802
        print("Note: clusterBy only changes file layout and is ignored on this PC.")
        return self

    def _writer(self, mode: str, **extra: Any) -> Any:
        w = self._df.write.format("delta").mode(mode)
        for k, v in {**self._options, **extra}.items():
            w = w.option(k, v)
        return w

    def _exists(self) -> bool:
        from . import lakehouse
        return bool(lakehouse.table_exists(self._table))

    def create(self) -> None:
        """A new table; an error when it already exists."""
        if self._exists():
            raise ValueError(f"[TABLE_OR_VIEW_ALREADY_EXISTS] Table {self._table} already exists. "
                             "Use createOrReplace() or append().")
        self._writer("errorifexists").saveAsTable(self._table)

    def replace(self) -> None:
        """Replace an existing table's data and schema; an error when there is none."""
        if not self._exists():
            raise ValueError(f"[TABLE_OR_VIEW_NOT_FOUND] Table {self._table} doesn't exist. Use create() or createOrReplace().")
        self._writer("overwrite", overwriteSchema="true").saveAsTable(self._table)

    def createOrReplace(self) -> None:  # noqa: N802
        self._writer("overwrite", overwriteSchema="true").saveAsTable(self._table)

    def append(self) -> None:
        """Add rows to an existing table, columns matched by name, as in Spark."""
        if not self._exists():
            raise ValueError(f"[TABLE_OR_VIEW_NOT_FOUND] Table {self._table} doesn't exist. Use create() first.")
        self._writer("append").saveAsTable(self._table)

    def overwrite(self, condition: Any) -> None:  # noqa: ARG002
        raise NotImplementedError("overwrite(condition) replaces only the rows that match, which runs in Fabric. "
                                  "On this PC, use createOrReplace(), or a MERGE.")

    def overwritePartitions(self) -> None:  # noqa: N802
        raise NotImplementedError("overwritePartitions() replaces partitions dynamically, which runs in Fabric. "
                                  "On this PC, use createOrReplace(), or a MERGE.")


def write_to(self: Any, table: str) -> DataFrameWriterV2:
    return DataFrameWriterV2(self, table)


def install() -> None:
    from . import spark_api
    DataFrame = _df_class()  # noqa: N806
    DataFrame.freqItems = freq_items
    spark_api._Stat.freqItems = lambda self, cols, support=None: freq_items(self._df, cols, support)  # type: ignore[attr-defined]  # added here
    DataFrame.inputFiles = input_files
    DataFrame.to = to
    DataFrame.withMetadata = with_metadata
    DataFrame.writeTo = write_to
    if not getattr(DataFrame.schema.fget, "_fd_metadata", False):
        prop = _schema_with_metadata(DataFrame.schema)
        prop.fget._fd_metadata = True
        DataFrame.schema = prop
