"""Read a Delta table's rows right when it uses deletion vectors or column mapping.

delta-rs 1.6's pyarrow reader (``to_pyarrow_dataset`` and ``to_pyarrow_table``) refuses a table with
deletion vectors. On a table with column mapping that another writer made, such as Fabric Spark,
the data files hold physical column names (``col-1ebb906d-...``), and that reader gives every
value as NULL with no error. Fabric turns column mapping on for a column name with a space, so
this happens to ordinary lakehouse tables.

delta-rs's DataFusion reader (``QueryBuilder``) applies both features, so those tables are read
through it here. A table that uses neither is read exactly as before.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

# A table read through DataFusion is read whole into memory (see dataset()). Past this many bytes of
# data files, the read is refused with the reason instead of taking the PC's memory.
MAX_WHOLE_READ_BYTES = 2 * 1024**3

# The table settings and protocol features that the pyarrow reader gets wrong or refuses.
_DV_FEATURE = "deletionVectors"
_CM_FEATURE = "columnMapping"
_CM_MODE = "delta.columnMapping.mode"


def column_mapping(table: Any) -> str:
    """The table's column mapping mode: "name", "id", or "" when it has none."""
    config = table.metadata().configuration or {}
    mode = str(config.get(_CM_MODE, "") or "").lower()
    return "" if mode in ("", "none") else mode


def deletion_vectors_on(table: Any) -> bool:
    """Whether the table may hold deletion vectors: the protocol lists the feature."""
    protocol = table.protocol()
    return _DV_FEATURE in set(protocol.reader_features or []) | set(protocol.writer_features or [])


def needs_kernel(table: Any) -> list[str]:
    """The features that make the pyarrow reader unsafe for this table; empty when it is safe."""
    reasons = []
    if deletion_vectors_on(table):
        reasons.append("deletion vectors")
    protocol = table.protocol()
    features = set(protocol.reader_features or []) | set(protocol.writer_features or [])
    if column_mapping(table) or _CM_FEATURE in features:
        reasons.append("column mapping")
    return reasons


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def arrow_schema(table: Any) -> pa.Schema:
    """The table's logical schema as pyarrow, with each field's Delta metadata (column mapping's
    physical name and ID)."""
    raw = table.schema()
    return pa.schema(raw.to_arrow()) if hasattr(raw, "to_arrow") else raw.to_pyarrow()


def _query(table: Any, columns: list[str] | None, tail: str = "") -> pa.Table:
    from deltalake import QueryBuilder

    schema = arrow_schema(table)
    names = list(columns) if columns is not None else schema.names
    select = ", ".join(_ident(n) for n in names) if names else "*"
    reader = QueryBuilder().register("kf_table", table).execute(f"SELECT {select} FROM kf_table{tail}")
    got = pa.table(reader)
    # DataFusion gives strings as string_view; hand back the types the pyarrow reader gives.
    # The Delta field metadata (physical names) is left off, so it can't follow the rows into a write.
    want = pa.schema([pa.field(n, schema.field(n).type, schema.field(n).nullable) for n in names]) \
        if names else got.schema
    return got.cast(want) if got.schema != want else got


def data_bytes(table: Any) -> int:
    """The size of the loaded version's data files, from the log (nothing is read)."""
    sizes = pa.table(table.get_add_actions(flatten=True)).column("size_bytes")
    return int(sum(s for s in sizes.to_pylist() if s))


def _whole(table: Any, columns: list[str] | None) -> pa.Table:
    reasons = needs_kernel(table)
    size = data_bytes(table)
    if size > MAX_WHOLE_READ_BYTES:
        raise ValueError(f"This table uses {' and '.join(reasons)}, so it is read whole into this PC's memory, and its "
                         f"files hold {size / 1024**3:.1f} GB, more than {MAX_WHOLE_READ_BYTES / 1024**3:.0f} GB. "
                         "Read part of it in Fabric, or in a local data project, which reads it in place.")
    return _query(table, columns)


def arrow(table: Any, columns: list[str] | None = None) -> pa.Table:
    """Every row of the table as loaded (its version), as an Arrow table."""
    if not needs_kernel(table):
        return table.to_pyarrow_table(columns=columns)
    return _whole(table, columns)


def head(table: Any, limit: int, columns: list[str] | None = None) -> pa.Table:
    """The first ``limit`` rows, without reading the rest where the reader can stop early."""
    if not needs_kernel(table):
        return table.to_pyarrow_dataset().head(limit, columns=columns)
    return _query(table, columns, f" LIMIT {int(limit)}")


def dataset(table: Any) -> Any:
    """The table as a pyarrow dataset. For a table that needs DataFusion's reader, its rows are read
    into memory first: a dataset over its files would show deleted rows or physical column names.
    So a query of such a table (even with LIMIT) reads all of it, up to MAX_WHOLE_READ_BYTES of files."""
    if not needs_kernel(table):
        return table.to_pyarrow_dataset()
    import pyarrow.dataset as ds  # here, not at the top: it costs about 0.2 s of engine start

    return ds.dataset(_whole(table, None))


def count(table: Any) -> int:
    """The number of rows, with deleted rows left out."""
    if not needs_kernel(table):
        return int(table.to_pyarrow_dataset().count_rows())
    from deltalake import QueryBuilder

    reader = QueryBuilder().register("kf_table", table).execute("SELECT count(*) AS n FROM kf_table")
    return int(pa.table(reader).column("n")[0].as_py())


def deleted_rows(table: Any) -> int:
    """Rows the table's deletion vectors remove, from the vectors themselves (0 when it has none)."""
    if not deletion_vectors_on(table):
        return 0
    vectors = pa.table(table.deletion_vectors())
    return sum(keep.count(False) for keep in vectors.column("selection_vector").to_pylist())


def deleted_positions(table: Any) -> dict[str, list[tuple[int, int]]]:
    """For each data file with a deletion vector, its deleted rows as (first, last) row-number ranges.

    Keyed by the file's URI as delta-rs gives it. A vector can be shorter than its file: rows past
    its end are kept."""
    if not deletion_vectors_on(table):
        return {}
    out: dict[str, list[tuple[int, int]]] = {}
    vectors = pa.table(table.deletion_vectors())
    for path, keep in zip(vectors.column("filepath").to_pylist(), vectors.column("selection_vector").to_pylist(),
                          strict=True):
        ranges: list[tuple[int, int]] = []
        start = None
        for i, kept in enumerate(keep):
            if not kept and start is None:
                start = i
            elif kept and start is not None:
                ranges.append((start, i - 1))
                start = None
        if start is not None:
            ranges.append((start, len(keep) - 1))
        if ranges:
            out[path] = ranges
    return out
