"""A full disk on Windows, on a real volume rather than an injected error (local-performance/disk-full).

test_disk_full makes delta-rs's and DuckDB's full-disk errors without filling a disk, and on Linux they
were measured on a full tmpfs. Windows words a full disk as os error 112, which disk_full matches, but
that was never seen on a real Windows volume. These tests attach a small virtual disk (diskpart, which
needs an administrator, as CI's Windows runners are), mount it on a folder, fill it to a few MB free,
and then let a metadata run's table write and a SQL page query's temp files run out of room on it.
"""

from __future__ import annotations

import ctypes
import errno
import shutil
import subprocess
import sys
import uuid
import warnings

import duckdb
import polars as pl
import pytest
from test_large_tables import DAY1, DAY2, Setup, rows
from test_write_failures import ids

from fabric_desktop import disk_full, sql_worker

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="a Windows volume; Linux was measured on a tmpfs"),
    pytest.mark.skipif(sys.platform == "win32" and not ctypes.windll.shell32.IsUserAnAdmin(),  # type: ignore[attr-defined]
                       reason="attaching a virtual disk needs an administrator (CI's Windows runners are)"),
]

FREE_MB = 3  # what is left free before the write, as in the Linux measurement


def diskpart(script: str) -> str:
    r = subprocess.run(["diskpart"], input=script, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    return r.stdout


@pytest.fixture(scope="module")
def volume(tmp_path_factory):
    """A 96 MB NTFS volume of its own, mounted on an empty folder; detached and deleted at the end."""
    base = tmp_path_factory.mktemp("diskfull")
    vhd, mount = base / f"kf-{uuid.uuid4().hex[:8]}.vhdx", base / "mnt"
    mount.mkdir()
    diskpart(f'create vdisk file="{vhd}" maximum=96 type=expandable\nselect vdisk file="{vhd}"\nattach vdisk\n'
             f'create partition primary\nformat fs=ntfs quick label=kfdiskfull\nassign mount="{mount}"\n')
    try:
        yield mount
    finally:
        diskpart(f'select vdisk file="{vhd}"\ndetach vdisk\n')
        vhd.unlink(missing_ok=True)


@pytest.fixture
def room(volume):
    """A folder on the small volume, emptied afterwards, and a way to fill the volume to FREE_MB free."""
    folder = volume / uuid.uuid4().hex[:8]
    folder.mkdir()

    def fill() -> None:
        left = shutil.disk_usage(volume).free - FREE_MB * 2**20
        with open(folder / "filler.bin", "wb") as f:
            f.truncate(max(left, 0))  # NTFS allocates it: the space is gone, nothing is written
        free = shutil.disk_usage(volume).free / 2**20
        assert free < FREE_MB + 1, f"{free:.1f} MB free after filling"

    try:
        yield folder, fill
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        assert shutil.disk_usage(volume).free > 50 * 2**20, "the volume wasn't emptied after the test"


def wide(n: int, day) -> pl.DataFrame:
    """``n`` rows that don't compress much (each name is a distinct hash), so they need room on disk."""
    df = rows(range(1000, 1000 + n), day)
    return df.with_columns(pl.col("id").hash(7).cast(pl.String).alias("name"))


def report(what: str, text: str) -> None:
    # A warning, so what Windows said shows in the job's warnings summary (pytest -q captures prints).
    warnings.warn(f"Disk full on Windows, {what}: {text[:400]!r}", stacklevel=2)


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
def test_a_write_that_fills_a_windows_volume_says_so_changes_nothing_and_the_next_run_works(room, merge):
    folder, fill = room
    s = Setup(folder, merge)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    before = s.store.version("LH_Bronze", "dbo", "events")
    s.source(wide(400_000, DAY2))
    fill()
    res = s.engine(DAY2).run("bronze.events")
    report(f"metadata run ({'merge' if merge else 'rewrite'})",
           res.message + " | " + " / ".join(step.detail for step in res.steps if step.detail)[-300:])
    assert res.status == "failed" and not res.retryable, res.message
    assert res.message.startswith("The disk is full (") and "The table was not changed." in res.message
    assert s.store.version("LH_Bronze", "dbo", "events") == before and ids(s) == [0, 1, 2]
    (folder / "filler.bin").unlink()  # space freed
    assert s.run(day=DAY2).status == "ok"
    assert len(ids(s)) == 400_003


def test_a_query_whose_temp_files_fill_a_windows_volume_says_the_drive_is_full(room):
    folder, fill = room
    temp = folder / "spill"
    temp.mkdir()
    fill()
    con = duckdb.connect()
    con.execute("SET memory_limit = '32MB'")
    con.execute("SET threads = 1")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET temp_directory = '{temp.as_posix()}'")  # no max_temp_directory_size: the drive itself fills
    with pytest.raises(duckdb.Error) as caught:
        con.execute("SELECT md5(i::VARCHAR) || md5((i + 1)::VARCHAR) AS s FROM range(3000000) t(i) "
                    "ORDER BY s").fetchall()
    kind, message = sql_worker.error_message(caught.value, {"temp_dir": str(temp)})
    report("SQL spill", f"{caught.value} | {kind}: {message}")
    assert kind == "disk" and "memory" not in message.lower(), (kind, message)
    assert "Nothing was saved." in message
    con.execute("SELECT 1").fetchall()  # the connection still works


def test_a_plain_file_write_on_a_full_windows_volume_is_enospc_without_a_winerror(room):
    folder, fill = room
    fill()
    with pytest.raises(OSError) as caught, open(folder / "more.bin", "wb", buffering=0) as f:
        for _ in range(FREE_MB + 2):
            f.write(b"x" * 2**20)
    report("file write", f"{caught.value!r} winerror={getattr(caught.value, 'winerror', None)}")
    # Measured: Python's own file writes go through the C runtime, which turns Windows' ERROR_DISK_FULL (112)
    # into errno ENOSPC with no winerror; delta-rs says "os error 112". disk_full matches both.
    assert caught.value.errno == errno.ENOSPC and disk_full.is_disk_full(caught.value)
