"""A notebook cell that fills the disk or runs out of memory says so plainly.

Measured (local-performance/out-of-memory, notebooks): a local Delta append that fills the disk
showed delta-rs's raw "Failed to parse parquet: ... No space left on device (os error 28)", and one
that ran out of memory ended the kernel with "stopped (exit code -6)". In both the table kept its
last version. The tests make the same errors without filling a disk.
"""

import errno
import sys

import pytest
import pyarrow as pa

from fabric_desktop import notebook
from fabric_desktop.kernel_proc import ProcessKernel, stopped_text

DELTA_TEXT = ("Failed to parse parquet: External: External: Generic LocalFileSystem error: Unable to copy data to "
              "file: No space left on device (os error 28)")


def test_a_full_disk_leads_with_what_to_do_and_keeps_the_raw_error():
    text = notebook.plain_error(OSError(DELTA_TEXT))
    assert text.startswith("The disk is full. A Delta table write that failed changed nothing. Free some space")
    assert text.endswith(f"({DELTA_TEXT})")
    assert notebook.plain_error(OSError(errno.ENOSPC, "No space left on device")).startswith("The disk is full.")


def test_running_out_of_memory_says_so_even_when_the_error_has_no_text():
    assert notebook.plain_error(MemoryError()) == (
        "This PC ran out of memory in this cell. Variables from earlier cells are kept. "
        "Close other apps, or work on less data at once, and run it again.")
    assert notebook.plain_error(pa.ArrowMemoryError("malloc of size 64 failed")).endswith("(malloc of size 64 failed)")


def test_other_errors_are_unchanged():
    assert notebook.plain_error(ValueError("bad value")) == "bad value"
    assert notebook.plain_error(KeyError("x")) == "'x'"


@pytest.mark.parametrize("code, memory", [(-6, True), (-9, True), (0xC0000409, True), (-1073740791, True),
                                          (0xC0000017, True), (1, False), (None, False)])
def test_a_stopped_process_names_running_out_of_memory_only_for_its_exit_codes(code, memory):
    text = stopped_text(code)
    assert text.startswith("The notebook's Python process stopped (exit code ")
    assert text.endswith("The next cell starts a fresh one.")
    assert ("most likely ran out of memory" in text) is memory
    if code in (0xC0000409, -1073740791):
        assert "exit code 0xC0000409" in text  # as Windows writes it, whichever sign Python gave it


@pytest.mark.skipif(sys.platform == "win32", reason="SIGABRT stands for Rust's abort; Windows has its own code")
def test_a_kernel_that_aborts_says_it_most_likely_ran_out_of_memory():
    k = ProcessKernel()
    try:
        r = k.run("import os\nos.abort()")  # what a failed allocation in Polars or delta-rs does
        assert r["status"] == "error" and r["outputs"][-1]["ename"] == "KernelStopped"
        assert "exit code -6" in r["outputs"][-1]["evalue"]
        assert "most likely ran out of memory" in r["outputs"][-1]["evalue"]
        assert k.run("print(1 + 1)")["outputs"][0]["text"].strip() == "2"  # a fresh process
    finally:
        k.close()


def test_a_cell_that_fills_the_disk_shows_the_plain_message(tmp_path):
    k = ProcessKernel()
    try:
        r = k.run("raise OSError(" + repr(DELTA_TEXT) + ")")
        err = r["outputs"][-1]
        assert r["status"] == "error" and err["ename"] == "OSError"
        assert err["evalue"].startswith("The disk is full.") and "No space left on device" in err["traceback"]
    finally:
        k.close()
