"""onelake.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4) showed no test
pinned. Every OneLake answer here is a stand-in: a 400 at each kind of request, the time limits each
request is sent with, a download stopping exactly at its size limit, a Range read cut to its length,
listing flags and pages, the read-only storage options, dates, and what each call returns. Nothing
reaches a tenant."""

from __future__ import annotations

import dataclasses

import pytest
import requests

from fabric_desktop import onelake as O
from fabric_desktop.paging import PagingError

MB = 1024 * 1024


class _Resp:
    def __init__(self, status=200, body=None, content=b"", headers=None, chunks=None):
        self.status_code, self._body, self.content = status, body or {}, content
        self.headers, self.text, self.url, self.reason = headers or {}, "", "https://onelake.example/x", ""
        self._chunks = chunks if chunks is not None else [content]
        self.chunk_sizes = []

    def json(self):
        return self._body

    def iter_content(self, size):
        self.chunk_sizes.append(size)
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def _next(self, method, url, kw):
        self.calls.append((method, url, kw))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def get(self, url, **kw):
        return self._next("GET", url, kw)

    def request(self, method, url, **kw):
        return self._next(method, url, kw)


def store(*answers):
    s = _Session(*answers)
    return O.FileStore(lambda: "tok", session=s, sleep=lambda x: None), s


def test_check_name_gives_the_name_back():
    assert O.check_name("table", "orders") == "orders"


def test_a_short_read_fails_within_a_minute():
    assert O.read_storage_options("tok") == {
        "bearer_token": "tok", "use_fabric_endpoint": "true", "timeout": "60s", "connect_timeout": "10s",
        "retry_timeout": "60s", "max_retries": "2"}


def test_a_table_ref_has_a_schema_folder_unless_flat_and_cant_be_changed():
    ref = O.TableRef("ws", "lh", "dbo", "t")
    assert ref.uri.endswith("/lh/Tables/dbo/t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.table = "u"


def test_the_table_catalog_lists_schemas_and_refuses_a_400():
    s = _Session(_Resp(body={"schemas": [{"name": "dbo"}, {"name": "sales"}]}))
    assert O.TableCatalog(lambda: "tok", session=s, sleep=lambda x: None).list_schemas("ws", "lh") == ["dbo", "sales"]
    bad = O.TableCatalog(lambda: "tok", session=_Session(_Resp(400)), sleep=lambda x: None)
    with pytest.raises(requests.HTTPError, match="list the lakehouse's schemas"):
        bad.list_schemas("ws", "lh")


def test_dates_become_iso_or_nothing():
    assert O._iso(None) == "" and O._iso("") == "" and O._iso("not a date") == ""
    assert O._iso("Wed, 11 Nov 2026 10:00:00 GMT") == "2026-11-11T10:00:00+00:00"


def test_list_is_shallow_by_default_with_a_minute_per_page():
    fs, s = store(_Resp(body={"paths": [{"name": "lh/Files/a.csv", "contentLength": "3"}]}))
    assert [e["path"] for e in fs.list("ws", "lh")] == ["Files/a.csv"]
    _, _, kw = s.calls[0]
    assert kw["params"]["recursive"] == "false" and kw["timeout"] == 60
    fs.list("ws", "lh", recursive=True)
    assert s.calls[1][2]["params"]["recursive"] == "true"


def test_the_lakehouse_root_can_be_listed_and_its_repeated_page_is_named_files():
    page = _Resp(body={"paths": []}, headers={"x-ms-continuation": "same"})
    fs, s = store(page)
    with pytest.raises(PagingError, match="Listing Files in OneLake stopped"):
        fs.list("ws", "lh", directory="")
    assert s.calls[0][2]["params"]["directory"] == "lh/"


def test_list_refuses_a_400():
    fs, _ = store(_Resp(400))
    with pytest.raises(requests.HTTPError, match="list Files"):
        fs.list("ws", "lh")


def test_a_probe_answered_400_says_why_and_waits_a_minute():
    fs, s = store(_Resp(400, body={"error": {"code": "BadRequest", "message": "no"}}))
    assert fs.probe("ws", "lh", "Files/x") == (400, "BadRequest no")
    assert s.calls[0][2]["timeout"] == 60


def test_a_download_exactly_at_its_limit_is_kept(tmp_path):
    fs, s = store(_Resp(chunks=[b"a" * 3, b"b" * 2]))
    dest = tmp_path / "deep" / "er" / "f.bin"
    assert fs.download("ws", "lh", "Files/f.bin", dest, max_bytes=5) == dest
    assert dest.read_bytes() == b"aaabb"
    assert s.calls[0][2]["stream"] is True and s.calls[0][2]["timeout"] == 300
    assert s.answers[0].chunk_sizes == [MB]


def test_a_download_one_byte_over_its_limit_is_removed_and_says_so(tmp_path):
    fs, _ = store(_Resp(chunks=[b"a" * MB, b"b" * (MB + 1)]))
    dest = tmp_path / "f.bin"
    with pytest.raises(O.FileTooBig, match="Files/f.bin is over 2 MB, so it wasn't downloaded"):
        fs.download("ws", "lh", "Files/f.bin", dest, max_bytes=2 * MB)
    assert not dest.exists()


def test_a_download_answered_400_writes_nothing(tmp_path):
    fs, _ = store(_Resp(400))
    with pytest.raises(requests.HTTPError, match="read Files/f.bin"):
        fs.download("ws", "lh", "Files/f.bin", tmp_path / "f.bin")
    assert not (tmp_path / "f.bin").exists()


def test_a_ranged_read_is_cut_to_its_length_and_waits_a_minute():
    fs, s = store(_Resp(content=b"0123456789"))
    assert fs.read_bytes("ws", "lh", "Files/x", length=4) == b"0123"
    assert s.calls[0][2]["headers"]["Range"] == "bytes=0-3" and s.calls[0][2]["timeout"] == 60
    assert fs.read_bytes("ws", "lh", "Files/x") == b"0123456789"


def test_a_read_answered_400_is_an_error():
    fs, _ = store(_Resp(400))
    with pytest.raises(requests.HTTPError, match="read Files/x"):
        fs.read_bytes("ws", "lh", "Files/x")


def test_an_upload_step_answered_400_stops_it_and_each_step_waits_five_minutes():
    fs, s = store(_Resp(201), _Resp(400), _Resp(200))
    with pytest.raises(requests.HTTPError, match="write Files/x.csv"):
        fs.upload("ws", "lh", "Files/x.csv", b"data")
    assert [c[2]["timeout"] for c in s.calls[:2]] == [300, 300]
    assert s.calls[2][0] == "DELETE"  # the half-made new file is removed


def test_mkdir_accepts_an_existing_folder_and_refuses_a_400():
    fs, s = store(_Resp(409))
    fs.mkdir("ws", "lh", "Files/a")
    assert s.calls[0][2]["timeout"] == 60
    fs, _ = store(_Resp(400))
    with pytest.raises(requests.HTTPError, match="create the folder Files/a"):
        fs.mkdir("ws", "lh", "Files/a")


def test_delete_accepts_a_missing_file_and_refuses_a_400():
    fs, _ = store(_Resp(404))
    fs.delete("ws", "lh", "Files/a")
    fs, _ = store(_Resp(400))
    with pytest.raises(requests.HTTPError, match="delete Files/a"):
        fs.delete("ws", "lh", "Files/a")
