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
