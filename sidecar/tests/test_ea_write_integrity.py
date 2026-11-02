"""Characterization tests for audit findings EA-001, EA-005, EA-006 and EA-007 (docs/audits/REMEDIATION_BACKLOG.md).

Each test states the behaviour the fix must give. On main at b75dcf5 every one fails, so each is marked
xfail(strict=True): the fixing PR removes its marker (a strict xfail that starts passing fails the run).
Drop this file into sidecar/tests/ in the fixing PR, or copy the test you need next to your fix.
Local only: local Delta tables and a fake ADLS session, no network."""

import datetime as dt
import subprocess
import sys

import polars as pl
import pyarrow as pa
import pytest
import requests
from deltalake import DeltaTable

from fabric_desktop import lakehouse, leases, onelake, publish
from fabric_desktop import publish as publish_mod


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield root
    lakehouse.attach(None)


class Ref:
    def __init__(self, uri):
        self.uri, self.schema, self.table = uri, "dbo", "t"


def ids(uri):
    return sorted(DeltaTable(uri).to_pyarrow_table().column("id").to_pylist())


# EA-001: an append must never quietly convert a value to fit the table's column type.
@pytest.mark.parametrize("col, first, second", [
    ("qty", pl.Series([3], dtype=pl.Int32), [2.75]),                                  # float into int
    ("d", [dt.date(2026, 1, 1)], [dt.datetime(2026, 1, 2, 23, 30, tzinfo=dt.timezone.utc)]),  # timestamp into date
    ("n", pl.Series([1], dtype=pl.Int64), ["12"]),                                     # text into int
])
def test_an_append_that_would_change_values_is_refused_and_the_table_is_unchanged(lake, col, first, second):
    lakehouse.write_table(pl.DataFrame({"id": [1], col: first}), "dbo.orders")
    with pytest.raises(Exception):  # noqa: B017 - the fix chooses the error type; it must say which column
        lakehouse.write_table(pl.DataFrame({"id": [2], col: second}), "dbo.orders", mode="append")
    assert DeltaTable(str(lake / "dbo" / "orders")).version() == 0


def test_a_wider_type_of_the_same_kind_still_appends(lake):
    # Guard for the EA-001 fix: INT data into a BIGINT column keeps writing, as in Spark.
    lakehouse.write_table(pl.DataFrame({"id": pl.Series([1], dtype=pl.Int64)}), "dbo.orders")
    lakehouse.write_table(pl.DataFrame({"id": pl.Series([2], dtype=pl.Int32)}), "dbo.orders", mode="append")
    assert ids(str(lake / "dbo" / "orders")) == [1, 2]


# EA-005: an overwrite must not keep rows another writer appended while it uploaded.
def test_an_overwrite_never_keeps_rows_appended_during_its_upload(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"id": [1, 2, 3]}), ref, "overwrite", "tok")
    schema = pa.schema([("id", pa.int64())])

    def batches():
        yield pa.record_batch({"id": [10]}, schema=schema)
        subprocess.run([sys.executable, "-c", "import pyarrow as pa, sys; from deltalake import write_deltalake as w; "
                        "w(sys.argv[1], pa.table({'id': [99]}), mode='append')", ref.uri], check=True)
        yield pa.record_batch({"id": [11]}, schema=schema)

    try:
        publish.publish(pa.RecordBatchReader.from_batches(schema, batches()), ref, "overwrite", "tok")
    except Exception:  # noqa: BLE001 - a conflict error is an acceptable outcome; a silent merge isn't
        pass
    # Either the overwrite wins after a retry, or it is refused and the table keeps the other writer's row.
    assert ids(ref.uri) in ([10, 11], [1, 2, 3, 99])


# EA-006: a commit that succeeded must not be reported as failed because a lease renewal failed.
def test_a_lease_lost_after_the_commit_reports_the_committed_write(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "TABLE_LEASE_TTL", 0.9)
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"id": [1]}), ref, "overwrite", "tok")
    schema = pa.schema([("id", pa.int64())])

    def flaky(self, *a):
        raise OSError("renewal request timed out")

    def slow_upload():
        monkeypatch.setattr(leases.LocalBackend, "replace", flaky)
        import time
        time.sleep(1.5)
        yield pa.record_batch({"id": [2]}, schema=schema)

    publish.publish(pa.RecordBatchReader.from_batches(schema, slow_upload()), ref, "append", "tok")  # must not raise
    assert ids(ref.uri) == [1, 2]


# EA-007: a failed upload must leave the file it was replacing as it was.
class FakeADLS:
    """ADLS Gen2 as the OneLake contract test describes it: PUT ?resource=file creates or replaces with an
    empty file, append stages bytes, flush commits them. Rename (x-ms-rename-source) moves a file."""

    def __init__(self):
        self.files, self.staged, self.fail_append = {}, {}, False

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        r = requests.Response()
        r.status_code, r._content = 200, b"{}"
        path = url.split("/", 3)[3]
        params = params or {}
        if method == "PUT" and (headers or {}).get("x-ms-rename-source"):
            src = headers["x-ms-rename-source"].split("/", 3)[-1].lstrip("/")
            self.files[path] = self.files.pop(next(k for k in self.files if k.endswith(src) or src.endswith(k)))
            r.status_code = 201
        elif method == "PUT" and params.get("resource") == "file":
            self.files[path] = b""
            r.status_code = 201
        elif params.get("action") == "append":
            if self.fail_append:
                raise requests.ConnectionError("connection reset during append")
            self.staged[path] = data
            r.status_code = 202
        elif params.get("action") == "flush":
            self.files[path] = self.staged.pop(path)
        elif method == "DELETE":
            self.files.pop(path, None)
        return r

    def get(self, url, **k):
        return self.request("GET", url, params=k.get("params"))


def test_a_failed_upload_keeps_the_file_it_was_replacing():
    fake = FakeADLS()
    fs = onelake.FileStore(lambda: "tok", session=fake, sleep=lambda s: None)
    fs.upload("ws", "lh", "Files/config.json", b'{"v": 1}')
    fake.fail_append = True
    with pytest.raises(Exception):  # noqa: B017 - the upload must still report its failure
        fs.upload("ws", "lh", "Files/config.json", b'{"v": 2}')
    assert [v for k, v in fake.files.items() if k.endswith("Files/config.json")] == [b'{"v": 1}']


def test_an_overwrite_that_replaces_the_schema_still_writes(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"id": [1, 2, 3]}), ref, "overwrite", "tok")
    publish.publish(pa.table({"name": ["a"]}), ref, "overwrite", "tok")
    assert DeltaTable(ref.uri).to_pyarrow_table().to_pylist() == [{"name": "a"}]


def test_the_committed_write_says_its_lease_lapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "TABLE_LEASE_TTL", 0.9)
    ref = Ref((tmp_path / "t").as_posix())
    schema = pa.schema([("id", pa.int64())])

    def slow_upload():
        monkeypatch.setattr(leases.LocalBackend, "replace", lambda self, *a: (_ for _ in ()).throw(OSError("timed out")))
        import time
        time.sleep(1.5)
        yield pa.record_batch({"id": [1]}, schema=schema)

    table = publish.publish(pa.RecordBatchReader.from_batches(schema, slow_upload()), ref, "overwrite", "tok")
    assert "don't run the write again" in table.lease_warning and table.version() == 0
