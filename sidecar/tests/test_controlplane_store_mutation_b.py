"""controlplane/store at its edges, from the mutation run (batch 471): the OneLake manifest read (a
missing file, an error status at 400, the size cap from the header and the stream, the chunk size),
a replace of a manifest a newer app wrote, the Read record, and the cache's order by name."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.controlplane import manifest as mf
from fabric_desktop.controlplane import store as st
from test_controlplane import LH, WS, StreamingDfs, fabric


class Dfs(StreamingDfs):
    """The streaming fake, recording how each read is asked for and answering with a set status or
    Content-Length when a test gives one."""

    def __init__(self):
        super().__init__()
        self.gets: list[dict] = []
        self.chunks: list[int] = []
        self.status: int | None = None
        self.length: str | None = None

    def get(self, url, headers=None, timeout=None, stream=False):
        self.gets.append({"stream": stream, "headers": dict(headers or {})})
        resp = self._resp(self.status) if self.status else super().get(url, headers, timeout, stream)
        if self.length is not None:
            resp.headers["Content-Length"] = self.length
        real = resp.iter_content

        def iter_content(chunk_size=1, decode_unicode=False):
            self.chunks.append(chunk_size)
            return real(chunk_size, decode_unicode)

        resp.iter_content = iter_content
        return resp


def backend(dfs):
    return st.OneLakeManifestBackend(WS, LH, lambda: "tok", session=dfs)


def test_a_missing_manifest_reads_as_none_and_reads_are_streamed_in_16_kb_chunks():
    dfs = Dfs()
    store = st.onelake_store(WS, LH, lambda: "tok", session=dfs)
    assert store.read() is None
    made = store.create(fabric())
    assert store.read() == made
    assert all(g["stream"] is True for g in dfs.gets) and dfs.chunks == [16 * 1024]


@pytest.mark.parametrize("status", [400, 403])
def test_an_error_status_from_400_is_an_error_not_a_manifest(status):
    dfs = Dfs()
    dfs.status = status
    with pytest.raises(Exception) as got:
        backend(dfs).read(st.NAME)
    assert not isinstance(got.value, mf.ManifestError)
    assert dfs.chunks == []  # the body of an error is never read as a manifest


def test_the_content_length_caps_the_read_before_the_body():
    dfs = Dfs()
    url = backend(dfs)._url(st.NAME)
    dfs.files[url] = (b"{}", '"1"')
    dfs.length = str(mf.MAX_BYTES + 1)
    with pytest.raises(mf.ManifestError, match=f"larger than {mf.MAX_BYTES // 1024} KB, so it isn't one"):
        backend(dfs).read(st.NAME)
    assert dfs.chunks == []
    dfs.length = str(mf.MAX_BYTES)
    dfs.files[url] = (b"x" * mf.MAX_BYTES, '"2"')
    assert len(backend(dfs).read(st.NAME).data) == mf.MAX_BYTES
    dfs.length = "2"
    dfs.files[url] = (b"{}", '"3"')
    assert backend(dfs).read(st.NAME).data == b"{}"


def test_a_manifest_a_newer_app_wrote_is_not_replaced(tmp_path):
    store = st.local_store(tmp_path)
    made = store.create(fabric())
    newer = st.Read(dict(made.manifest, manifest_version=mf.MANIFEST_VERSION + 1), made.etag)
    with pytest.raises(mf.ManifestError, match="read-only here"):
        store.replace(newer, made.manifest)
    assert store.read() == made


def test_a_read_is_frozen():
    r = st.Read({}, '"1"')
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.etag = '"2"'


def test_the_cache_lists_environments_by_name_whatever_their_case(tmp_path):
    cache = st.Cache(tmp_path / "controlplane")
    for name in ("charlie", "Bravo", "alpha"):
        cache.remember(fabric(name=name))
    assert [e["name"] for e in cache.all()] == ["alpha", "Bravo", "charlie"]
