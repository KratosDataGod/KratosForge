"""write_queue.py: the queue's starting state and workers, the commit fingerprint, spills and stats.

These assertions pin what a mutation run found changeable without a failing test. A queue that
starts out holding bytes would make the first large write wait for memory that was never used,
and a worker that misses a write submitted while it idles would leave that write unsent."""

from __future__ import annotations

import hashlib
import logging
import threading
import time

import pyarrow as pa

from fabric_desktop import write_queue as W


def test_three_uploads_run_at_once_by_default():
    assert W.WORKERS == 3
    assert W.Queue(memory_cap=1).workers == 3


def test_a_new_queue_holds_nothing():
    q = W.Queue(memory_cap=10)
    assert q._held == 0
    assert q._ready == [] and q._pending == [] and q.warnings == []


def test_a_write_as_large_as_the_cap_starts_at_once_on_an_empty_queue():
    q = W.Queue(memory_cap=10)
    started = threading.Event()

    def submit():
        q.submit("t", "t", 10, lambda: "ok").result(timeout=5)
        started.set()

    threading.Thread(target=submit, daemon=True).start()
    assert started.wait(5), "the first write waited for memory nothing held"
    assert q.flush() == []
    assert q._held == 0


def test_no_rows_fingerprint_as_zeros_and_one_row_does_not():
    empty = pa.table({"a": pa.array([], pa.int64())})
    assert W.fingerprint_part(empty) == (0, 0, 0)
    rows, _total, _xor = W.fingerprint_part(pa.table({"a": [7]}))
    assert rows == 1


def test_a_write_submitted_while_the_worker_idles_still_runs():
    # The worker waits for more work after each write; one that arrives then must be picked up.
    q = W.Queue(workers=1, memory_cap=1 << 20)
    assert q.submit("t", "first", 0, lambda: 1).result(timeout=5) == 1
    time.sleep(0.3)  # the worker is waiting for more now
    assert q.submit("t", "second", 0, lambda: 2).result(timeout=3) == 2
    assert [(t.daemon, t.name) for t in q._threads] == [(True, "onelake-write")]


def test_the_fingerprint_kept_in_the_commit_is_32_hex_characters_of_the_columns_and_rows():
    schema = pa.schema([("a", pa.int64())])
    expected = hashlib.sha256(b"a:int64|1|2|3").hexdigest()[:32]
    assert W.fingerprint(schema, (1, 2, 3)) == expected
    assert W.combine([(1, 2, 3), (1, 5, 3)]) == (2, 7, 0)


def test_a_spill_makes_its_folder_and_removes_its_file_quietly(tmp_path, caplog):
    schema = pa.schema([("a", pa.int64())])
    spill = W.Spill(tmp_path / "not" / "made", schema)
    spill.add(pa.record_batch([pa.array([1, 2])], schema=schema))
    spill.close()
    assert spill.rows == 2
    assert spill.reader().read_all().num_rows == 2
    spill.remove()
    assert not spill.path.exists()
    with caplog.at_level(logging.INFO, logger=W.__name__):
        spill.remove()  # already gone is not a failure to report
    assert caplog.records == []


def test_stats_round_to_hundredths():
    s = W.Stats()
    s.wait = 1.23456
    s._spans = [(0.0, 2.34567)]
    s.tables = 1
    d = s.as_dict()
    assert (d["wait_seconds"], d["upload_seconds"]) == (1.23, 2.35)
