"""The Delta log a version inside the kept history reads stays on OneLake too (maintenance.keep_log, after_write).

keep_log lists _delta_log through delta-rs's own object store and reads each log file's date from it, as delta-rs's
cleanup does. Here the tables are abfss:// URIs, opened with OneLake's storage_options (onelake.storage_options),
and delta-rs talks to a stand-in Azure Blob service on this PC: a separate process (delta-rs holds the GIL while
it lists) keeping blobs in memory. As Azure does, it dates each blob when it is uploaded, gives that date to the
second (Last-Modified), lists in pages, and deletes in batches. Commits are dated as their writer's clock would
have stamped them (test_write_log_retention._history) and uploaded with those dates; the real cleanup runs.
"""

from __future__ import annotations

import email.utils
import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from xml.sax.saxutils import escape

PAGE = 4  # blobs a list request returns at most (Azure: 5000), so a log of a few versions takes several pages


# ---- the stand-in Azure Blob service (only the standard library: it runs as its own process)

class _Blobs:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.mtime: dict[str, float] = {}
        self.blocks: dict[tuple[str, str], bytes] = {}
        self.deleted: list[str] = []
        self.lock = threading.Lock()


def _handler(store: _Blobs) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            pass

        def _parts(self) -> tuple[str, str, dict[str, str]]:
            """(container, blob name, query): the container is the workspace, the same for every table here."""
            u = urlsplit(self.path)
            container, _, name = unquote(u.path).lstrip("/").partition("/")
            return container, name, {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}

        def _body(self) -> bytes:
            if "chunked" in (self.headers.get("Transfer-Encoding") or ""):
                out = b""
                while size := int(self.rfile.readline().split(b";")[0].strip(), 16):
                    out += self.rfile.read(size)
                    self.rfile.readline()
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return out
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _send(self, code: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _props(self, name: str) -> dict[str, str]:
            return {"Last-Modified": email.utils.formatdate(store.mtime[name], usegmt=True),
                    "ETag": f'"{hashlib.md5(store.data[name] + str(store.mtime[name]).encode()).hexdigest()}"',
                    "x-ms-blob-type": "BlockBlob"}

        def do_HEAD(self) -> None:
            self.do_GET()

        def _admin(self, what: str, q: dict[str, str]) -> None:
            with store.lock:
                if what == "load":  # a table folder on this PC, uploaded under prefix with each file's own date
                    root = Path(q["dir"])
                    for p in sorted(root.rglob("*")):
                        if p.is_file():
                            name = q["prefix"] + "/" + p.relative_to(root).as_posix()
                            store.data[name], store.mtime[name] = p.read_bytes(), p.stat().st_mtime
                elif what == "date":
                    store.mtime[q["name"]] = float(q["mtime"])
                body = json.dumps({"mtime": store.mtime, "deleted": store.deleted}).encode()
            self._send(200, body, {"Content-Type": "application/json"})

        def do_GET(self) -> None:
            container, name, q = self._parts()
            if container == "__admin":
                return self._admin(name, q)
            if q.get("comp") == "list":
                return self._list(q)
            with store.lock:
                if name not in store.data:
                    return self._send(404, b"", {"x-ms-error-code": "BlobNotFound"})
                body, props = store.data[name], self._props(name)
            if rng := self.headers.get("Range") or self.headers.get("x-ms-range"):
                m = re.match(r"bytes=(\d+)-(\d*)", rng)
                assert m is not None
                first = int(m.group(1))
                last = min(int(m.group(2)) if m.group(2) else len(body) - 1, len(body) - 1)
                props["Content-Range"] = f"bytes {first}-{last}/{len(body)}"
                return self._send(206, body[first:last + 1], props)
            self._send(200, body, props)

        def _list(self, q: dict[str, str]) -> None:
            prefix, delimiter = q.get("prefix", ""), q.get("delimiter")
            entries: dict[str, str] = {}
            with store.lock:
                for name in sorted(store.data):
                    if not name.startswith(prefix) or name < q.get("startFrom", "") or name < q.get("marker", ""):
                        continue
                    rest = name[len(prefix):]
                    if delimiter and delimiter in rest:
                        sub = prefix + rest.split(delimiter)[0] + delimiter
                        entries.setdefault(sub, f"<BlobPrefix><Name>{escape(sub)}</Name></BlobPrefix>")
                        continue
                    entries[name] = (
                        f"<Blob><Name>{escape(name)}</Name><Properties>"
                        f"<Last-Modified>{email.utils.formatdate(store.mtime[name], usegmt=True)}</Last-Modified>"
                        f"<Etag>0x{hashlib.md5(name.encode() + str(store.mtime[name]).encode()).hexdigest()[:16]}</Etag>"
                        f"<Content-Length>{len(store.data[name])}</Content-Length>"
                        f"<Content-Type>application/octet-stream</Content-Type><BlobType>BlockBlob</BlobType>"
                        f"</Properties></Blob>")
            keys = sorted(entries)
            page, rest_ = keys[:PAGE], keys[PAGE:]
            xml = ('<?xml version="1.0" encoding="utf-8"?><EnumerationResults>'
                   f"<Prefix>{escape(prefix)}</Prefix><Blobs>{''.join(entries[k] for k in page)}</Blobs>"
                   f"<NextMarker>{escape(rest_[0]) if rest_ else ''}</NextMarker></EnumerationResults>")
            self._send(200, xml.encode(), {"Content-Type": "application/xml"})

        def do_PUT(self) -> None:
            _container, name, q = self._parts()
            body = self._body()
            with store.lock:
                if q.get("comp") == "block":
                    store.blocks[(name, q["blockid"])] = body
                    return self._send(201)
                content = body
                if q.get("comp") == "blocklist":
                    ids = re.findall(rb"<(?:Latest|Uncommitted|Committed)>([^<]*)<", body)
                    content = b"".join(store.blocks.pop((name, i.decode()), b"") for i in ids)
                if source := self.headers.get("x-ms-copy-source"):
                    from_name = unquote(urlsplit(source).path).lstrip("/").partition("/")[2]
                    if from_name not in store.data:
                        return self._send(404, b"", {"x-ms-error-code": "CannotVerifyCopySource"})
                    content = store.data[from_name]
                if self.headers.get("If-None-Match") == "*" and name in store.data:
                    return self._send(409, b"", {"x-ms-error-code": "BlobAlreadyExists"})
                store.data[name], store.mtime[name] = content, time.time()
                headers = self._props(name)
            if source:
                return self._send(202, b"", headers | {"x-ms-copy-status": "success"})
            self._send(201, b"", headers)

        def _delete(self, name: str) -> bool:
            with store.lock:
                if name not in store.data:
                    return False
                del store.data[name], store.mtime[name]
                store.deleted.append(name)
                return True

        def do_DELETE(self) -> None:
            _container, name, _q = self._parts()
            self._send(202) if self._delete(name) else self._send(404, b"", {"x-ms-error-code": "BlobNotFound"})

        def do_POST(self) -> None:
            _container, _name, q = self._parts()
            body = self._body()
            if q.get("comp") != "batch":
                return self._send(400)
            boundary = (self.headers.get("Content-Type") or "").split("boundary=")[1]
            answer = "batchresponse_" + uuid.uuid4().hex
            out = []
            for part in body.split(b"--" + boundary.encode()):
                if b"DELETE " not in part:
                    continue
                cid = re.search(rb"Content-ID:\s*(\d+)", part)
                path = re.search(rb"DELETE (\S+) HTTP", part)
                assert cid is not None and path is not None
                name = unquote(urlsplit(path.group(1).decode()).path).lstrip("/").partition("/")[2]
                status = ("202 Accepted" if self._delete(name)
                          else "404 The specified blob does not exist.\r\nx-ms-error-code: BlobNotFound")
                out.append(f"--{answer}\r\nContent-Type: application/http\r\nContent-ID: {cid.group(1).decode()}\r\n\r\n"
                           f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n")
            self._send(202, ("".join(out) + f"--{answer}--\r\n").encode(),
                       {"Content-Type": f"multipart/mixed; boundary={answer}"})

    return Handler


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(_Blobs()))
    print(server.server_address[1], flush=True)
    server.serve_forever()
    sys.exit(0)


# ---- the tests

import polars as pl  # noqa: E402
import pyarrow as pa  # noqa: E402
import pytest  # noqa: E402
from deltalake import DeltaTable  # noqa: E402

from fabric_desktop import lakehouse, leases, maintenance, onelake, publish  # noqa: E402
from fabric_desktop.meta.store import OneLakeStore  # noqa: E402
from tests.test_write_log_retention import AGES, CHECKPOINT, STORE_WRITES, _history  # noqa: E402

DAY = 24 * 3600
WS, LH, LH_ID = "ws-test", "LH_Silver", "lh-silver"
TABLE = f"{LH_ID}/Tables/dbo/t"  # the table's blobs, under the workspace's container
URI = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{TABLE}"


class Service:
    """The stand-in Blob service: its endpoint, and its own view of the blobs (dates, deletions)."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def admin(self, what: str = "state", **q: str) -> dict:
        query = "&".join(f"{k}={quote(v, safe='')}" for k, v in q.items())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"{self.endpoint}/__admin/{what}?{query}", timeout=30) as r:
            return json.load(r)

    def options(self, token: str = "tok") -> dict[str, str]:
        """OneLake's own storage_options, sent to this service instead of onelake.dfs.fabric.microsoft.com."""
        return onelake.storage_options(token) | {"azure_storage_endpoint": self.endpoint, "azure_allow_http": "true",
                                                 "max_retries": "1", "retry_timeout": "20s", "timeout": "20s"}

    def upload(self, folder: Path, prefix: str = TABLE) -> None:
        self.admin("load", dir=str(folder), prefix=prefix)

    def log(self, prefix: str = TABLE) -> dict[str, float]:
        """The table's log files as the service holds them, each with its date."""
        mark = prefix + "/_delta_log/"
        return {k[len(mark):]: v for k, v in self.admin()["mtime"].items() if k.startswith(mark)}


@pytest.fixture
def service(monkeypatch):
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    proc = subprocess.Popen([sys.executable, "-I", __file__], stdout=subprocess.PIPE, text=True,
                            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    try:
        assert proc.stdout is not None
        yield Service(f"http://127.0.0.1:{int(proc.stdout.readline())}")
    finally:
        proc.kill()
        proc.wait()


class StandInOneLake(OneLakeStore):
    """OneLakeStore as it is (abfss:// URIs from TableRef, onelake.storage_options), but at the stand-in service."""

    def __init__(self, service: Service, cache: Path):
        super().__init__("dev", WS, {LH: LH_ID}, lambda: "tok", cache)
        self.service = service

    def options(self) -> dict[str, str]:
        return self.service.options(self._token())


def _seed(service: Service, tmp_path: Path, ages: list[float], config: dict[str, str] | None = None,
          prefix: str = TABLE) -> None:
    """test_write_log_retention's history (version v holds id v, dated ages[v] days ago, a checkpoint at each;
    version 3, a day ago, sets a checkpoint interval of 5), made on this PC and uploaded with its dates."""
    folder = tmp_path / f"seed-{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    _history(str(folder), ages, config)
    service.upload(folder, prefix)


def _ids(service: Service, version: int | None = None, uri: str = URI) -> list[int]:
    table = DeltaTable(uri, version=version, storage_options=service.options())
    return sorted(table.to_pyarrow_table().column("id").to_pylist())


def _ids_at(service: Service, days_ago: float) -> list[int]:
    table = DeltaTable(URI, storage_options=service.options())
    table.load_as_version(datetime.fromtimestamp(time.time() - days_ago * DAY, timezone.utc))
    return sorted(table.to_pyarrow_table().column("id").to_pylist())


def _kept(service: Service) -> None:
    """test_write_log_retention._kept, on OneLake: version 1 (35 days old, current until 2 days ago) loads and a
    time travel to 3 days ago reads it; the write checkpointed; and the cleanup ran with OneLake's options:
    version 0's log, 100 days old and before the history kept, is gone from the service."""
    log = service.log()
    assert f"{CHECKPOINT:020d}.checkpoint.parquet" in log
    assert not {f"{0:020d}.json", f"{0:020d}.checkpoint.parquet"} & set(log)
    assert _ids(service, 1) == [1] and _ids(service, 2) == [2]
    assert _ids_at(service, 3) == [1]


# ---- the stand-in behaves as OneLake does where keep_log depends on it

def test_the_stand_in_dates_blobs_to_the_second_and_lists_in_pages(service, tmp_path):
    from deltalake.fs import DeltaStorageHandler
    from pyarrow.fs import FileSelector

    _seed(service, tmp_path, AGES)
    log = service.log()
    assert len(log) > PAGE  # more than one page
    service.admin("date", name=f"{TABLE}/_delta_log/{1:020d}.json", mtime="1700000000.875")
    table = DeltaTable(URI, storage_options=service.options())
    listed = {i.base_name: i.mtime.timestamp()
              for i in DeltaStorageHandler(table.table_uri, service.options()).get_file_info_selector(
                  FileSelector("_delta_log"))}
    assert set(listed) == set(log)  # every page
    assert listed[f"{1:020d}.json"] == 1700000000  # Last-Modified: to the second
    assert all(0 <= when - listed[name] < 1 for name, when in service.log().items())


# ---- keep_log and after_write on an abfss:// table with OneLake's options

def test_keep_log_on_onelake_raises_the_log_retention_from_the_dates_the_service_gives(service, tmp_path):
    _seed(service, tmp_path, AGES)  # version 1: 35 days old, current until 2 days ago; Delta's 30-day log
    options = service.options()
    assert maintenance.keep_log(URI, maintenance.retention_hours(7), options) is True
    config = DeltaTable(URI, storage_options=options).metadata().configuration
    assert config["delta.logRetentionDuration"] == "interval 60 days"  # the property commit landed on OneLake
    assert maintenance.keep_log(URI, maintenance.retention_hours(7), options) is False
    DeltaTable(URI, storage_options=options).cleanup_metadata()
    assert _ids(service, 1) == [1] and _ids_at(service, 3) == [1]


@pytest.mark.parametrize("how", STORE_WRITES)
def test_a_onelake_store_write_on_a_checkpoint_version_keeps_the_history_kept(service, tmp_path, how):
    """Store.write / write_stream / upsert / merge_insert on OneLakeStore: after_write lists the log, commits the
    longer log retention and cleans the log, each through OneLake's options."""
    _seed(service, tmp_path, AGES)
    STORE_WRITES[how](StandInOneLake(service, tmp_path / "cache"))
    assert 9 in _ids(service, CHECKPOINT)
    _kept(service)


def test_a_notebook_write_to_onelake_keeps_the_history_kept(attached, tmp_path):
    """lakehouse.write_table (publish: a notebook's write, saveAsTable, a job's) to a OneLake table."""
    _seed(attached, tmp_path, AGES)
    lakehouse.write_table(pa.table({"id": [9], "region": ["EU"]}), "dbo.t", mode="append")
    _kept(attached)


def test_a_table_copied_with_new_dates_cleans_nothing_still_read(service, tmp_path):
    """A table copied or restored on OneLake: every log file is dated when it was uploaded, its commitInfo
    keeps the writer's dates. keep_log takes the later of the two for a version's date; the cleanup goes by the
    blob's date alone, so nothing is old enough to go, and every version still loads."""
    folder = tmp_path / "copied"
    folder.mkdir()
    _history(str(folder), [100, 35, 2])
    now = time.time()
    for p in (folder / "_delta_log").iterdir():
        os.utime(p, (now - 60, now - 60))  # uploaded a minute ago
    service.upload(folder)
    options = service.options()
    assert maintenance.keep_log(URI, maintenance.retention_hours(7), options) is False
    StandInOneLake(service, tmp_path / "cache").write(LH, "dbo", "t", pl.DataFrame({"id": [9], "region": ["EU"]}),
                                                      mode="append")
    assert not service.admin()["deleted"]
    assert [_ids(service, v) for v in (0, 1, 2)] == [[0], [1], [2]]


def test_a_log_dated_older_than_its_commits_is_kept_by_the_blobs_dates(service, tmp_path):
    """The reverse: commitInfo says every version is recent (the writer's clock was behind OneLake's, or the
    commits were rewritten), the blobs are old. The cleanup deletes by the blobs' dates, so keep_log keeps by
    them: the log retention covers the oldest blob any version inside the history kept reads."""
    folder = tmp_path / "skewed"
    folder.mkdir()
    _history(str(folder), [3, 2.5, 2])  # commitInfo: all inside 7 days
    for v, days in enumerate([100, 35, 20, 10]):
        for p in (folder / "_delta_log").glob(f"{v:020d}.*"):
            os.utime(p, (time.time() - days * DAY,) * 2)
    service.upload(folder)
    store = StandInOneLake(service, tmp_path / "cache")
    store.write(LH, "dbo", "t", pl.DataFrame({"id": [9], "region": ["EU"]}), mode="append")  # version 4
    config = DeltaTable(URI, storage_options=service.options()).metadata().configuration
    assert maintenance.log_seconds(config) >= 101 * DAY
    assert not service.admin()["deleted"]
    assert [_ids(service, v) for v in (0, 1, 2)] == [[0], [1], [2]]


# ---- a notebook's OPTIMIZE and VACUUM on a OneLake table (lakehouse.maintain_table)

@pytest.fixture
def attached(service, tmp_path, monkeypatch):
    monkeypatch.setattr(lakehouse, "storage_options", service.options)
    monkeypatch.setattr(publish, "storage_options", service.options)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda *_a: None)
    # The write lease is OneLake's Files API, not this service's: kept on this PC instead.
    monkeypatch.setattr(publish, "_lease", lambda ref, _token: leases.table_lease(
        leases.LocalBackend(tmp_path / "_leases"), ref.schema, ref.table, owner=publish.WRITER))
    lakehouse.attach(lakehouse.Attached(WS, LH_ID, LH, lambda: "tok", tmp_path))
    yield service
    lakehouse.attach(None)


@pytest.mark.parametrize("op", ["optimize", "vacuum"])
def test_notebook_maintenance_on_onelake_keeps_the_history_kept(attached, tmp_path, op):
    """OPTIMIZE and VACUUM ignore post-commit hooks and clean the log at a checkpoint: keep_log comes first,
    through OneLake's options. The commit lands on version 4, a checkpoint version."""
    _seed(attached, tmp_path, AGES)
    lakehouse.maintain_table("dbo.t", op)
    assert _ids(attached, 1) == [1] and _ids_at(attached, 3) == [1]
    assert maintenance.log_seconds(DeltaTable(URI, storage_options=attached.options()).metadata().configuration) \
        >= 36 * DAY


ELSEWHERE = {"cleanup off in Spark's words": {"delta.enableExpiredLogCleanup": "False"},
             "100 days in Spark's words": {"delta.logRetentionDuration": "INTERVAL 100 DAYS"}}
CHANGES = {"optimize": lambda: lakehouse.maintain_table("dbo.t", "optimize"),
           "vacuum": lambda: lakehouse.maintain_table("dbo.t", "vacuum"),
           "write": lambda: lakehouse.write_table(pa.table({"id": [9], "region": ["EU"]}), "dbo.t", mode="append")}


@pytest.mark.parametrize("change", CHANGES)
@pytest.mark.parametrize("config", ELSEWHERE)
def test_onelake_keeps_the_history_kept_whatever_another_engine_wrote_in_its_properties(attached, tmp_path, config,
                                                                                         change):
    """Properties Spark reads as "no log cleanup" or "100 days", and delta-rs as its default 30 days: delta-rs's
    cleanup (OPTIMIZE's and VACUUM's own, or after_write's) would delete version 1's log, 35 days old, unless
    keep_log reads them as delta-rs does."""
    _seed(attached, tmp_path, AGES, ELSEWHERE[config])
    CHANGES[change]()
    assert _ids(attached, 1) == [1] and _ids_at(attached, 3) == [1]
    if change == "write":  # OPTIMIZE and VACUUM land on version 5, after keep_log's property commit
        assert f"{CHECKPOINT:020d}.checkpoint.parquet" in attached.log()
