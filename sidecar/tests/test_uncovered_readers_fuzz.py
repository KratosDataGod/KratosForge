"""Fuzz the readers of files someone hands the app that no other fuzz test covers: a backup zip
(backup.validate and backup.restore), a failure replay bundle (replay.read and what a replay then
reads from it), and a table diagram the SQL page asks to save (Service.sql_diagram_export).

Each case is a seeded mutation of a valid file (stdlib ``random``, reproducible from its seed): a
manifest or bundle.json field of the wrong kind, missing or extra; member names that leave the app's
folders or collide; JSON nested too deep; zip bytes damaged. Every case must be read correctly or
refused by name (BackupError, ValueError), never raise anything else, and a restore must never write
outside the folders it restores into. The fixed cases first reproduce what long runs found."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import random
import time
import zipfile
from pathlib import Path

import pytest

from fabric_desktop import backup, replay
from fabric_desktop.service import Service

from tests.settings_fuzz import ODD_VALUES, mutations

CASES = 400
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _zip(path: Path, members: list[tuple[str, bytes | str]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)
    return path


def _manifest(files: list[tuple[str, bytes]], /, **extra) -> str:
    entries = [{"path": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files]
    return json.dumps({"schema": backup.SCHEMA, "created": "2026-01-01T00:00:00+00:00", "files": entries,
                       "cleaned": [], "not_included": []} | extra)


def _backup(path: Path, files: list[tuple[str, bytes]], /, **extra) -> Path:
    return _zip(path, [("manifest.json", _manifest(files, **extra)), *files])


def _damaged_deflate(path: Path, name: str, body: bytes) -> Path:
    _zip(path, [(name, body)])
    raw = bytearray(path.read_bytes())
    for i in range(30 + len(name), 30 + len(name) + 20):  # the deflate data, after the local header
        raw[i] ^= 0x5A
    path.write_bytes(bytes(raw))
    return path


def _unsupported_version(path: Path, name: str, body: bytes) -> Path:
    _zip(path, [(name, body)])
    raw = bytearray(path.read_bytes())
    cd = raw.rindex(b"PK\x01\x02")
    raw[cd + 6] = 99  # "version needed to extract" 9.9 in the central directory
    path.write_bytes(bytes(raw))
    return path


# ---- backup: what long runs found

def test_a_name_with_a_double_slash_is_refused_and_nothing_is_written_outside(tmp_path):
    """A name with an empty part ("app//...") is refused: restore checks every entry stays inside the restore folder."""
    outside = tmp_path / "outside" / "other.txt"
    name = f"app/{outside.as_posix()}"
    assert "//" in name
    zp = _backup(tmp_path / "b.zip", [(name, b"x")])
    _manifest_, problems = backup.validate(zp)
    assert any("would be written outside the app's folders" in p for p in problems)
    data = tmp_path / "data"
    data.mkdir()
    with pytest.raises(backup.BackupError):
        backup.restore(zp, data, apply=True)
    assert not outside.exists() and not outside.with_name("other.txt.restoring").exists()


@pytest.mark.parametrize("name", ["app//x", "app/./x", "app/x/", "agent//x"])
def test_names_that_arent_written_as_one_path_are_never_safe(name):
    assert not backup._safe(name)


@pytest.mark.parametrize("name", [
    "app/MSAL_CACHE.BIN", "app/Link-Port", "app/EBWEBVIEW/x", "app/Logs/a.json", "app/x.LOG", "app/db.sqlite3-WAL",  # case
    "app/msal_cache.bin.", "app/x.log.", "app/settings.json ", "app/sub./x.json",  # a trailing dot or space
    "app/NUL", "app/con.txt", "app/COM1.json", "app/sub/aux/x.json", "agent/LPT1",  # a device name
    "app/MSAL_C~1.BIN", "app/PROJEC~1/x.json",  # an 8.3 short name
])
def test_restore_refuses_names_windows_would_read_as_another_file(tmp_path, name):
    assert not backup._safe(name)
    _manifest_, problems = backup.validate(_backup(tmp_path / "b.zip", [(name, b"x")]))
    assert any("is never restored" in p for p in problems)


@pytest.mark.parametrize("name", ["app/settings.json", "app/Projects/x.json", "agent/queue.sqlite3", "app/a.b.json",
                                  "app/con-notes.json", "app/console.json", "app/x~y.json", "app/notes~2.json",
                                  "app/projects/a-long-draft~1.txt"])
def test_ordinary_names_are_still_restored(name):
    assert backup._safe(name)


def test_a_backup_made_here_always_passes_its_own_restore_check(tmp_path):
    """create leaves out what validate would refuse (here a name in 8.3 shape) and says so, so the two agree."""
    data = tmp_path / "data"
    for rel in ("settings.json", "projects/draft~1.txt", "projects/notes~2.json", "projects/x~y.json"):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text("{}", encoding="utf-8")
    made = backup.create(tmp_path / "out", data)
    manifest, problems = backup.validate(Path(made["path"]))
    assert problems == []
    listed = {e["path"] for e in manifest["files"]}
    assert {"app/settings.json", "app/projects/notes~2.json", "app/projects/x~y.json"} <= listed
    assert "app/projects/draft~1.txt" not in listed
    assert {"path": "app/projects/draft~1.txt", "reason": "its name looks like a Windows short name (such as draft~1.txt), so rename the file to back it up"} in \
        manifest["not_included"]


def test_a_manifest_larger_than_any_backup_writes_is_refused_before_it_is_read(tmp_path):
    """Restore checks the manifest is a sensible size before reading it."""
    zp = tmp_path / "large-manifest.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        with z.open("manifest.json", "w", force_zip64=True) as f:
            f.write(b'{"schema": "' + backup.SCHEMA.encode() + b'", "pad": "')
            block = b" " * (1 << 20)
            for _ in range(backup.MAX_MANIFEST // len(block) + 1):
                f.write(block)
            f.write(b'"}')
    assert zp.stat().st_size < 1_000_000
    manifest, problems = backup.validate(zp)
    assert manifest == {} and len(problems) == 1
    assert problems[0].startswith("Its manifest.json wasn't read, because it's over 64 MB")


def test_a_backup_of_many_files_is_checked_in_linear_time(tmp_path):
    """Duplicate names were found with a list count and a set rebuilt per entry: 8,000 files took 4 s."""
    files = [(f"app/notebooks/n{i}.ipynb", b"") for i in range(12_000)]
    zp = _backup(tmp_path / "b.zip", files)
    start = time.perf_counter()
    manifest, problems = backup.validate(zp)
    assert problems == [] and len(manifest["files"]) == 12_000
    assert time.perf_counter() - start < 4


@pytest.mark.parametrize("extra", [
    {"files": "app/a"}, {"files": [1]}, {"files": [None]}, {"files": 1.5}, {"files": {"path": "app/a"}},
    {"created": None}, {"created": 5}, {"cleaned": 3}, {"cleaned": ["x"]}, {"cleaned": [{"path": "a"}]},
])
def test_a_manifest_of_the_wrong_kind_is_refused_by_name(tmp_path, extra):
    zp = _backup(tmp_path / "b.zip", [], **extra)
    manifest, problems = backup.validate(zp)
    assert manifest == {} and problems[0].startswith("Its manifest.json can't be used, because")
    with pytest.raises(backup.BackupError):
        backup.restore(zp, tmp_path / "data")


def test_a_manifest_without_created_is_refused_not_a_key_error(tmp_path):
    zp = _zip(tmp_path / "b.zip", [("manifest.json", json.dumps({"schema": backup.SCHEMA, "files": []}))])
    with pytest.raises(backup.BackupError, match="manifest.json can't be used"):
        backup.restore(zp, tmp_path / "data")


@pytest.mark.parametrize("make", ["deep", "deflate", "version"])
def test_an_unreadable_manifest_is_refused_by_name(tmp_path, make):
    body = (b"[" * 100_000 + b"]" * 100_000) if make == "deep" else _manifest([]).encode() * 20
    zp = tmp_path / "b.zip"
    if make == "deep":
        _zip(zp, [("manifest.json", body)])
    elif make == "deflate":
        _damaged_deflate(zp, "manifest.json", body)
    else:
        _unsupported_version(zp, "manifest.json", body)
    manifest, problems = backup.validate(zp)
    assert manifest == {} and len(problems) == 1


def test_a_damaged_member_is_a_problem_not_a_crash(tmp_path):
    good = tmp_path / "good.zip"
    body = json.dumps({"k": "v" * 400}).encode()
    _backup(good, [("app/settings.json", body)])
    raw = bytearray(good.read_bytes())
    start = raw.index(b"PK\x03\x04", 1) + 30 + len("app/settings.json")  # the second member's data
    for i in range(start, start + 12):
        raw[i] ^= 0x5A
    (tmp_path / "bad.zip").write_bytes(bytes(raw))
    _manifest_, problems = backup.validate(tmp_path / "bad.zip")
    assert problems and all(isinstance(p, str) for p in problems)


@pytest.mark.parametrize("names", [["app/a", "app/a.restoring"], ["app/a.restoring", "app/a"],
                                   ["app/a", "app/a/b"], ["app/A", "app/a/b"]])
def test_files_that_would_write_over_each_other_are_refused(tmp_path, names):
    """Restore stages app/a as a.restoring: a backup that also holds app/a.restoring wrote one over the
    other, and app/a beside app/a/b raised FileExistsError part way."""
    zp = _backup(tmp_path / "b.zip", [(n, n.encode()) for n in names])
    _manifest_, problems = backup.validate(zp)
    assert any("another file in this backup would be written over it" in p for p in problems)


# ---- backup: seeded mutations

_NAMES = ["app/settings.json", "app/notebooks/a.ipynb", "app/x.db", "agent/queue.sqlite3", "app/a",
          "app/a.restoring", "app/sub/b.txt", "app/A.txt", "app/a.txt"]
# No "app//x" here: before the fix it wrote to /x. The double-slash test above aims it inside tmp_path.
_ODD_NAMES = ["../x", "app/../../x", "/etc/x", "app\\x", "C:x", "app/./x", "app/x/", "app/a/b",
              "app/x.db-wal", "app/link-port", "app/logs/x", "agent/x", "app", ""]


def _backup_case(rnd: random.Random) -> bytes:
    files = [(n, rnd.choice([b"", b"text", backup.SQLITE_HEADER + bytes(64), os.urandom(rnd.randint(0, 99))]))
             for n in rnd.sample(_NAMES, rnd.randint(0, len(_NAMES)))]
    manifest = json.loads(_manifest(files))
    kind = rnd.randrange(6)
    if kind == 0 and manifest["files"]:
        entry = rnd.choice(manifest["files"])
        entry[rnd.choice(["path", "size", "sha256"])] = copy.deepcopy(rnd.choice(ODD_VALUES))
    elif kind == 1:
        manifest[rnd.choice(["schema", "created", "files", "cleaned"])] = copy.deepcopy(rnd.choice(ODD_VALUES))
    elif kind == 2:
        manifest.pop(rnd.choice(list(manifest)))
    elif kind == 3:
        name = rnd.choice(_ODD_NAMES)
        files.append((name, b"q"))
        manifest["files"].append({"path": name, "size": 1, "sha256": hashlib.sha256(b"q").hexdigest()})
    raw = json.dumps(manifest).encode()
    if kind == 4:
        raw = rnd.choice([body for _label, body in mutations(manifest, rnd.randrange(1 << 30), 30)])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", raw)
        for name, data in files:
            if name:
                z.writestr(name, data)
    data = bytearray(buf.getvalue())
    if kind == 5 and data:
        for _ in range(rnd.randint(1, 6)):
            data[rnd.randrange(len(data))] = rnd.randrange(256)
    return bytes(data)


def test_mutated_backups_restore_or_are_refused_and_never_write_outside(tmp_path):
    for seed in range(CASES):
        case = tmp_path / str(seed)
        data, agent = case / "data", case / "agent"
        data.mkdir(parents=True)
        agent.mkdir()
        zp = case / "b.zip"
        zp.write_bytes(_backup_case(random.Random(seed)))
        try:
            manifest, problems = backup.validate(zp)
            if not problems:
                backup.describe(backup.restore(zp, data, agent, apply=True))
                for entry in manifest["files"]:
                    top, rest = entry["path"].split("/", 1)
                    written = (agent if top == "agent" else data) / rest
                    assert hashlib.sha256(written.read_bytes()).hexdigest() == entry["sha256"], seed
        except backup.BackupError:
            pass
        for p in case.rglob("*"):
            assert p == zp or data in p.parents or agent in p.parents or p in (data, agent), (seed, p)


# ---- replay bundles

def _bundle(**kw):
    return {"format": replay.FORMAT, "version": 1, "notebook": "nb", "saved_at": "2026-01-01T00:00:00Z",
            "failed_cell": 2, "cells": [{"type": "code", "source": "x = 1"}, {"type": "code", "source": "1/0"}],
            "error": {"ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": ""},
            "inputs": {"tables": [{"table": "dbo.t", "version": 3, "lakehouse_id": "L"}],
                       "files": [{"path": "Files/a.csv", "sha256": "ab"}]},
            "environment": {}, "ids_included": True, "lakehouse": {"name": "lh", "lakehouse_id": "L"},
            "samples": [{"table": "lh/dbo.t", "file": "samples/1.parquet", "rows": 1}], "limitations": []} | kw


def _bundle_zip(path: Path, body: bytes | str) -> Path:
    return _zip(path, [("bundle.json", body), ("samples/1.parquet", b"PAR1")])


def _use(b: dict) -> None:
    """What prove_service does with a bundle replay.read returned."""
    replay.notebook_cells(b)
    replay.tables_to_pin(b, None)
    replay.sample_tables(b)
    replay.outcome(b, 1, "error", [])
    replay.input_differences(b, {"files": {"Files/a.csv": {"sha256": "cd"}}}, {})
    (b.get("lakehouse") or {}).get("lakehouse_id")


def test_a_good_bundle_opens_and_is_usable(tmp_path):
    _use(replay.read(_bundle_zip(tmp_path / "b.zip", json.dumps(_bundle()))))


@pytest.mark.parametrize("version", ["x", "1", [1], {"a": 1}, None, 1.5, True])
def test_a_version_of_the_wrong_kind_is_refused_by_name(tmp_path, version):
    """int(version) raised TypeError for a list, dict or null, which broke the bundle list in Downloads."""
    with pytest.raises(ValueError, match=r"^b\.zip isn't a replay bundle\.$"):
        replay.read(_bundle_zip(tmp_path / "b.zip", json.dumps(_bundle(version=version))))


def test_an_infinite_version_is_refused_by_name(tmp_path):
    body = json.dumps(_bundle()).replace('"version": 1', '"version": Infinity')
    with pytest.raises(ValueError, match="isn't a replay bundle"):
        replay.read(_bundle_zip(tmp_path / "b.zip", body))


@pytest.mark.parametrize("make", ["deep", "deflate", "version"])
def test_an_unreadable_bundle_json_is_refused_by_name(tmp_path, make):
    body = (b"[" * 100_000 + b"]" * 100_000) if make == "deep" else json.dumps(_bundle()).encode() * 4
    zp = tmp_path / "b.zip"
    if make == "deep":
        _zip(zp, [("bundle.json", body)])
    elif make == "deflate":
        _damaged_deflate(zp, "bundle.json", body)
    else:
        _unsupported_version(zp, "bundle.json", body)
    with pytest.raises(ValueError, match=r"^b\.zip isn't a replay bundle\.$"):
        replay.read(zp)


@pytest.mark.parametrize("change", [
    {"cells": None}, {"cells": "x"}, {"cells": [1]}, {"cells": [{"type": "code"}]}, {"cells": [{"source": "x"}]},
    {"failed_cell": None}, {"failed_cell": "2"}, {"failed_cell": 0}, {"failed_cell": True},
    {"inputs": None}, {"inputs": []}, {"inputs": {"tables": []}}, {"inputs": {"tables": [], "files": [1]}},
    {"inputs": {"tables": [], "files": [{"sha256": "x"}]}}, {"lakehouse": "lh"}, {"error": [1]},
    {"environment": "x"}, {"samples": "x"},
])
def test_a_bundle_a_replay_cant_use_is_refused_when_opened(tmp_path, change):
    """These opened, then broke the replay with a KeyError or TypeError when its notebook was made."""
    with pytest.raises(ValueError, match=r"^b\.zip (can't be opened as a|isn't a) replay bundle"):
        replay.read(_bundle_zip(tmp_path / "b.zip", json.dumps(_bundle(**change))))


def _deep_mutate(rnd: random.Random, value):
    if isinstance(value, dict) and value and rnd.random() < 0.7:
        key = rnd.choice(list(value))
        if rnd.random() < 0.2:
            del value[key]
        else:
            value[key] = _deep_mutate(rnd, value[key])
        return value
    if isinstance(value, list) and value and rnd.random() < 0.7:
        i = rnd.randrange(len(value))
        value[i] = _deep_mutate(rnd, value[i])
        return value
    return copy.deepcopy(rnd.choice(ODD_VALUES + ["../x", "samples/1.parquet", "dbo.t"]))


def test_mutated_bundles_open_usable_or_are_refused_by_name(tmp_path):
    for seed in range(CASES * 2):
        rnd = random.Random(seed)
        if rnd.random() < 0.8:
            b = _deep_mutate(rnd, _bundle())
            if rnd.random() < 0.4:
                b = _deep_mutate(rnd, b)
            body = json.dumps(b).encode()
        else:
            body = rnd.choice([raw for _label, raw in mutations(_bundle(), seed, 30)])
        zp = _bundle_zip(tmp_path / f"{seed}.zip", body)
        if rnd.random() < 0.2:
            raw = bytearray(zp.read_bytes())
            for _ in range(rnd.randint(1, 6)):
                raw[rnd.randrange(len(raw))] = rnd.randrange(256)
            zp.write_bytes(bytes(raw))
        try:
            opened = replay.read(zp)
        except ValueError:
            continue
        _use(opened)


# ---- a table diagram from the SQL page

def test_a_diagram_with_a_lone_surrogate_is_refused_by_name():
    """A JSON string may hold "\\ud800"; svg.encode raised UnicodeEncodeError before the size check."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>\ud800</text></svg>'
    with pytest.raises(ValueError, match="because it holds a character that can't be written to a file"):
        Service.sql_diagram_export(Service, svg)  # type: ignore[arg-type]
