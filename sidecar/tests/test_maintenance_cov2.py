"""maintenance.py's walk of a table folder at the two edges the rest of the suite leaves untaken: an entry
that is neither a folder nor a file (a named pipe), and a folder with no _delta_log."""

from __future__ import annotations

import os
import sys
import time

import pytest

from fabric_desktop import maintenance

STAGED = "part-00000-0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0-c000.snappy.parquet#1"
OLD = time.time() - 3 * 3600


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes are a POSIX file kind")
def test_a_named_pipe_in_a_table_folder_is_neither_listed_nor_followed(tmp_path):
    (tmp_path / "data.parquet").write_bytes(b"x")
    os.mkfifo(tmp_path / "pipe")
    assert [e.name for e in maintenance._data_folder_files(str(tmp_path))] == [
        "data.parquet"
    ]


def test_staged_files_in_a_folder_with_no_log_are_found_by_their_data_names(tmp_path):
    staged = tmp_path / STAGED
    staged.write_bytes(b"x")
    os.utime(staged, (OLD, OLD))
    (tmp_path / "fresh.parquet#2").write_bytes(b"x")
    assert not (tmp_path / "_delta_log").exists()
    assert maintenance._stale_staged(str(tmp_path)) == [STAGED]
