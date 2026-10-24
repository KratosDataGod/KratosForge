"""OneLake writes from notebooks on this PC: in the background (write_queue), zstd, and skipped when
a rewrite holds the same rows. The upload line is what a write waits on (thread "Local TPC-H run
slowdown"), so a cell goes on computing while its writes upload, and unchanged tables upload nothing."""

import threading
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable

from fabric_desktop import app_settings, lakehouse, publish, write_queue
from fabric_desktop.write_queue import Queue, WriteFailed


class Ref:  # publish() needs the table's address and name (for its lease)
    def __init__(self, uri):
        self.uri, self.schema, self.table = uri, "dbo", "t"


def data(*ids):
    return pa.table({"id": list(ids), "v": [f"v{i}" for i in ids]})


# ---- the queue


def test_one_tables_writes_commit_in_order_and_tables_upload_side_by_side():
    q, order, both = Queue(workers=3), [], threading.Barrier(2, timeout=5)

    def work(name, wait=0.0, meet=False):
        def run():
            if meet:
                both.wait()  # only passes if the other table's write runs at the same time
            time.sleep(wait)
            order.append(name)
            return name
        return run
    first = q.submit("a", "a1", 0, work("a1", 0.1, meet=True))
    second = q.submit("a", "a2", 0, work("a2"))
    other = q.submit("b", "b1", 0, work("b1", meet=True))
    assert q.flush() == []
    assert order.index("a1") < order.index("a2")
    assert (first.result(), second.result(), other.result()) == ("a1", "a2", "b1")


def test_a_failed_write_fails_the_writes_after_it_to_the_same_table():
    q = Queue()

    def boom():
        raise OSError("connection reset")
    q.submit("a", "dbo.a", 0, boom)
    later = q.submit("a", "dbo.a", 0, lambda: "never")
    fine = q.submit("b", "dbo.b", 0, lambda: "ok")
    failed = q.flush()
    assert failed == ["dbo.a: OSError: connection reset",
                      "dbo.a: WriteFailed: dbo.a wasn't written because an earlier write to the same table failed."]
    assert isinstance(later.exception(), WriteFailed) and fine.result() == "ok"
    assert q.flush() == []  # flushed writes are forgotten: the next cell starts clean


def test_cancel_drops_writes_that_havent_started():
    q, gate = Queue(workers=1), threading.Event()
    q.submit("a", "dbo.a", 0, gate.wait)
    q.submit("a", "dbo.a", 0, lambda: "never")
    threading.Timer(0.2, gate.set).start()
    assert q.flush(cancel=True) == ["dbo.a: not written (the cell was cancelled)"]


def test_memory_held_for_uploads_is_capped():
    q, gate, started = Queue(memory_cap=100), threading.Event(), []
    q.submit("a", "a", 80, gate.wait)
    waited = threading.Event()

    def second():
        q.submit("b", "b", 80, lambda: started.append("b"))
        waited.set()
    threading.Thread(target=second).start()
    assert not waited.wait(0.3)  # 160 bytes would be held: the second write waits for the first
    gate.set()
    assert waited.wait(5)
    assert q.flush() == [] and started == ["b"]


def test_fingerprint_ignores_row_order_and_adds_up_over_a_stream():
    whole = write_queue.fingerprint_part(data(1, 2, 3, 4))
    parts = write_queue.combine([write_queue.fingerprint_part(data(3, 1)), write_queue.fingerprint_part(data(4, 2))])
    assert parts == whole
    schema = data(1).schema
    assert write_queue.fingerprint(schema, whole) != write_queue.fingerprint(schema, write_queue.fingerprint_part(data(1, 2, 3)))
    assert write_queue.fingerprint(schema, whole) != write_queue.fingerprint(pa.schema([("id", pa.int32()), ("v", pa.string())]), whole)


def test_a_spill_reads_back_what_was_written_and_cleans_up(tmp_path):
    spill = write_queue.Spill(tmp_path, data(1).schema)
    for batch in data(1, 2).to_batches() + data(3).to_batches():
        spill.add(batch)
    spill.close()
    assert spill.rows == 3 and spill.reader().read_all().column("id").to_pylist() == [1, 2, 3]
    spill.remove()
    assert list(tmp_path.iterdir()) == []


# ---- publish: zstd, the fingerprint, skip unchanged


def test_publish_writes_zstd_and_skips_an_unchanged_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: {"onelake_compression": "zstd"}.get(key))
    uri = str(tmp_path / "t")
    rows = write_queue.fingerprint(data(1).schema, write_queue.fingerprint_part(data(1, 2)))
    first = publish.publish(data(1, 2), Ref(uri), "overwrite", "tok", rows=rows, skip_unchanged=True)
    assert first.version() == 0 and first.skipped is False
    again = publish.publish(data(2, 1), Ref(uri), "overwrite", "tok", rows=rows, skip_unchanged=True)
    assert again.skipped is True and DeltaTable(uri).version() == 0  # nothing uploaded, no new version
    files = DeltaTable(uri).file_uris()
    assert {pq.ParquetFile(f).metadata.row_group(0).column(0).compression for f in files} == {"ZSTD"}

    publish.publish(data(3), Ref(uri), "append", "tok")  # someone added rows: the same rows again must write
    assert publish.publish(data(1, 2), Ref(uri), "overwrite", "tok", rows=rows, skip_unchanged=True).skipped is False
    assert DeltaTable(uri).version() == 2


def test_snappy_when_the_setting_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: {"onelake_compression": "snappy"}.get(key))
    uri = str(tmp_path / "t")
    publish.publish(data(1), Ref(uri), "overwrite", "tok")
    f = DeltaTable(uri).file_uris()[0]
    assert pq.ParquetFile(f).metadata.row_group(0).column(0).compression == "SNAPPY"


# ---- settings


def test_the_onelake_settings_take_only_their_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "settings.json")
    assert app_settings.value("onelake_writes") == "local_first"
    assert app_settings.value("onelake_compression") == "zstd"
    assert app_settings.value("onelake_skip_unchanged") is True
    app_settings.save({"onelake_writes": "direct", "onelake_compression": "snappy", "onelake_skip_unchanged": False})
    assert (app_settings.value("onelake_writes"), app_settings.value("onelake_compression")) == ("direct", "snappy")
    with pytest.raises(ValueError, match="local_first, direct"):
        app_settings.save({"onelake_writes": "sometimes"})
    app_settings.save({"onelake_writes": None})
    assert app_settings.value("onelake_writes") == "local_first"


# ---- notebooks: the cell ends when its writes are in


LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "LH_Gold"}


@pytest.fixture
def service(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    calls = []

    def local_publish(data, ref, mode, token, schema_mode="auto", rows=None, skip_unchanged=False):
        calls.append((ref.table, threading.current_thread().name, rows))
        if ref.table == "broken":
            raise OSError("connection reset by peer")
        time.sleep(0.2)
        return publish.publish(data, Ref(str(tmp_path / "onelake" / ref.table)), mode, token, schema_mode,
                               rows, skip_unchanged)
    monkeypatch.setattr(lakehouse, "publish", local_publish)
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "settings.json")
    svc = Service(client_id="x", root=tmp_path)
    svc._auth = type("Auth", (), {"storage_token": lambda self: "tok"})()
    svc._schema_cache = {LH["lakehouse_id"]: True}
    svc.calls = calls
    svc.onelake = tmp_path / "onelake"
    return svc


WRITE = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
         "w = lakehouse.write_table(pa.table({'id': [1, 2]}), 'sales')\n")


def test_a_notebook_writes_in_the_background_and_the_cell_waits_for_it(service):
    out = service.run_cell(WRITE + "print(type(w).__name__, w.rows)", LH)
    assert out["status"] == "ok", out
    assert out["outputs"][0]["text"].strip() == "PendingWrite 2"
    assert service.calls[0][1] == "onelake-write"  # uploaded off the cell's thread
    assert DeltaTable(str(service.onelake / "sales")).version() == 0  # in OneLake once the cell ended


def test_a_rerun_with_the_same_rows_uploads_nothing(service):
    assert service.run_cell(WRITE, LH)["status"] == "ok"
    out = service.run_cell(WRITE + "w", LH)
    assert "unchanged" in out["outputs"][-1]["text"] and "nothing was uploaded" in out["outputs"][-1]["text"]
    assert DeltaTable(str(service.onelake / "sales")).version() == 0


def test_a_failed_background_write_fails_its_cell(service):
    code = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
            "lakehouse.write_table(pa.table({'id': [1]}), 'broken')\nprint('went on')")
    out = service.run_cell(code, LH)
    assert out["status"] == "error"
    assert out["outputs"][0]["text"].strip() == "went on"
    assert out["outputs"][-1]["ename"] == "WriteFailed" and "connection reset by peer" in out["outputs"][-1]["evalue"]
    assert service.run_cell("1", LH)["status"] == "ok"  # the next cell starts clean


def test_a_read_waits_for_the_cells_own_writes(service, monkeypatch):
    seen = []
    monkeypatch.setattr(lakehouse, "_open_table", lambda *a, **k: seen.append(service.onelake.joinpath("sales").exists()))
    service.run_cell(WRITE + "lakehouse._see_own_writes()\nlakehouse._open_table('sales')", LH)
    assert seen == [True]


def test_a_stream_is_one_commit(service):
    code = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
            "t = pa.table({'id': list(range(10))})\n"
            "lakehouse.write_table(pa.RecordBatchReader.from_batches(t.schema, t.to_batches(max_chunksize=3)), 'sales')")
    assert service.run_cell(code, LH)["status"] == "ok"
    table = DeltaTable(str(service.onelake / "sales"))
    assert table.version() == 0 and sorted(table.to_pyarrow_table().column("id").to_pylist()) == list(range(10))


def test_a_write_leaves_nothing_on_this_pcs_disk(service, tmp_path):
    code = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
            "t = pa.table({'id': list(range(10))})\n"
            "lakehouse.write_table(pa.RecordBatchReader.from_batches(t.schema, t.to_batches(max_chunksize=3)), 'sales')\n"
            "lakehouse.write_table(pa.table({'id': [1]}), 'broken')")
    assert service.run_cell(code, LH)["status"] == "error"  # one upload failed, one went in
    staging = [p for p in tmp_path.rglob("*") if p.is_file() and "staging" in p.parts]
    assert staging == []  # no spill file and no staged table, after a failed upload too


def test_a_cancelled_write_removes_its_spill_file(tmp_path):
    import threading as _t
    spill = write_queue.Spill(tmp_path, data(1).schema)
    spill.add(data(1).to_batches()[0])
    spill.close()
    q, gate = Queue(workers=1), _t.Event()
    q.submit("a", "a", 0, gate.wait)
    later = q.submit("a", "a", 0, lambda: None)
    later.add_done_callback(lambda f: spill.remove() if f.cancelled() else None)  # as lakehouse.write_table does
    _t.Timer(0.2, gate.set).start()
    q.flush(cancel=True)
    assert list(tmp_path.iterdir()) == []


def test_a_notebook_can_write_direct(service):
    service.notebooks.save("nb", [{"type": "code", "source": "1"}], LH)
    assert service.notebook_onelake_writes_set("nb", "direct") == {"onelake_writes": "direct", "effective": "direct"}
    assert service.open_notebook("nb")["onelake_writes"] == "direct"
    out = service.run_cell(WRITE + "print(type(w).__name__)", LH, notebook="nb")
    assert out["outputs"][0]["text"].strip() == "WriteResult"
    assert service.calls[0][1] != "onelake-write"
    with pytest.raises(ValueError):
        service.notebook_onelake_writes_set("nb", "fast")
    assert service.notebook_onelake_writes_set("nb", None)["effective"] == "local_first"


# ---- timing: how long a cell waited on its writes (the side-by-side card's split)


def test_stats_count_a_nested_wait_once_and_side_by_side_uploads_once():
    s = write_queue.Stats()
    assert s.as_dict() is None  # a cell that wrote nothing has nothing to say
    with s.waiting():
        with s.waiting():
            time.sleep(0.05)
    assert 0.03 <= s.wait < 0.2  # Windows' clock ticks about every 16 ms, so a 0.05 s sleep can read 0.047
    now = time.monotonic()
    s.wrote(now - 2, 10, 100, False)
    s.wrote(now - 1, 5, 50, False)  # overlapped the first: the second it added counts once
    s.wrote(now - 1, 0, None, True)
    d = s.as_dict()
    assert 1.9 < d["upload_seconds"] < 2.2
    assert (d["tables"], d["rows"], d["bytes"], d["skipped"]) == (3, 15, 150, 1)


def test_a_cell_reports_its_upload_wait_and_what_it_uploaded(service):
    out = service.run_cell(WRITE, LH)
    onelake = out["onelake"]
    assert onelake["tables"] == 1 and onelake["rows"] == 2 and onelake["bytes"] > 0
    assert onelake["wait_seconds"] >= 0.15  # the cell ended when the 0.2 s upload was in
    assert onelake["upload_seconds"] >= 0.2
    assert "onelake" not in service.run_cell("1 + 1", LH)  # the next cell starts at zero


def test_a_cell_that_computes_while_it_uploads_waits_less_than_the_upload(service):
    out = service.run_cell(WRITE + "import time\ntime.sleep(0.3)", LH)
    assert out["onelake"]["upload_seconds"] >= 0.2
    assert out["onelake"]["wait_seconds"] < 0.1  # the upload ran while the cell slept


def test_a_direct_write_is_all_wait(service):
    service.notebooks.save("nb", [{"type": "code", "source": "1"}], LH)
    service.notebook_onelake_writes_set("nb", "direct")
    onelake = service.run_cell(WRITE + "import time\ntime.sleep(0.3)", LH, notebook="nb")["onelake"]
    assert onelake["wait_seconds"] >= 0.2 and onelake["tables"] == 1


def test_publish_says_what_an_append_uploaded(tmp_path):
    ref = Ref(str(tmp_path / "t"))
    first = publish.publish(data(*range(1000)), ref, "overwrite", "tok")
    assert first.written_bytes == sum(pa.table(first.get_add_actions(flatten=True)).column("size_bytes").to_pylist())
    second = publish.publish(data(1), ref, "append", "tok")
    assert 0 < second.written_bytes < first.written_bytes  # only the new file
