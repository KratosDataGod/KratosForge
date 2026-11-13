"""Replay bundles (replay.py), the paths the main tests don't reach: frozen cells, files that changed
after a cell read them, a write that fails half way, bundles that aren't bundles or name files outside
their samples folder, and each way a replay can end. Pure functions and local zip files."""

from __future__ import annotations

import json
import zipfile

import pyarrow as pa
import pytest

from fabric_desktop import replay


# ---- what a bundle holds


def test_a_frozen_cell_is_kept_frozen_and_not_counted():
    cells = [{"type": "code", "source": "x = 1", "meta": {"frozen": True}},
             {"type": "code", "source": "y = 2", "count": 1}]
    out, notes = replay.code(cells, 1)
    assert out[0] == {"type": "code", "source": "x = 1", "frozen": True} and notes == []


def test_a_failed_cell_without_an_error_output_has_no_error():
    assert replay.error([{"outputs": [{"type": "stream", "text": "hi"}]}], 0) is None


def test_a_file_that_changed_after_it_was_read_gets_no_hash(tmp_path):
    f = tmp_path / "in.csv"
    f.write_text("a\n1\n")
    st = f.stat()
    reads = {"files": {"a": {"path": "Files/in.csv", "local": str(f), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns},
                       "b": {"path": "Files/gone.csv", "local": str(tmp_path / "gone.csv"), "bytes": 1, "mtime_ns": 1}}}
    got, notes = replay.inputs(reads)
    by_path = {r["path"]: r for r in got["files"]}
    assert len(by_path["Files/in.csv"]["sha256"]) == 64 and by_path["Files/gone.csv"]["sha256"] is None
    assert notes == ["Files/gone.csv changed on this PC after a cell read it, so its SHA-256 isn't recorded."]


# ---- writing and reading bundle files


def _bundle(**extra):
    return {"format": replay.FORMAT, "version": replay.VERSION, "cells": [], "failed_cell": 1,
            "inputs": {"tables": [], "files": []}, "samples": [], **extra}


def test_a_write_that_fails_removes_the_file_it_made(tmp_path):
    path = tmp_path / "b.zip"
    with pytest.raises(AttributeError):
        replay.write(path, _bundle(), {"dbo.t": "not a table"}, mode="x")
    assert not path.exists()


def _zip(path, bundle, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("bundle.json", bundle if isinstance(bundle, str) else json.dumps(bundle))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


@pytest.mark.parametrize("bundle, extra, says", [
    ("{not json", None, "isn't a replay bundle"),
    (_bundle(version=replay.VERSION + 1), None, "was saved by a newer KratosForge"),
    (_bundle(samples=[{"table": "dbo.t", "file": "../evil.parquet"}]), None, "isn't in the bundle's samples folder"),
    (_bundle(samples=[{"table": "dbo.t", "file": "samples/1.parquet"}]), None, "isn't in the bundle's samples folder"),
    (_bundle(inputs={"tables": [{"table": "../../x"}], "files": []}), None, "isn't a replay bundle: "),
])
def test_a_file_that_isnt_a_usable_bundle_is_refused(tmp_path, bundle, extra, says):
    with pytest.raises(ValueError, match=says):
        replay.read(_zip(tmp_path / "b.zip", bundle, extra))


def test_bundle_json_and_samples_too_large_are_refused(tmp_path, monkeypatch):
    path = _zip(tmp_path / "b.zip", _bundle(samples=[{"table": "dbo.t", "file": "samples/1.parquet"}]),
                {"samples/1.parquet": b"x" * 100})
    monkeypatch.setattr(replay, "MAX_SAMPLE_FILE", 10)
    with pytest.raises(ValueError, match="the sample file samples/1.parquet is too large"):
        replay.read(path)
    monkeypatch.setattr(replay, "MAX_BUNDLE_JSON", 10)
    with pytest.raises(ValueError, match="is too large to be a replay bundle"):
        replay.read(path)


def test_a_bundle_round_trips_with_its_samples(tmp_path):
    bundle = _bundle(inputs={"tables": [{"table": "dbo.t", "version": 3}], "files": []})
    out = replay.write(tmp_path / "b.zip", bundle, {"dbo.t": pa.table({"a": [1, 2]})})
    assert out["samples"] == [{"table": "dbo.t", "file": "samples/1.parquet", "rows": 2}]
    assert replay.read(tmp_path / "b.zip")["samples"] == out["samples"]


# ---- opening a bundle


def test_cells_keep_their_parameter_tag_and_frozen_flag():
    cells = replay.notebook_cells({"cells": [{"type": "code", "source": "a = 1", "parameters": True, "frozen": True}]})
    assert cells == [{"type": "code", "source": "a = 1", "meta": {"tags": ["parameters"], "frozen": True}}]


def test_tables_from_another_lakehouse_or_without_a_version_arent_pinned():
    bundle = {"inputs": {"tables": [{"table": "dbo.made", "created": True},
                                    {"table": "dbo.far", "lakehouse_id": "other", "lakehouse": "Far", "version": 1},
                                    {"table": "dbo.latest", "lakehouse_id": "mine"},
                                    {"table": "dbo.pinned", "lakehouse_id": "mine", "version": 2}]}}
    pin, notes = replay.tables_to_pin(bundle, "mine")
    assert [t["table"] for t in pin] == ["dbo.pinned"]
    assert notes == ["dbo.far was read from another lakehouse (Far), so this replay can't read it."]


# ---- how a replay ended


def _err(ename, evalue):
    return [{"type": "error", "ename": ename, "evalue": evalue}]


BUNDLE = {"failed_cell": 3, "error": {"ename": "KeyError", "evalue": "'region'"}}


@pytest.mark.parametrize("stopped_at, status, outputs, verdict", [
    (2, "ok", [], "stopped"),
    (3, "ok", [], "not_reproduced"),
    (2, "error", _err("ValueError", "bad"), "failed_earlier"),
    (3, "error", _err("KeyError", "'region'"), "reproduced"),
    (3, "error", _err("KeyError", "'city'"), "same_kind"),
    (3, "error", _err("TypeError", "no"), "different"),
])
def test_each_way_a_replay_can_end(stopped_at, status, outputs, verdict):
    assert replay.outcome(BUNDLE, stopped_at, status, outputs)["verdict"] == verdict


def test_files_read_differently_in_the_replay_are_named():
    bundle = {"inputs": {"files": [{"path": "Files/a.csv", "sha256": "aaa"}, {"path": "Files/b.csv", "sha256": None}]}}
    log = {"files": {"Files/a.csv": {"sha256": "bbb"}, "Files/b.csv": {"sha256": "ccc"}, "Files/new.csv": {}}}
    assert replay.input_differences(bundle, log, {"dbo.t": "latest"}) == [
        "dbo.t was read at its latest version, not at the bundle's version: not the same input.",
        "Files/a.csv is different now from the file the bundle's run read (SHA-256 differs).",
        "Files/b.csv has no SHA-256 in the bundle, so it can't be checked.",
        "Files/new.csv wasn't read in the bundle's run.",
    ]
