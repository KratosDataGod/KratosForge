"""Fuzz more readers of files someone hands the app, or that the app writes for someone to open: the samples
a replay seeds its sandbox from (prove_service._replay_seed), a backup restored over a folder that isn't
laid out as the backup expects (backup.restore), CSV cells a spreadsheet would run (csv_cells and the
exports through it), and the support bundle the smoke test checks for anything personal (smoketest).

Each case is seeded (stdlib ``random``, reproducible from its seed). Every case must be read correctly or
refused by name (ValueError, BackupError), never raise anything else, and every write stays in tmp_path.
The fixed cases first reproduce what long runs found."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import pytest

from fabric_desktop import app_settings, backup, csv_cells, replay, smoketest, support_bundle
from fabric_desktop import prove_service as ps
from fabric_desktop.frames import _csv_ready
from fabric_desktop.sql_exec import csv_ready_batch, csv_ready_schema

CASES = 200


# ---- replay samples (prove_service._replay_seed)

class _Seeder:
    """Just what _replay_seed needs from the service: the app's copy of the bundle."""

    def __init__(self, path: Path):
        self.path = path

    def _replay_copy(self, info, name=""):
        return self.path


def _parquet(rows: int = 3, **kw) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.table({"a": list(range(rows))}), buf, **kw)
    return buf.getvalue()


def _replay_bundle(path: Path, samples: list[tuple[object, bytes]]) -> Path:
    bundle = {"format": replay.FORMAT, "version": 1, "notebook": "nb", "failed_cell": 1,
              "cells": [{"type": "code", "source": "1/0"}], "error": {}, "environment": {}, "lakehouse": {},
              "inputs": {"tables": [{"table": "dbo.t", "version": 3}, {"table": "u", "version": 1}], "files": []},
              "samples": [{"table": t, "file": f"samples/{i + 1}.parquet", "rows": 1} for i, (t, _b) in enumerate(samples)]}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bundle.json", json.dumps(bundle))
        for i, (_t, body) in enumerate(samples):
            z.writestr(f"samples/{i + 1}.parquet", body)
    return path


def _seed(tmp_path: Path, zp: Path, tables: list[str]) -> Path:
    root = tmp_path / "lakehouse"
    ps.ProveMixin._replay_seed(_Seeder(zp), {}, "rid", root.as_posix(), lambda: {}, tables)  # type: ignore[arg-type]
    return root


def test_a_good_sample_seeds_the_sandbox(tmp_path):
    root = _seed(tmp_path, _replay_bundle(tmp_path / "b.zip", [("LH/dbo.t", _parquet())]), ["dbo.t"])
    assert (root / "Files/_fabric_desktop/proofs/rid/pc/tables/dbo/t/_delta_log").is_dir()


def test_a_sample_whose_table_isnt_text_is_refused_when_opened(tmp_path):
    """str(5) passed the name check, then the replay broke with AttributeError on 5.split."""
    zp = _replay_bundle(tmp_path / "b.zip", [(5, _parquet())])
    with pytest.raises(ValueError, match="can't be opened as a replay bundle, because its samples aren't a list of tables"):
        replay.read(zp)


def _bad_column_name() -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.table({"zzzz": [1]}), buf, store_schema=False)
    return buf.getvalue().replace(b"zzzz", b"\xa2zzz")  # UnicodeDecodeError from Arrow


@pytest.mark.parametrize("body", [b"", b"PAR1junkPAR1", _parquet()[:-9], _parquet()[:40] + b"\xff" * 30 + _parquet()[70:],
                                  _bad_column_name()])
def test_a_sample_that_isnt_parquet_is_refused_by_name(tmp_path, body):
    """Arrow's own errors reached the person ("Couldn't deserialize thrift"), some as a bare OSError."""
    zp = _replay_bundle(tmp_path / "b.zip", [("dbo.t", body)])
    with pytest.raises(ValueError, match="^The bundle's sample of dbo.t can't be read, because it isn't a Parquet"):
        _seed(tmp_path, zp, ["dbo.t"])


def test_a_sample_of_more_rows_than_a_bundle_holds_is_refused_before_it_is_read(tmp_path, monkeypatch):
    """Sample files are read in small batches with row and byte limits; write never saves more than SAMPLE_ROWS rows."""
    zp = _replay_bundle(tmp_path / "b.zip", [("dbo.t", _parquet(replay.SAMPLE_ROWS + 1, compression="zstd"))])
    monkeypatch.setattr(pq.ParquetFile, "read", lambda *a, **k: pytest.fail("the sample was read"))
    with pytest.raises(ValueError, match="^The bundle's sample of dbo.t wasn't read, because it holds more"):
        _seed(tmp_path, zp, ["dbo.t"])


@pytest.mark.parametrize("tables", [("LH/dbo.t", "dbo.t"), ("u", "dbo.u")])
def test_two_samples_of_one_table_are_refused_by_name(tmp_path, tables):
    """The second write raised deltalake's "A table already exists" part way through seeding."""
    zp = _replay_bundle(tmp_path / "b.zip", [(t, _parquet()) for t in tables])
    with pytest.raises(ValueError, match="holds two samples of"):
        _seed(tmp_path, zp, ["dbo.t", "u", "dbo.u"])


def test_mutated_samples_seed_or_are_refused_by_name(tmp_path):
    for seed in range(CASES):
        rnd = random.Random(seed)
        samples = []
        for table in rnd.sample(["dbo.t", "u", "LH/dbo.t", "dbo.u", "x.y"], rnd.randint(0, 3)):
            body = bytearray(_parquet(rnd.randint(0, 20), compression=rnd.choice(["snappy", "zstd", "none"])))
            kind = rnd.randrange(5)
            if kind == 0:
                for _ in range(rnd.randint(1, 8)):
                    body[rnd.randrange(len(body))] = rnd.randrange(256)
            elif kind == 1:
                body = body[:rnd.randint(0, len(body))]
            elif kind == 2:
                body = bytearray(b"PAR1" + rnd.randbytes(rnd.randint(0, 40)) + b"PAR1")
            samples.append((table, bytes(body)))
        case = tmp_path / str(seed)
        case.mkdir()
        zp = _replay_bundle(case / "b.zip", samples)
        try:
            replay.read(zp)
            _seed(case, zp, rnd.sample(["dbo.t", "u", "dbo.u", "x.y"], rnd.randint(0, 4)))
        except ValueError:
            pass
        assert {p.name for p in case.iterdir()} <= {"b.zip", "lakehouse"}, seed


# ---- backup restore over a folder laid out differently

def _backup_zip(path: Path, files: list[tuple[str, bytes]]) -> Path:
    entries = [{"path": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps({"schema": backup.SCHEMA, "created": "2026-01-01T00:00:00+00:00",
                                                "files": entries, "cleaned": [], "not_included": []}))
        for n, b in files:
            z.writestr(n, b)
    return path


def _files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*")
            if p.is_file() and backup.SAFETY not in p.relative_to(folder).parts}


@pytest.mark.parametrize("layout, name, why", [
    ("folder", "app/notes", "a folder is where the file goes"),
    ("file", "app/sub/b.txt", "a file is where one of its folders goes"),
    ("none", "app/" + "n" * 250, "its path is too long for this PC"),  # fits, but its .restoring doesn't
    ("none", "app/" + "n" * 256, "its path is too long for this PC"),
])
@pytest.mark.parametrize("apply", [True, False])
def test_a_restore_that_cant_write_a_file_is_refused_by_name_and_changes_nothing(tmp_path, layout, name, why, apply):
    """These raised a bare IsADirectoryError, FileExistsError or OSError, the long name even on a preview."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_bytes(b"old")
    if layout == "folder":
        (data / "notes").mkdir()
        (data / "notes" / "keep").write_bytes(b"k")
    elif layout == "file":
        (data / "sub").write_bytes(b"a file")
    before = _files(data)
    zp = _backup_zip(tmp_path / "b.zip", [("app/a.txt", b"new"), (name, b"x")])
    assert backup.validate(zp)[1] == []
    if apply or len(name) > 255:  # a preview writes nothing, so only a name the PC can't even look up stops it
        with pytest.raises(backup.BackupError) as refused:
            backup.restore(zp, data, apply=apply)
        assert str(refused.value).startswith(f"Nothing was restored, because {name} can't be written")
        assert why in str(refused.value)
    else:
        assert not backup.restore(zp, data, apply=apply)["applied"]
    assert _files(data) == before
    assert {p.name for p in tmp_path.iterdir()} == {"data", "b.zip"}


_LOCAL = ["a", "b.db", "sub", "sub/c", "sub/d/e", "n" * 250, "sub/" + "m" * 255, "x.db-wal", "q"]


def test_backups_restored_over_mixed_folders_restore_whole_or_change_nothing(tmp_path):
    for seed in range(CASES // 2):
        rnd = random.Random(seed)
        case = tmp_path / str(seed)
        data = case / "data"
        data.mkdir(parents=True)
        for n in rnd.sample(_LOCAL, rnd.randint(0, 5)):
            p = data / n
            try:
                if rnd.random() < 0.4:
                    p.mkdir(parents=True, exist_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"old")
            except OSError:
                pass
        files = [("app/" + n, backup.SQLITE_HEADER + bytes(20) if n.endswith(".db") else rnd.randbytes(rnd.randint(0, 9)))
                 for n in rnd.sample(_LOCAL, rnd.randint(1, 5))]
        zp = _backup_zip(case / "b.zip", files)
        if backup.validate(zp)[1]:
            continue
        before = _files(data)
        try:
            backup.restore(zp, data, apply=True)
        except backup.BackupError:
            assert _files(data) == before, seed
        else:
            after = _files(data)
            assert all(after[n.split("/", 1)[1]] == body for n, body in files), seed
        assert {p.name for p in case.iterdir()} == {"data", "b.zip"}, seed


# ---- CSV cells a spreadsheet would run

def test_a_binary_cell_in_the_sql_csv_export_is_written_like_a_text_cell(tmp_path, monkeypatch):
    """CSV export treats binary cells like text cells. A list column used to stop the export."""
    from tests.test_notebook import LH, _sql_service
    service, _ = _sql_service(tmp_path, monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    out = service.export_sql_csv("SELECT CAST('=HYPERLINK(1)' AS BLOB) AS b, ['=x'] AS l", LH)
    rows = list(csv.reader(io.StringIO(Path(out["path"]).read_text(encoding="utf-8"))))
    assert rows == [["b", "l"], ["0x3D48595045524C494E4B283129", '["=x"]']]


_ALPHABET = ["=", "+", "-", "@", "\t", "\r", "\n", "'", " ", "a", "1", '"', ",", "é", "|", "%"]
_TYPES = [pa.string(), pa.large_string(), "dict", "dict-large", pa.binary(), pa.large_binary(), pa.int64(), "list"]


def _text(rnd: random.Random) -> str | None:
    return None if rnd.random() < 0.1 else "".join(rnd.choice(_ALPHABET) for _ in range(rnd.randint(0, 6)))


def _column(rnd: random.Random, kind, n: int) -> pa.Array:
    values = [_text(rnd) for _ in range(n)]
    if kind == pa.int64():
        return pa.array([rnd.randint(-9, 9) for _ in range(n)], kind)
    if kind in ("dict", "dict-large"):
        return pa.array(values, pa.string() if kind == "dict" else pa.large_string()).dictionary_encode()
    if kind == "list":
        return pa.array([None if v is None else [v] for v in values])
    if kind in (pa.binary(), pa.large_binary()):
        return pa.array([None if v is None else v.encode() for v in values], kind)
    return pa.array(values, kind)


def _written_cells(data: bytes, numbers: set[int]) -> list[str]:
    """Every cell of a written CSV that a spreadsheet would read as a calculation."""
    risky = []
    for row in csv.reader(io.StringIO(data.decode("utf-8"), newline="")):
        for i, cell in enumerate(row):
            if cell.startswith(csv_cells.STARTS) and i not in numbers:
                risky.append(cell)
    return risky


def test_random_tables_written_as_csv_never_hold_a_cell_that_runs():
    for seed in range(CASES * 2):
        rnd = random.Random(seed)
        n = rnd.randint(0, 5)
        kinds = [rnd.choice(_TYPES) for _ in range(rnd.randint(1, 4))]
        table = pa.table([_column(rnd, k, n) for k in kinds], names=[_text(rnd) or f"c{i}" for i in range(len(kinds))])
        numbers = {i for i, k in enumerate(kinds) if k == pa.int64()}
        ready, _n = csv_cells.protect_table(_csv_ready(table))  # Frames.download
        sink = io.BytesIO()
        pa_csv.write_csv(ready, sink)
        assert _written_cells(sink.getvalue(), numbers) == [], seed
        for batch in table.to_batches():  # the SQL page's export (sql_exec), a batch at a time
            header, _n = csv_cells.protect_header(csv_ready_schema(batch.schema))
            sink = io.BytesIO()
            with pa_csv.CSVWriter(sink, header) as writer:
                writer.write_batch(csv_cells.protect_batch(csv_ready_batch(batch))[0])
            assert _written_cells(sink.getvalue(), numbers) == [], seed


# ---- the support bundle and the smoke test's check of it

@pytest.fixture
def pc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    data = tmp_path / "la" / "FabricDesktop"
    data.mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    return data


@pytest.mark.parametrize("user", ["Zoë Ünal", "王伟", "émile"])
def test_a_user_name_with_accents_in_a_setting_is_taken_out_of_the_support_bundle(pc, user):
    """about.json was written with every non-ASCII letter escaped (\\u00eb), and redaction then took out only
    the part of the name before the first one: "C:\\\\Users\\\\[user]\\u00eb \\u00dcnal"."""
    app_settings.user_path().write_text(json.dumps({"sql_temp_dir": f"C:\\Users\\{user}\\Temp"}), encoding="utf-8")
    about = support_bundle._files(pc, None)["about.json"]
    value = json.loads(about)["settings"]["sql_temp_dir"]["value"]
    assert value == "C:\\Users\\[user]\\Temp"


class _Who:
    def __init__(self, user: str, tenant: str = "22222222-2222-2222-2222-222222222222"):
        self.user, self.tenant = user, tenant

    def signed_in_user(self):
        return self.user

    def signed_in_tenant(self):
        return self.tenant


WS = "11111111-1111-1111-1111-111111111111"


def _check(tmp_path: Path, who: _Who, name: str, body: str) -> bool:
    """Whether the smoke test's flow 10 finds something personal in a bundle holding ``body``."""
    def bundle(out: Path) -> Path:
        with zipfile.ZipFile(out / "b.zip", "w") as z:
            z.writestr(name, body)
        return out / "b.zip"
    test = smoketest.SmokeTest(WS, who, None, tmp_path / "out", support_bundle=bundle, run_id="r")
    try:
        test._bundle()
    except RuntimeError:
        return True
    return False


@pytest.mark.parametrize("user", ["josé@contoso.com", "ZOË.ÜNAL@FABRIKAM.DE", 'ann"o@x.com', "d\\user"])
def test_the_smoke_test_finds_a_user_name_a_json_file_holds_escaped(tmp_path, user):
    """A name with é, a quote or a backslash is escaped in JSON ("jos\\u00e9"), and the check missed it."""
    assert _check(tmp_path, _Who(user.lower()), "about.json", json.dumps({"who": user}))


def test_the_smoke_test_finds_what_a_bundle_holds_in_any_form(tmp_path):
    users = ["chris@contoso.com", "José@contoso.com", "王@contoso.cn", 'ann"o@x.com', "d\\user", "o'brien@x.ie"]
    for seed in range(CASES):
        rnd = random.Random(seed)
        who = _Who(rnd.choice(users))
        leak = rnd.random() < 0.6
        secret = rnd.choice([who.user, who.tenant, WS])
        secret = secret.upper() if rnd.random() < 0.3 else secret
        pad = "x" * rnd.randint(0, 20)
        form = rnd.choice(["log", "json", "json-plain"])
        body = (pad + secret if leak else pad) if form == "log" else json.dumps(
            {"p": pad + secret if leak else pad, "deep": [[{}]] * rnd.randint(0, 3)}, ensure_ascii=form == "json")
        assert _check(tmp_path, who, "sidecar.log" if form == "log" else "about.json", body) == leak, seed

