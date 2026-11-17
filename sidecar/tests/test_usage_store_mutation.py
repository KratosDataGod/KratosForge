"""Mutation-driven checks for usage/store.py (batch 50): imports list newest first, and one
without an import time sorts last instead of breaking the list."""

from __future__ import annotations

import json

from fabric_desktop.usage.store import FOLDER, Store


def _write(root, import_id, imported_at):
    folder = root / FOLDER
    folder.mkdir(exist_ok=True)
    doc = {"id": import_id, "rows": [{"key": import_id, "slot": "s"}]}
    if imported_at is not None:
        doc["imported_at"] = imported_at
    (folder / f"{import_id}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_all_lists_newest_first_and_undated_last(tmp_path):
    _write(tmp_path, "u-" + "a" * 16, "2026-01-01T00:00:00+00:00")
    _write(tmp_path, "u-" + "b" * 16, "2026-03-01T00:00:00+00:00")
    _write(tmp_path, "u-" + "c" * 16, None)
    _write(tmp_path, "u-" + "d" * 16, "2026-02-01T00:00:00+00:00")
    ids = [d["id"][2:3] for d in Store(tmp_path).all()]
    assert ids == ["b", "d", "a", "c"]
