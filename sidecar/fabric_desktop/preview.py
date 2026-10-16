"""Previews for the Workspaces page: a table's first rows, a file's start, a notebook's cells.

Pure functions over what the service hands in, so they test without a tenant."""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.json as pa_json
import pyarrow.parquet as pq
from deltalake import DeltaTable

ROWS = 100
# A file bigger than this is not downloaded to preview it.
MAX_FILE_BYTES = 200 * 1024 * 1024
TEXT_BYTES = 20_000
TABLE_FILES = {"csv", "tsv", "parquet", "json", "jsonl"}
TEXT_FILES = {"txt", "md", "py", "sql", "log", "yaml", "yml", "toml", "xml", "ini", "sh", "ps1", "r", "scala"}


def _value(v: Any) -> Any:
    """A cell value JSON can carry: no NaN, dates or decimals."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    return str(v)


def _rows(table: pa.Table, limit: int) -> dict[str, Any]:
    head = table.slice(0, limit)
    columns = [c.to_pylist() for c in head.columns]  # by position: names can repeat
    return {"columns": [{"name": f.name, "type": str(f.type)} for f in table.schema],
            "rows": [[_value(v) for v in row] for row in zip(*columns)]}


def table_preview(dt: DeltaTable, limit: int = ROWS) -> dict[str, Any]:
    """The first rows, Delta's own column types and the row count from the log (no full scan)."""
    dataset = dt.to_pyarrow_dataset()
    head = dataset.head(limit)
    out = _rows(head, limit)
    # Delta's type names (string, long, timestamp), as Fabric shows them.
    out["columns"] = [{"name": f.name, "type": f.type.type if isinstance(f.type.type, str) else str(f.type)}
                      for f in dt.schema().fields]
    out["row_count"] = row_count(dt)
    out["version"] = dt.version()
    return out


def row_count(dt: DeltaTable) -> int | None:
    """Rows in the current version, from the file stats in the log; None if a file has no stats."""
    try:
        actions = dt.get_add_actions(flatten=True)
        actions = pa.table(actions) if not isinstance(actions, pa.Table) else actions
        counts = actions.column("num_records").to_pylist()
    except Exception:  # an older writer without stats, or a log this reader can't flatten
        return None
    return None if any(c is None for c in counts) else int(sum(counts))


def file_kind(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if ext in TABLE_FILES:
        return "table"
    if ext in TEXT_FILES:
        return "text"
    return "other"


def file_preview(path: Path, name: str, limit: int = ROWS) -> dict[str, Any]:
    """Rows for a data file, the start of a text file."""
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "parquet":
        pf = pq.ParquetFile(path)
        batch = next(pf.iter_batches(batch_size=limit), None)
        table = pa.Table.from_batches([batch]) if batch is not None else pf.schema_arrow.empty_table()
        return {"kind": "table", **_rows(table, limit), "row_count": pf.metadata.num_rows}
    if ext in ("csv", "tsv"):
        opts = pa_csv.ParseOptions(delimiter="\t" if ext == "tsv" else ",")
        reader = pa_csv.open_csv(path, parse_options=opts)
        batches, n = [], 0
        for batch in reader:
            batches.append(batch)
            n += batch.num_rows
            if n >= limit:
                break
        table = pa.Table.from_batches(batches, schema=reader.schema)
        return {"kind": "table", **_rows(table, limit), "row_count": None}
    if ext in ("json", "jsonl"):
        try:
            table = pa_json.read_json(path)  # newline-delimited
        except pa.ArrowInvalid:
            data = json.loads(path.read_text(encoding="utf-8"))
            data = data if isinstance(data, list) else [data]
            table = pa.Table.from_pylist([r if isinstance(r, dict) else {"value": r} for r in data[:limit]])
        return {"kind": "table", **_rows(table, limit), "row_count": table.num_rows}
    with path.open("rb") as f:
        raw = f.read(TEXT_BYTES + 1)
    return {"kind": "text", "text": raw[:TEXT_BYTES].decode("utf-8", errors="replace"), "truncated": len(raw) > TEXT_BYTES}


def notebook_cells(definition: dict[str, Any]) -> dict[str, Any]:
    """Cells from a notebook item's ipynb definition (Fabric's getDefinition result)."""
    parts = (definition.get("definition") or definition).get("parts") or []
    part = next((p for p in parts if p.get("path", "").endswith(".ipynb")), None)
    if part is None:
        raise ValueError("Fabric didn't return the notebook's cells")
    nb = json.loads(base64.b64decode(part["payload"]))
    meta = nb.get("metadata") or {}
    language = (meta.get("kernel_info") or {}).get("name") or (meta.get("language_info") or {}).get("name") or ""
    cells = []
    for c in nb.get("cells", []):
        src = c.get("source", "")
        cells.append({"type": c.get("cell_type", "code"), "source": "".join(src) if isinstance(src, list) else str(src)})
    return {"cells": cells, "language": language}
