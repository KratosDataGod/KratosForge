"""The Fabric step: a notebook's last code cell, run on Fabric Spark after the others ran here.

Tables written from this PC (delta-rs) carry no V-Order, the Parquet layout Direct Lake reads
fastest; only Fabric Spark writes it. So a notebook can mark its last code cell "Run on Fabric":
the cells before it run on this PC as usual, then that one cell is published as a one-cell Spark
notebook, "<name> (Fabric step)", with the same lakehouse attached, and started as a Fabric job.
The step turns V-Order on for its session, so anything the cell writes is V-Ordered too, and
saves what the cell printed to ``Files/_fabric_desktop/results/<request>.json`` in the
lakehouse, which the app reads back into the cell.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import magics
from .fabric_compute import RESULTS_DIR
from .fabric_run import fabric_sql_magics
from .notebook import leaf_name, to_ipynb
from .onelake import DFS_HOST

STEP_SUFFIX = " (Fabric step)"
# OPTIMIZE leaves a file alone when it has nothing to compact it with (a table of one file, or
# files already large), so "Rewrite all files" reads each table and writes it back instead: a
# write with no data change, V-Ordered like everything the step writes. Tested on Fabric
# 2026-10-23: OPTIMIZE ... VORDER, even with minFileSize at 1 TiB, didn't touch a one-file table.
REWRITE_FUNCTION = "rewrite_with_vorder"
_REWRITE_DEF = f'''def {REWRITE_FUNCTION}(table):
    """Reads the table and writes it back, so every file is rewritten with V-Order."""
    path = spark.sql(f"DESCRIBE DETAIL {{table}}").first()["location"]
    (spark.read.format("delta").load(path).write.format("delta")
     .mode("overwrite").option("dataChange", "false").save(path))'''


# Prove and Side by side run every cell on this PC too, which can't run the step.
STEP_REFUSED = ("is marked Run on Fabric, which runs only on Fabric Spark, so the run on this PC can't run it. "
                "Clear the mark (cell ⋯ menu > Run on this PC, not Fabric) to compare this notebook.")


def is_step(cell: dict[str, Any]) -> bool:
    return bool((cell.get("meta") or {}).get("fabric_step"))


def _code(cells: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    return [(i, c) for i, c in enumerate(cells) if c.get("type") != "markdown" and c.get("source", "").strip()]


def step_index(cells: list[dict[str, Any]]) -> int | None:
    """The marked cell's index, or None when no cell is marked. Raises ValueError when the mark
    isn't on the last code cell, or more than one cell carries it."""
    marked = [i for i, c in enumerate(cells) if is_step(c)]
    if not marked:
        return None
    if len(marked) > 1:
        raise ValueError(f"Only one cell can run on Fabric; {len(marked)} are marked. Clear the mark on all but the last.")
    code = _code(cells)
    if not code or code[-1][0] != marked[0]:
        if not cells[marked[0]].get("source", "").strip():
            raise ValueError("The cell marked Run on Fabric is empty. Write its code, or clear the mark.")
        after = sum(1 for i, _ in code if i > marked[0])
        raise ValueError(f"Only the last code cell can run on Fabric, and {after} code "
                         f"cell{'s are' if after != 1 else ' is'} below the marked one. "
                         "Move the mark to the last cell, or move those cells above it. Nothing was run.")
    if cells[marked[0]].get("meta", {}).get("frozen"):
        raise ValueError("The cell marked Run on Fabric is frozen. Unfreeze it, or clear the mark.")
    return marked[0]


def step_name(name: str) -> str:
    """The companion notebook's name in Fabric, which names it without the notebook's folders."""
    return leaf_name(name) + STEP_SUFFIX


def _quote(part: str) -> str:
    return "`" + part.replace("`", "``") + "`"


def table_name(table: str) -> str:
    """``schema.table`` (or ``table``) as Spark SQL names it, each part quoted."""
    parts = [p for p in str(table).split(".") if p]
    if not 1 <= len(parts) <= 2:
        raise ValueError(f"{table!r}: name tables as table or schema.table in the attached lakehouse")
    return ".".join(_quote(p) for p in parts)


def vorder_source(tables: list[str], rewrite_all: bool = False) -> str:
    """The cell "Insert V-Order cell" writes: OPTIMIZE ... VORDER for each table.

    OPTIMIZE rewrites only the files it compacts; ``rewrite_all`` reads each table and writes it
    back, for a table OPTIMIZE left alone."""
    if not tables:
        raise ValueError("Pick at least one table to V-Order")
    lines = ["# V-Order the tables this notebook wrote, so Direct Lake reads them faster."]
    if rewrite_all:
        lines += ["# OPTIMIZE leaves alone a file it has nothing to compact with, so every file is rewritten.",
                  _REWRITE_DEF, ""]
        lines += [f"{REWRITE_FUNCTION}({json.dumps(table_name(t))})" for t in tables]
    else:
        lines += [f"spark.sql({json.dumps(f'OPTIMIZE {table_name(t)} VORDER')})" for t in tables]
    return "\n".join(lines)


def vorder_candidates(cells: list[dict[str, Any]], tables: list[str]) -> list[str]:
    """The lakehouse tables the notebook's code cells (the step left out) name, in ``tables`` order:
    what "Insert V-Order cell" ticks first. A guess from the code; the person picks."""
    code = "\n".join(c.get("source", "") for c in cells if c.get("type") != "markdown" and not is_step(c))
    out = []
    for t in tables:
        parts = [re.escape(p) for p in t.split(".") if p]
        if not parts:
            continue
        # The full name, or the bare table name as a quoted string (write_table(df, "orders")).
        pattern = r"\b" + r"[`\"']?\s*\.\s*[`\"']?".join(parts) + r"\b"
        if re.search(pattern, code, re.I) or re.search(r"[\"'`]" + parts[-1] + r"[\"'`]", code, re.I):
            out.append(t)
    return out


_NAME = r"((?:`[^`]+`|[\w]+)(?:\s*\.\s*(?:`[^`]+`|[\w]+)){0,2})"
_OPTIMIZE = re.compile(r"\bOPTIMIZE\s+" + _NAME + "|" + r"\b" + REWRITE_FUNCTION + r"\(\s*[\"']" + _NAME, re.I)


def optimized_tables(source: str) -> list[str]:
    """The tables a cell OPTIMIZEs or rewrites, as ``schema.table`` (or ``table``): what the V-Order
    check reads after the step. A three-part name's lakehouse is the attached one, so it's left out."""
    out: list[str] = []
    for m in _OPTIMIZE.finditer(re.sub(r"(?m)^\s*#.*$", "", source)):  # not in comment lines
        found = m.group(1) or m.group(2)
        parts = [p.strip().strip("`").replace("``", "`") for p in re.findall(r"`[^`]+`|[\w]+", found)]
        name = ".".join(parts[-2:])
        if name and name not in out:
            out.append(name)
    return out


def result_path(request_id: str) -> str:
    return f"{RESULTS_DIR}/{request_id}.json"


_STEP = '''# Fabric Desktop step: the last cell of %(name)s, run on Fabric Spark. Published from Fabric
# Desktop on each run; edits here are replaced the next time it runs.
import contextlib as _fd_ctx, io as _fd_io, json as _fd_json, traceback as _fd_tb
spark.conf.set("spark.sql.parquet.vorder.default", "true")  # V-Order what this cell writes
_fd_out, _fd_error = _fd_io.StringIO(), None
try:
    with _fd_ctx.redirect_stdout(_fd_out):
        exec(compile(%(source)s, "cell", "exec"), globals())
except Exception:
    _fd_error = _fd_tb.format_exc()
notebookutils.fs.put(%(result)s, _fd_json.dumps({"request_id": %(request)s, "stdout": _fd_out.getvalue()[-1000000:],
                     "error": _fd_error}), True)
print(_fd_out.getvalue()[-100000:])
if _fd_error:
    raise RuntimeError(_fd_error)
'''


def step_ipynb(name: str, cell: dict[str, Any], lakehouse: dict[str, Any], request_id: str) -> dict[str, Any]:
    """The companion Spark notebook: the cell's code, run with V-Order on, its printed output saved
    to the lakehouse. %%sql and ``%sql`` lines become ``spark.sql`` as when publishing."""
    if magics.cell(cell.get("source", "")).language == "sql" or cell.get("source", "").lstrip().startswith("%"):
        cell = fabric_sql_magics([magics.fabric_cell(cell)], spark=True)[0]
        if magics.cell(cell["source"]).language == "sql":  # a %%sql cell whose rows nothing reads
            cell = {**cell, "source": f"display(spark.sql({json.dumps(magics.cell(cell['source']).body.strip())}))"}
    source = cell.get("source", "")
    try:
        compile(source, "cell", "exec")
    except SyntaxError as exc:
        raise ValueError(f"The Fabric step isn't Python that Fabric Spark can run (line {exc.lineno}: {exc.msg}). "
                         "Magics other than %%sql don't run in the Fabric step.") from exc
    target = (f"abfss://{lakehouse['workspace_id']}@{DFS_HOST}/{lakehouse['lakehouse_id']}/"
              f"{result_path(request_id)}")
    body = _STEP % {"name": json.dumps(name)[1:-1], "source": repr(source), "result": repr(target),
                    "request": repr(request_id)}
    return to_ipynb([{"type": "code", "source": body}], lakehouse)
