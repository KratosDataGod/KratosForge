"""Workspaces page previews: a table's first rows, a file's start, a notebook's cells."""

from __future__ import annotations

import base64
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import preview
from fabric_desktop.preview_service import PreviewMixin


def test_table_preview_rows_types_and_count(tmp_path):
    uri = str(tmp_path / "sales")
    write_deltalake(uri, pa.table({"region": ["East", "West"], "units": [3, 4]}))
    write_deltalake(uri, pa.table({"region": ["North"], "units": [5]}), mode="append")
    out = preview.table_preview(DeltaTable(uri), limit=2)
    assert [c["name"] for c in out["columns"]] == ["region", "units"]
    assert out["columns"][1]["type"] == "long"
    assert len(out["rows"]) == 2
    assert out["row_count"] == 3
    assert out["version"] == 1


def test_csv_preview_stops_at_limit(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text("id,amount\n" + "".join(f"{i},{i * 1.5}\n" for i in range(500)))
    out = preview.file_preview(path, "Files/orders.csv", limit=10)
    assert out["kind"] == "table"
    assert [c["name"] for c in out["columns"]] == ["id", "amount"]
    assert len(out["rows"]) == 10 and out["rows"][1] == [1, 1.5]


def test_parquet_and_json_preview(tmp_path):
    pq.write_table(pa.table({"a": list(range(300))}), tmp_path / "x.parquet")
    out = preview.file_preview(tmp_path / "x.parquet", "Files/x.parquet", limit=5)
    assert out["row_count"] == 300 and len(out["rows"]) == 5
    (tmp_path / "x.json").write_text(json.dumps([{"k": 1}, {"k": 2}]))
    out = preview.file_preview(tmp_path / "x.json", "Files/x.json")
    assert out["rows"] == [[1], [2]]
    (tmp_path / "y.jsonl").write_text('{"k": 1}\n{"k": float("nan")}\n'.replace('float("nan")', "null"))
    assert preview.file_preview(tmp_path / "y.jsonl", "Files/y.jsonl")["rows"] == [[1], [None]]


def test_text_preview_is_capped(tmp_path):
    (tmp_path / "log.txt").write_text("x" * (preview.TEXT_BYTES + 50))
    out = preview.file_preview(tmp_path / "log.txt", "Files/log.txt")
    assert out["kind"] == "text" and out["truncated"] and len(out["text"]) == preview.TEXT_BYTES


def test_file_kind():
    assert preview.file_kind("Files/a/b.CSV") == "table"
    assert preview.file_kind("Files/readme.md") == "text"
    assert preview.file_kind("Files/photo.png") == "other"
    assert preview.file_kind("Files/noext") == "other"


def test_notebook_cells_from_definition():
    nb = {"metadata": {"kernel_info": {"name": "synapse_pyspark"}},
          "cells": [{"cell_type": "markdown", "source": ["# Title\n", "text"]},
                    {"cell_type": "code", "source": "df = spark.read.table('x')"}]}
    definition = {"definition": {"parts": [
        {"path": ".platform", "payload": base64.b64encode(b"{}").decode()},
        {"path": "notebook-content.ipynb", "payload": base64.b64encode(json.dumps(nb).encode()).decode()}]}}
    out = preview.notebook_cells(definition)
    assert out["language"] == "synapse_pyspark"
    assert out["cells"] == [{"type": "markdown", "source": "# Title\ntext"},
                            {"type": "code", "source": "df = spark.read.table('x')"}]
    with pytest.raises(ValueError):
        preview.notebook_cells({"definition": {"parts": []}})


class FakeService(PreviewMixin):
    root = None

    class fabric:
        @staticmethod
        def get_item_definition(ws, item, fmt):
            nb = {"cells": [{"cell_type": "code", "source": "1"}]}
            return {"definition": {"parts": [{"path": "notebook-content.ipynb",
                                              "payload": base64.b64encode(json.dumps(nb).encode()).decode()}]}}


def test_service_guards_files():
    svc = FakeService()
    ref = {"workspace_id": "w", "lakehouse_id": "l"}
    assert svc.preview_file(ref, "Files/pic.png") == {"kind": "other"}
    assert svc.preview_file(ref, "Files/big.csv", size=preview.MAX_FILE_BYTES + 1)["kind"] == "too_big"
    with pytest.raises(ValueError):
        svc.preview_file(ref, "Files/../../secret.csv")
    assert svc.preview_notebook("w", "nb")["cells"] == [{"type": "code", "source": "1"}]


def test_server_routes_previews():
    from fabric_desktop.server import METHODS
    assert {"preview_table", "preview_file", "preview_notebook"} <= METHODS


# PB-087: a previewed file doesn't stay on this PC, and a file over the limit isn't downloaded whole.
class _Resp:
    status_code = 200

    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield from self.chunks


class _Session:
    def __init__(self, chunks):
        self.chunks, self.read = chunks, 0

    def get(self, url, headers=None, **kwargs):
        return _Resp(self._count())

    def _count(self):
        for c in self.chunks:
            self.read += len(c)
            yield c


def test_download_stops_at_its_limit_and_leaves_nothing(tmp_path):
    from fabric_desktop.onelake import FileStore, FileTooBig
    session = _Session([b"x" * 10] * 100)
    dest = tmp_path / "big.csv"
    with pytest.raises(FileTooBig):
        FileStore(lambda: "t", session=session).download("w", "l", "Files/big.csv", dest, max_bytes=25)
    assert not dest.exists()
    assert session.read <= 30, "it stopped reading at the limit, not after the whole file"
    small = FileStore(lambda: "t", session=_Session([b"a,b\n", b"1,2\n"])).download("w", "l", "Files/s.csv", tmp_path / "s.csv", max_bytes=25)
    assert small.read_bytes() == b"a,b\n1,2\n"


def _preview_service(tmp_path, monkeypatch, chunks):
    from fabric_desktop import preview_service
    real = preview_service.FileStore
    seen = []

    class Store:
        def __init__(self, token):
            pass

        def download(self, ws, lh, path, dest, max_bytes=None):
            seen.append((dest, max_bytes))
            return real(lambda: "t", session=_Session(chunks)).download(ws, lh, path, dest, max_bytes=max_bytes)

    monkeypatch.setattr(preview_service, "FileStore", Store)

    class Svc(PreviewMixin):
        root = tmp_path

        class auth:
            storage_token = staticmethod(lambda: "t")

    return Svc(), seen


def test_a_previewed_file_is_removed_once_read(tmp_path, monkeypatch):
    svc, seen = _preview_service(tmp_path, monkeypatch, [b"region,units\n", b"East,3\n"])
    out = svc.preview_file({"workspace_id": "w", "lakehouse_id": "l"}, "Files/raw/sales.csv", size=20)
    assert out["kind"] == "table" and out["rows"] == [["East", 3]]
    dest, limit = seen[0]
    assert limit == preview.MAX_FILE_BYTES
    assert dest.name == "preview.csv" and tmp_path / "preview-files" in dest.parents
    assert not dest.parent.exists(), "its folder is gone once the preview is read"
    assert not (tmp_path / "files").exists(), "nothing goes in the notebooks' download folder"


@pytest.mark.parametrize("folder_type", ["PurePosixPath", "PureWindowsPath"])
@pytest.mark.parametrize("path, name", [
    ("Files/raw/sales.csv", "preview.csv"),
    ("Files/raw/C:\\Users\\x.csv", "preview.csv"),
    ("Files/raw/\\x.csv", "preview.csv"),
    ("Files/raw/\\\\srv\\share\\x.csv", "preview.csv"),
    ("Files/raw/D:x.csv", "preview.csv"),
    ("Files/raw/odd.c$v", "preview"),
    ("Files/raw/noext", "preview"),
])
def test_the_local_copy_has_a_fixed_name_in_its_own_folder(folder_type, path, name):
    import pathlib
    from fabric_desktop.preview_service import preview_dest
    folder = getattr(pathlib, folder_type)("C:/data/preview-files/abc" if folder_type == "PureWindowsPath" else "/data/preview-files/abc")
    dest = preview_dest(folder, path)
    assert dest.parent == folder and dest.name == name


def test_a_file_with_no_size_given_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "MAX_FILE_BYTES", 15)
    svc, seen = _preview_service(tmp_path, monkeypatch, [b"x" * 10] * 5)
    out = svc.preview_file({"workspace_id": "w", "lakehouse_id": "l"}, "Files/big.csv")
    assert out["kind"] == "too_big"
    assert not seen[0][0].parent.exists()


def test_preview_downloads_left_behind_go_at_the_next_start(tmp_path):
    import os
    from fabric_desktop import retention
    base = tmp_path / "preview-files"
    old, new = base / "old", base / "new"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "a.csv").write_text("x")
    os.utime(old, (1_000, 1_000))
    now = 1_000 + retention.PREVIEW_HOURS * 3600 + 1
    os.utime(new, (now, now))
    for days in (30, 0):  # whatever local_retention_days says, even "keep everything"
        assert retention.sweep(tmp_path, days, dry_run=True, now=now)["removed"] == ["preview-files/old"]
    out = retention.sweep(tmp_path, 0, now=now)
    assert out["removed"] == ["preview-files/old"] and out["bytes"] == 1
    assert not old.exists() and new.exists(), "one another process may be reading now stays"
