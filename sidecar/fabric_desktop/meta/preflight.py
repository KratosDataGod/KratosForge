"""Before reading a Delta source: how much of it a run will read, and whether that fits in memory.

The estimate comes from the Delta log alone (each data file's size, row count and min/max
statistics), so it costs one log read, not a scan. With a watermark, files whose largest
watermark value is not after it are left out, as the scan itself leaves them out. Memory is
estimated from the rows and the column types read: 8 bytes for a number or timestamp, 4 for a
date, 1 for a flag, and ``STRING_BYTES`` for each text value. It is an estimate, shown as "about".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable

STRING_BYTES = 32  # average for a text value in memory: Arrow's offsets plus typical short text
HEADROOM = 3.0  # the read plus the copies later steps make: a 5M-row first load peaked at 2.5x the estimate


@dataclass
class Estimate:
    files: int
    files_total: int
    rows: int
    bytes_on_disk: int
    memory_bytes: int
    exact_rows: bool = True  # False when some files have no row count in the log

    def text(self) -> str:
        rows = f"{self.rows:,}" if self.exact_rows else f"at least {self.rows:,}"
        return (f"about {rows} rows in {self.files} of {self.files_total} file{'s' if self.files_total != 1 else ''} "
                f"({mb(self.bytes_on_disk)} on disk, {mb(self.memory_bytes)} in memory)")

    def part(self, rows: int) -> "Estimate":
        """The same read, limited to ``rows`` of its rows (one batch of a batched load)."""
        share = rows / self.rows if self.rows else 1.0
        return Estimate(files=self.files, files_total=self.files_total, rows=rows,
                        bytes_on_disk=int(self.bytes_on_disk * share), memory_bytes=int(self.memory_bytes * share))

    def to_dict(self) -> dict[str, Any]:
        return {"files": self.files, "files_total": self.files_total, "rows": self.rows, "exact_rows": self.exact_rows,
                "bytes_on_disk": self.bytes_on_disk, "memory_bytes": self.memory_bytes}


def mb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{max(n / 1024 ** 2, 0.1):.1f} MB"


def _width(dtype: pl.DataType) -> int:
    if dtype == pl.Boolean:
        return 1
    if dtype in (pl.String, pl.Binary) or isinstance(dtype, (pl.List, pl.Struct, pl.Categorical)):
        return STRING_BYTES
    if dtype in (pl.Int8, pl.UInt8):
        return 1
    if dtype in (pl.Int16, pl.UInt16):
        return 2
    if dtype in (pl.Int32, pl.UInt32, pl.Float32, pl.Date):
        return 4
    if isinstance(dtype, pl.Decimal) or dtype in (pl.Int128,):
        return 16
    return 8  # 64-bit numbers, timestamps, durations and anything else


def estimate(table: DeltaTable, columns: list[str], schema: dict[str, pl.DataType],
             watermark: tuple[str, Any] | None = None) -> Estimate:
    """What reading ``columns`` of ``table`` (after ``watermark``: column and value) takes."""
    actions = pl.from_arrow(pa.table(table.get_add_actions(flatten=True)))
    assert isinstance(actions, pl.DataFrame)
    total = actions.height
    if watermark and f"max.{watermark[0]}" in actions.columns:
        col = f"max.{watermark[0]}"
        # A file without statistics for the column might hold anything, so it is counted.
        actions = actions.filter(pl.col(col).is_null() | (pl.col(col) > watermark[1]))
    counts = actions["num_records"] if "num_records" in actions.columns else pl.Series([None] * actions.height)
    rows = int(counts.fill_null(0).sum())
    names = [c for c in columns if c in schema]
    fraction = len(names) / max(len(schema), 1)
    width = sum(_width(schema[c]) for c in names)
    return Estimate(files=actions.height, files_total=total, rows=rows,
                    bytes_on_disk=int(int(actions["size_bytes"].sum() or 0) * fraction),
                    memory_bytes=rows * width, exact_rows=counts.null_count() == 0)


def available_memory() -> int | None:
    """Bytes of memory free for a new allocation on this machine, or None when it can't be told."""
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
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return int(status.ullAvailPhys)
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, AttributeError):
        return None
    return None  # macOS and others: no guard


def too_big(est: Estimate, available: int | None) -> str:
    """Why the read shouldn't start (empty when it should): the rows, plus the copies the steps
    after the read make, need more memory than the machine has free."""
    if available is None or est.memory_bytes * HEADROOM <= available:
        return ""
    return (f"this run would read {est.text()}, and with the steps after the read that needs about "
            f"{mb(int(est.memory_bytes * HEADROOM))}, but this machine has {mb(available)} free. "
            f"Load it in smaller pieces (batch_by under [load], an incremental load with a watermark, or fewer columns), run it in Fabric, "
            f"or set memory_guard = false under [load] to try anyway")
