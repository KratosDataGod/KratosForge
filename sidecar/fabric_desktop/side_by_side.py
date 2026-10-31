"""Side by side: run one notebook on this PC and in Fabric, then compare the time and the results.

The two runs go one after the other against the same lakehouse, so they never write a table at
the same moment. Each run leaves its own Delta versions behind, so the tables can be compared
afterwards exactly as each run wrote them:

* before: the version of every table in the lakehouse before anything runs
* after the PC run: tables whose version moved were written on this PC
* after the Fabric run: tables whose version moved again were written in Fabric

A table is compared by row count, column names and types, and a checksum of every row that
doesn't depend on row order. Numbers with a fraction are rounded to 10 significant digits
first, so two engines that add up the same doubles in a different order still match. When a
run only appended to a table, the rows that run added are compared instead of the whole table.
"""

from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass
from typing import Any, Callable

import pyarrow as pa

from .redact import describe
from .duckdb_rows import one_row

_log = logging.getLogger(__name__)

TIMING_DIR = "Files/_fabric_desktop/side_by_side"
_MOD = 1 << 64

# ---- the checksum


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _normalized(name: str, typ: pa.DataType, as_number: bool = False) -> str:
    """A SQL expression for one column that two engines produce the same way for the same value.
    ``as_number`` normalizes an integer column as a number, to match a decimal column on the other side."""
    c = _quote(name)
    if pa.types.is_integer(typ) and not as_number:
        return f"CAST({c} AS HUGEINT)"
    if pa.types.is_floating(typ) or pa.types.is_decimal(typ) or pa.types.is_integer(typ):
        x = f"CAST({c} AS DOUBLE)"
        return (f"CASE WHEN {x} IS NULL OR {x} = 0 OR NOT isfinite({x}) THEN {x} "
                f"ELSE round({x}, 9 - CAST(floor(log10(abs({x}))) AS INTEGER)) END")
    if pa.types.is_timestamp(typ):
        return f"epoch_us(CAST({c} AS TIMESTAMP))"
    if pa.types.is_string(typ) or pa.types.is_large_string(typ):
        return c
    return f"CAST({c} AS VARCHAR)"


def kind(typ: pa.DataType) -> str:
    """The type as a person compares it: integer, number, string, date, timestamp, boolean..."""
    if pa.types.is_integer(typ):
        return "integer"
    if pa.types.is_floating(typ) or pa.types.is_decimal(typ):
        return "number"
    if pa.types.is_string(typ) or pa.types.is_large_string(typ):
        return "string"
    if pa.types.is_timestamp(typ):
        return "timestamp"
    if pa.types.is_date(typ):
        return "date"
    if pa.types.is_boolean(typ):
        return "boolean"
    return str(typ)


def fingerprint(data: Any, numbers: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """Rows, columns and an order-independent checksum of a pyarrow table or dataset. Integer columns
    named in ``numbers`` (lower case) are summed as numbers, as a decimal column of the same values would be."""
    import duckdb
    schema: pa.Schema = data.schema
    columns = [[f.name, kind(f.type)] for f in schema]
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.register("t", data)
        fields = sorted(schema, key=lambda f: f.name.lower())
        if fields:
            row_hash = f"hash({', '.join(_normalized(f.name, f.type, f.name.lower() in numbers) for f in fields)})"
            rows, total = one_row(con.execute(f"SELECT count(*), coalesce(sum(CAST({row_hash} AS HUGEINT)), 0) FROM t"))
        else:
            rows, total = one_row(con.execute("SELECT count(*), 0 FROM t"))
    finally:
        con.close()
    return {"rows": int(rows), "checksum": int(total) % _MOD, "columns": columns}


def _hex(n: int | None) -> str | None:
    return None if n is None else f"{n:016x}"


# ---- what each run wrote


@dataclass
class TableSide:
    """One run's result for one table."""
    version: int
    start_version: int | None  # the version before this run, if the table existed
    mode: str  # "overwrite" (the whole table is the run's), "append" (only added rows) or "in place"


def write_mode(commits: list[dict[str, Any]]) -> str:
    """How a run's commits changed a table: replaced it, only appended to it, or changed rows in place."""
    ops = [(str(c.get("operation") or "").upper(), str((c.get("operationParameters") or {}).get("mode") or "").lower())
           for c in commits]
    if any(op == "WRITE" and mode in ("overwrite", "errorifexists", "ignore") or "CREATE" in op or "REPLACE" in op
           for op, mode in ops):
        return "overwrite"
    if ops and all(op in ("WRITE", "STREAMING UPDATE") and mode in ("append", "") for op, mode in ops):
        return "append"
    return "in place"


def changes(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """Tables created, or given a new version, between two snapshots."""
    return sorted(t for t, v in after.items() if before.get(t, -1) < v)


def side(table: str, start: dict[str, int], end: dict[str, int],
         history: Callable[[str], list[dict[str, Any]]]) -> TableSide | None:
    if table not in changes(start, end):
        return None
    prior = start.get(table)
    if prior is None:
        return TableSide(end[table], None, "overwrite")
    commits = [c for c in history(table) if prior < int(c.get("version", -1)) <= end[table]]
    return TableSide(end[table], prior, write_mode(commits))


class Tables:
    """A lakehouse's tables as the comparison reads them. Tests use local folders."""

    def versions(self) -> dict[str, int]:
        raise NotImplementedError

    def history(self, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def dataset(self, table: str, version: int) -> Any:
        raise NotImplementedError

    def fingerprint(self, table: str, version: int) -> dict[str, Any]:
        return fingerprint(self.dataset(table, version))


def _result_of(tables: Tables, table: str, s: TableSide) -> dict[str, Any]:
    full = tables.fingerprint(table, s.version)
    out = {"version": s.version, "mode": s.mode, "columns": full["columns"]}
    if s.mode == "append" and s.start_version is not None:
        base = tables.fingerprint(table, s.start_version)
        return out | {"rows": full["rows"] - base["rows"], "checksum": (full["checksum"] - base["checksum"]) % _MOD,
                      "table_rows": full["rows"]}
    return out | {"rows": full["rows"], "checksum": full["checksum"], "table_rows": full["rows"]}


def _columns_text(a: list[list[str]], b: list[list[str]]) -> str:
    left = {n.lower(): t for n, t in a}
    right = {n.lower(): t for n, t in b}
    if left == right:
        return "same"
    parts = []
    if only := sorted(set(left) - set(right)):
        parts.append("only on this PC: " + ", ".join(only))
    if only := sorted(set(right) - set(left)):
        parts.append("only in Fabric: " + ", ".join(only))
    if typed := sorted(n for n in set(left) & set(right) if left[n] != right[n]):
        parts.append("types differ: " + ", ".join(f"{n} ({left[n]} vs {right[n]})" for n in typed))
    return "; ".join(parts)


def compare(tables: Tables, before: dict[str, int], after_local: dict[str, int],
            after_fabric: dict[str, int]) -> list[dict[str, Any]]:
    """One row per table either run wrote: what each side wrote and whether they match."""
    rows = []
    for table in sorted(set(changes(before, after_local)) | set(changes(after_local, after_fabric))):
        row: dict[str, Any] = {"table": table}
        try:
            here = side(table, before, after_local, tables.history)
            there = side(table, after_local, after_fabric, tables.history)
            row["local"] = _result_of(tables, table, here) if here else None
            row["fabric"] = _result_of(tables, table, there) if there else None
        except Exception as exc:  # noqa: BLE001 - one unreadable table shouldn't hide the rest
            rows.append(row | {"result": "error", "note": f"Couldn't read it: {exc}"})
            continue
        a, b = row["local"], row["fabric"]
        if a is None:
            row |= {"result": "only_fabric", "note": "Written in Fabric only. The run on this PC didn't write it."}
        elif b is None:
            row |= {"result": "only_local", "note": "Written on this PC only. The run in Fabric didn't write it."}
        else:
            row["columns"] = _columns_text(a["columns"], b["columns"])
            same_names = {n.lower() for n, _ in a["columns"]} == {n.lower() for n, _ in b["columns"]}
            row["result"] = "match" if same_names and a["rows"] == b["rows"] and a["checksum"] == b["checksum"] else "differs"
            if a["mode"] == "append" or b["mode"] == "append":
                row["note"] = "Appended: compared the rows each run added."
            elif "in place" in (a["mode"], b["mode"]):
                row["note"] = "Changed in place (MERGE, UPDATE or DELETE): compared the whole table after each run."
        for k in ("local", "fabric"):
            if row.get(k):
                row[k]["checksum"] = _hex(row[k]["checksum"])
        rows.append(row)
    return rows


def verdict(rows: list[dict[str, Any]], local_status: str, fabric_status: str,
            local_cell: int | None = None) -> dict[str, Any]:
    """The one line at the top of the comparison."""
    failed = [w for w, s in (("this PC", local_status), ("Fabric", fabric_status)) if s != "ok"]
    if len(failed) == 2 and local_cell:
        # The same code failing in both places is usually the notebook or its data, not either side:
        # this PC's error is the one to read first, it names the line.
        return {"same": False, "text": f"The run failed on this PC (cell {local_cell}) and in Fabric. "
                                       "Fix this PC's error first: Fabric usually fails the same way."}
    if failed:
        return {"same": False, "text": f"The run failed on {' and '.join(failed)}, so the results can't be the same."}
    if not rows:
        return {"same": None, "text": "Neither run wrote a table, so only the time is compared."}
    bad = [r for r in rows if r["result"] != "match"]
    if not bad:
        return {"same": True, "text": f"Same results in both places ({len(rows)} table{'s' if len(rows) != 1 else ''})."}
    return {"same": False, "text": f"Results differ in {len(bad)} of {len(rows)} table{'s' if len(rows) != 1 else ''}."}


def speed(local_seconds: float | None, fabric_total: float | None, fabric_run: float | None,
          local_processing: float | None = None) -> dict[str, str]:
    """Which side was faster: end to end, on the notebook's own run time (Fabric's session start left
    out) and, when this PC waited on OneLake uploads, on this PC's processing alone."""
    def faster(here: float | None, there: float | None, what: str) -> str:
        if not here or not there:
            return ""
        if here <= there:
            return f"This PC was {there / here:.1f}x faster {what}"
        return f"Fabric was {here / there:.1f}x faster {what}"
    return {"end_to_end": faster(local_seconds, fabric_total, "end to end"),
            "run": faster(local_seconds, fabric_run, "on run time alone, leaving out Fabric's session start"),
            "processing": faster(local_processing, fabric_run, "on processing alone, leaving out this PC's wait for "
                                 "OneLake uploads (Fabric's run time includes its own writes)")
            if local_processing is not None and local_processing < (local_seconds or 0) else ""}


# ---- timing inside the Fabric run

def timing_cells(request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cells added before and after the notebook when it is published for a side-by-side run.

    They time the notebook's own cells inside Fabric, so the comparison can tell the session's
    start-up apart from the run. They aren't added to the notebook on this PC."""
    first = {"type": "code", "source": "# Fabric Desktop side by side: start the clock\n"
                                       "import time as _fd_time\n_fd_started = _fd_time.time()"}
    last = {"type": "code", "source": (
        "# Fabric Desktop side by side: how long the notebook's cells took in Fabric\n"
        "import json as _fd_json, os as _fd_os\n"
        f"_fd_dir = '/lakehouse/default/{TIMING_DIR}'\n"
        "try:\n"
        "    _fd_os.makedirs(_fd_dir, exist_ok=True)\n"
        f"    with open(_fd_dir + '/{request_id}.json', 'w') as _fd_f:\n"
        "        _fd_json.dump({'seconds': _fd_time.time() - _fd_started}, _fd_f)\n"
        "except Exception as _fd_e:\n"
        "    print('Side by side timing not saved:', _fd_e)\n")}
    return first, last


def parse_timing(data: bytes | None) -> float | None:
    try:
        return float(json.loads(data or b"null")["seconds"])
    except (TypeError, ValueError, KeyError):
        return None


# ---- this PC


def machine(cores_used: int | None = None) -> dict[str, Any]:
    """What this PC brings to the comparison: cores (and how many the run may use, when capped)
    and memory."""
    return {"cores": os.cpu_count(), "cores_used": cores_used, "memory_gb": _memory_gb(), "os": platform.system()}


# ---- cores: side by side's "Match Fabric" (cpu_cap.py holds the PC's run to them)

PYTHON_VCORES = 2  # a Fabric Python notebook's session without %%configure
CORE_CHOICES = ("all", "match")  # or a number of cores
DEFAULT_CORES = "match"


def fabric_vcores(cells: list[dict[str, Any]], engine: str) -> int | None:
    """The vCores the notebook's Fabric session gets: a Python notebook's 2, or what its
    ``%%configure`` cell asks for. None for a Spark notebook, whose pool decides."""
    if engine != "python":
        return None
    for cell in cells:
        source = (cell.get("source") or "").lstrip()
        if not source.startswith("%%configure"):
            continue
        try:
            asked = json.loads(source.split("\n", 1)[1] if "\n" in source else "{}")
            vcores = int(asked.get("vCores"))
        except (ValueError, TypeError, AttributeError):
            continue
        if vcores > 0:
            return vcores
    return PYTHON_VCORES


def cores_for(choice: Any, vcores: int | None, pc_cores: int | None) -> int | None:
    """How many cores the run on this PC may use for ``choice``; None for all of them. "match"
    with no known vCores (a Spark notebook) uses all, and the card says so."""
    pc = pc_cores or 1
    if choice == "match":
        wanted = vcores
    elif isinstance(choice, int) and not isinstance(choice, bool):
        wanted = choice
    else:
        wanted = None
    return None if not wanted or wanted >= pc else max(1, wanted)


def cores_line(machine: dict[str, Any], vcores: int | None) -> str:
    """One line for the result: whether both sides had the same cores."""
    used = machine.get("cores_used") or machine.get("cores")
    if not used or not vcores:
        return ""
    if used == vcores:
        return f"Same cores on both sides: {used} on this PC, {vcores} vCores in Fabric"
    return f"This PC used {used} cores and Fabric's session had {vcores} vCores, so this compares hardware as well as engines"


def _memory_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            total = status.ullTotalPhys
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return round(total / 1024 ** 3, 1)
    except Exception as exc:  # noqa: BLE001 - only shown as context
        _log.info("Couldn't read this PC's memory: %s", describe(exc))
        return None
