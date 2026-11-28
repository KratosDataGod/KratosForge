"""Mutation batch 73: the Workspaces page previews (preview_service.py): where a previewed file
goes on this PC, which files are refused, and what each preview call passes on. Nothing is read
from or written to Fabric: the store, the lakehouse and the definition are stand-ins."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from fabric_desktop import preview, preview_service
from fabric_desktop.preview_service import PreviewMixin, preview_dest

REF = {"workspace_id": "ws-1", "lakehouse_id": "lh-1"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.mark.parametrize("path, name", [
    ("Files/a.csv", "preview.csv"),
    ("Files/a.CSV", "preview.CSV"),
    ("Files/a.parquet", "preview.parquet"),
    ("Files/a.abcdefghij", "preview.abcdefghij"),     # ten characters: kept
    ("Files/a.abcdefghijk", "preview"),               # eleven: dropped
    ("Files/a.", "preview"),
    ("Files/a.c-v", "preview"),
    ("Files/a.csv ", "preview"),                      # a trailing space isn't part of a kept extension
    ("Files/a.cs\nv", "preview"),
])
def test_the_extension_is_kept_only_when_it_is_one_to_ten_letters_or_digits(path, name):
    folder = PurePosixPath("/data/preview-files/abc")
    assert preview_dest(folder, path) == folder / name


def test_a_name_that_would_leave_the_folder_is_refused(monkeypatch):
    # The fixed name can't do it on its own; this pins the check behind it.
    class Odd(type(PurePosixPath())):
        def __truediv__(self, other):
            return PurePosixPath("/elsewhere") / other

    with pytest.raises(ValueError, match=r"^Files/x\.csv can't be previewed$"):
        preview_dest(Odd("/data/preview-files/abc"), "Files/x.csv")


class _Store:
    calls: list = []

    def __init__(self, token):
        self.token = token

    def download(self, ws, lh, path, dest, max_bytes=None):
        _Store.calls.append({"ws": ws, "lh": lh, "path": path, "dest": dest, "max": max_bytes, "token": self.token()})
        dest.parent.mkdir(parents=True, exist_ok=True)   # as the real store does
        dest.write_text("a,b\n1,2\n", encoding="utf-8")
        return dest


def _svc(tmp_path, monkeypatch, store=_Store):
    _Store.calls = []
    monkeypatch.setattr(preview_service, "FileStore", store)
    seen = {}

    def file_preview(dest, path, limit):
        seen.update(dest=dest, path=path, limit=limit, existed=dest.exists())
        return {"kind": "table", "limit": limit}

    monkeypatch.setattr(preview, "file_preview", file_preview)

    class Svc(PreviewMixin):
        root = str(tmp_path)

        class auth:
            storage_token = staticmethod(lambda: "tok")

    return Svc(), seen


def test_a_file_is_downloaded_with_its_ids_path_token_and_limit_then_read_with_the_limit(tmp_path, monkeypatch):
    svc, seen = _svc(tmp_path, monkeypatch)
    out = svc.preview_file(REF, "Files/raw/a.csv", size=8, limit=7)
    assert out == {"kind": "table", "limit": 7}
    (call,) = _Store.calls
    assert (call["ws"], call["lh"], call["path"], call["token"]) == ("ws-1", "lh-1", "Files/raw/a.csv", "tok")
    assert call["max"] == preview.MAX_FILE_BYTES
    assert seen["dest"] == call["dest"] and seen["path"] == "Files/raw/a.csv" and seen["existed"]
    assert call["dest"].parent.parent == Path(tmp_path) / "preview-files"
    assert len(call["dest"].parent.name) == 32
    assert not call["dest"].parent.exists()


def test_the_default_row_limit_is_the_preview_one(tmp_path, monkeypatch):
    svc, seen = _svc(tmp_path, monkeypatch)
    svc.preview_file(REF, "Files/a.csv")
    assert seen["limit"] == preview.ROWS


def test_a_file_at_the_size_limit_is_read_and_one_over_is_not(tmp_path, monkeypatch):
    svc, seen = _svc(tmp_path, monkeypatch)
    assert svc.preview_file(REF, "Files/a.csv", size=preview.MAX_FILE_BYTES)["kind"] == "table"
    assert svc.preview_file(REF, "Files/a.csv", size=preview.MAX_FILE_BYTES + 1) == {
        "kind": "too_big", "size": preview.MAX_FILE_BYTES + 1}
    assert len(_Store.calls) == 1


def test_a_kind_that_cant_be_previewed_is_said_before_its_size_or_path_is_looked_at(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path, monkeypatch)
    assert svc.preview_file(REF, "../../x.png", size=preview.MAX_FILE_BYTES + 5) == {"kind": "other"}
    assert _Store.calls == []


@pytest.mark.parametrize("path", ["Tables/a.csv", "files/a.csv", "Files.csv", "/Files/a.csv", "Files/../a.csv",
                                  "Files/raw/../../a.csv", "Files/../raw/a.csv"])
def test_a_path_outside_files_or_with_a_parent_step_is_refused(tmp_path, monkeypatch, path):
    svc, _ = _svc(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="is not a file under Files/"):
        svc.preview_file(REF, path)
    assert _Store.calls == []


def test_dots_that_are_part_of_a_name_are_allowed(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path, monkeypatch)
    assert svc.preview_file(REF, "Files/..raw/a..b.csv")["kind"] == "table"
    assert svc.preview_file(REF, "Files/.../a.csv")["kind"] == "table"


def test_a_download_stopped_at_the_limit_says_too_big_with_the_given_size_or_one_over(tmp_path, monkeypatch):
    class TooBig(_Store):
        def download(self, ws, lh, path, dest, max_bytes=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("partial", encoding="utf-8")
            raise preview_service.FileTooBig(path, max_bytes or 0)

    svc, _ = _svc(tmp_path, monkeypatch, TooBig)
    assert svc.preview_file(REF, "Files/a.csv") == {"kind": "too_big", "size": preview.MAX_FILE_BYTES + 1}
    assert svc.preview_file(REF, "Files/a.csv", size=10) == {"kind": "too_big", "size": 10}
    assert list((Path(tmp_path) / "preview-files").iterdir()) == []


def test_the_folder_goes_even_when_reading_fails(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path, monkeypatch)

    def broken(dest, path, limit):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(preview, "file_preview", broken)
    with pytest.raises(RuntimeError, match="unreadable"):
        svc.preview_file(REF, "Files/a.csv")
    assert list((Path(tmp_path) / "preview-files").iterdir()) == []


def test_each_preview_gets_a_folder_of_its_own(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path, monkeypatch)
    svc.preview_file(REF, "Files/a.csv")
    svc.preview_file(REF, "Files/a.csv")
    a, b = (c["dest"].parent for c in _Store.calls)
    assert a != b


def test_a_table_preview_attaches_the_lakehouse_then_reads_the_table_with_the_limit(monkeypatch):
    order = []

    class Svc(PreviewMixin):
        def _attach(self, ref):
            order.append(("attach", ref))

    monkeypatch.setattr(preview_service.lakehouse, "open_table", lambda t: order.append(("open", t)) or "dt")
    monkeypatch.setattr(preview, "table_preview", lambda dt, limit: {"dt": dt, "limit": limit})
    assert Svc().preview_table(REF, "sales", limit=3) == {"dt": "dt", "limit": 3}
    assert order == [("attach", REF), ("open", "sales")]
    assert Svc().preview_table(REF, "sales")["limit"] == preview.ROWS


def test_a_notebook_preview_asks_for_the_ipynb_definition(monkeypatch):
    asked = []

    class Svc(PreviewMixin):
        class fabric:
            @staticmethod
            def get_item_definition(ws, item, fmt):
                asked.append((ws, item, fmt))
                return {"def": 1}

    monkeypatch.setattr(preview, "notebook_cells", lambda d: {"cells": d})
    assert Svc().preview_notebook("ws-1", "nb-1") == {"cells": {"def": 1}}
    assert asked == [("ws-1", "nb-1", "ipynb")]


def test_a_download_that_fails_before_making_its_folder_still_says_why(tmp_path, monkeypatch):
    class Offline(_Store):
        def download(self, ws, lh, path, dest, max_bytes=None):
            raise ConnectionError("no network")

    svc, _ = _svc(tmp_path, monkeypatch, Offline)
    with pytest.raises(ConnectionError, match="no network"):
        svc.preview_file(REF, "Files/a.csv")

    class TooBigAtOnce(_Store):
        def download(self, ws, lh, path, dest, max_bytes=None):
            raise preview_service.FileTooBig(path, max_bytes or 0)

    svc, _ = _svc(tmp_path, monkeypatch, TooBigAtOnce)
    assert svc.preview_file(REF, "Files/a.csv", size=3) == {"kind": "too_big", "size": 3}
