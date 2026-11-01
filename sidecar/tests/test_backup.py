"""Backup and restore: what goes in, what never does, and checks before anything is written."""

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone

import pytest

from fabric_desktop import backup
from fabric_desktop.cli import main

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJwYXQifQ.c2lnbmF0dXJl"


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "FabricDesktop"
    (root / "notebooks").mkdir(parents=True)
    (root / "notebooks" / "sales.ipynb").write_text('{"cells": []}')
    (root / "app-settings.json").write_text(json.dumps({"channel": "preview"}))
    (root / "settings.json").write_text(json.dumps({"teams_webhook": "https://contoso.webhook.office.com/x",
                                                    "cost": {"price": 0.18}}))
    (root / "history.jsonl").write_text(json.dumps({"error": f"401 Bearer {JWT}"}) + "\n")
    (root / "schedules.json").write_text(json.dumps({"engine": "C:/old/python.exe", "schedules": [{"id": "s1"}]}))
    (root / "msal_cache.bin").write_bytes(b"tokens")
    (root / "fabric-view-signin.bin").write_bytes(b"tokens")
    (root / "sidecar.log").write_text("log")
    (root / "engine" / "python").mkdir(parents=True)
    (root / "engine" / "python" / "python.exe").write_bytes(b"exe")
    db = sqlite3.connect(root / "workloads.sqlite")
    db.execute("create table w (name text)")
    db.execute("insert into w values ('orders')")
    db.commit()
    db.close()
    return root


def test_backup_leaves_out_tokens_engine_logs_and_webhooks(app, tmp_path):
    result = backup.create(tmp_path / "out", app, now=NOW)
    with zipfile.ZipFile(result["path"]) as z:
        names = set(z.namelist())
        settings = json.loads(z.read("app/settings.json"))
        history = z.read("app/history.jsonl").decode()
        manifest = json.loads(z.read("manifest.json"))
    assert names == {"manifest.json", "app/notebooks/sales.ipynb", "app/app-settings.json", "app/settings.json",
                     "app/history.jsonl", "app/workloads.sqlite", "app/schedules.json"}
    with zipfile.ZipFile(result["path"]) as z:  # registered again with Task Scheduler at the next start
        assert json.loads(z.read("app/schedules.json")) == {"schedules": [{"id": "s1"}]}
    assert settings == {"cost": {"price": 0.18}}
    assert JWT not in history and "[removed]" in history
    assert {c["path"] for c in manifest["cleaned"]} == {"app/settings.json", "app/history.jsonl"}
    assert {s["path"] for s in manifest["not_included"]} == {"app/msal_cache.bin", "app/fabric-view-signin.bin"}
    assert result["path"].endswith("fabric-desktop-backup-20261017-120000.zip")


def test_restore_previews_then_applies_and_keeps_what_it_replaced(app, tmp_path):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").write_text('{"cells": ["changed"]}')
    (app / "notebooks" / "new.ipynb").write_text("{}")

    preview = backup.restore(zip_path, app)
    assert not preview["applied"]
    assert (app / "notebooks" / "sales.ipynb").read_text() == '{"cells": ["changed"]}'  # a preview writes nothing
    assert not (app / backup.SAFETY).exists()

    done = backup.restore(zip_path, app, apply=True, now=NOW)
    assert done["applied"]
    assert (app / "notebooks" / "sales.ipynb").read_text() == '{"cells": []}'
    assert (app / "notebooks" / "new.ipynb").exists()  # restore never deletes
    kept = app / backup.SAFETY / "20261017-120000" / "app" / "notebooks" / "sales.ipynb"
    assert kept.read_text() == '{"cells": ["changed"]}'
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        assert db.execute("select name from w").fetchall() == [("orders",)]
    assert "set it again" in backup.describe(done)


def _rows(path):
    with closing(sqlite3.connect(path)) as db:
        tables = {r[0] for r in db.execute("select name from sqlite_master where type = 'table'")}
        return db.execute("select count(*) from r").fetchone()[0], tables


def test_restore_moves_a_stale_wal_aside_so_it_isnt_replayed(app, tmp_path):
    runs = app / "runs.sqlite"
    with closing(sqlite3.connect(runs)) as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table r (id int)")
        db.execute("insert into r values (1)")
        db.commit()
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]

    with closing(sqlite3.connect(runs)) as db:  # written after the backup and still only in the WAL
        db.execute("pragma wal_autocheckpoint=0")
        db.executemany("insert into r values (?)", [(i,) for i in range(200)])
        db.execute("create table later (x int)")
        db.commit()
        wal = (app / "runs.sqlite-wal").read_bytes()
    (app / "runs.sqlite-wal").write_bytes(wal)  # as if the app stopped before checkpointing it

    done = backup.restore(zip_path, app, apply=True, now=NOW)
    assert not (app / "runs.sqlite-wal").exists()
    kept = app / backup.SAFETY / "20261017-120000" / "app"
    assert (kept / "runs.sqlite-wal").read_bytes() == wal  # the safety copy is complete, WAL and all
    assert (kept / "runs.sqlite").exists() and done["safety"] == str(kept.parent)
    assert _rows(runs) == (1, {"r"})


def test_restore_refuses_a_database_in_use_and_changes_nothing(app, tmp_path):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").write_text('{"cells": ["changed"]}')
    with closing(sqlite3.connect(app / "workloads.sqlite", isolation_level=None)) as db:
        db.execute("begin exclusive")
        db.execute("insert into w values ('later')")
        with pytest.raises(backup.BackupError, match="Close KratosForge and stop the agent"):
            backup.restore(zip_path, app, apply=True, now=NOW)
        db.execute("commit")
    assert (app / "notebooks" / "sales.ipynb").read_text() == '{"cells": ["changed"]}'
    assert not (app / backup.SAFETY).exists()
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        assert db.execute("select name from w").fetchall() == [("orders",), ("later",)]


def test_restore_refuses_while_an_idle_connection_has_a_wal_database_open(app, tmp_path):
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        db.execute("pragma journal_mode=wal")
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        db.execute("select name from w").fetchall()  # open with no transaction, like a running app
        with pytest.raises(backup.BackupError, match="databases open"):
            backup.restore(zip_path, app, apply=True, now=NOW)
    assert not (app / backup.SAFETY).exists()


def test_restore_checks_each_file_it_writes(app, tmp_path, monkeypatch):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").write_text('{"cells": ["changed"]}')
    real = backup._sha256
    monkeypatch.setattr(backup, "_sha256", lambda p: "0" * 64 if p.name.startswith("sales") else real(p))
    with pytest.raises(backup.BackupError, match="didn't write correctly"):
        backup.restore(zip_path, app, apply=True, now=NOW)
    assert (app / "notebooks" / "sales.ipynb").read_text() == '{"cells": ["changed"]}'
    assert not (app / "notebooks" / "sales.ipynb.restoring").exists()


def _tamper(zip_path, tmp_path, name, data, manifest_entry=None):
    out = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(out, "w") as dst:
        manifest = json.loads(src.read("manifest.json"))
        if manifest_entry:
            manifest["files"].append(manifest_entry)
        for item in src.namelist():
            if item not in ("manifest.json", name):
                dst.writestr(item, src.read(item))
        dst.writestr(name, data)
        dst.writestr("manifest.json", json.dumps(manifest))
    return out


def test_a_changed_or_unsafe_backup_is_refused_before_writing(app, tmp_path):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    changed = _tamper(zip_path, tmp_path, "app/app-settings.json", '{"channel": "stable"}')
    with pytest.raises(backup.BackupError, match="changed since the backup"):
        backup.restore(changed, app, apply=True)
    assert json.loads((app / "app-settings.json").read_text()) == {"channel": "preview"}

    import hashlib
    evil = b"x"
    entry = {"path": "app/../../evil.txt", "size": 1, "sha256": hashlib.sha256(evil).hexdigest()}
    unsafe = _tamper(zip_path, tmp_path, "app/../../evil.txt", evil, entry)
    with pytest.raises(backup.BackupError, match="outside the app's folders"):
        backup.restore(unsafe, app, apply=True)
    entry = {"path": "app/msal_cache.bin", "size": 1, "sha256": hashlib.sha256(evil).hexdigest()}
    tokens = _tamper(zip_path, tmp_path, "app/msal_cache.bin", evil, entry)
    with pytest.raises(backup.BackupError, match="never restored"):
        backup.restore(tokens, app, apply=True)
    assert (app / "msal_cache.bin").read_bytes() == b"tokens"

    (tmp_path / "junk.zip").write_text("not a zip")
    assert "isn't a zip" in backup.validate(tmp_path / "junk.zip")[1][0]


def test_agent_queue_is_included_when_asked(app, tmp_path):
    home = tmp_path / "agent"
    home.mkdir()
    with closing(sqlite3.connect(home / "queue.sqlite3")) as db, db:
        db.execute("create table q (id int)")
    (home / "agent.log").write_text("log")
    zip_path = backup.create(tmp_path / "out", app, home, now=NOW)["path"]
    assert "agent/queue.sqlite3" in zipfile.ZipFile(zip_path).namelist()
    assert all(not f["path"].startswith("agent/") for f in backup.restore(zip_path, app)["files"])  # not asked
    other = tmp_path / "agent2"
    backup.restore(zip_path, app, other, apply=True)
    assert (other / "queue.sqlite3").exists()


def test_cli(app, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(app.parent))
    with pytest.raises(SystemExit) as exc:
        main(["backup", "--out", str(tmp_path / "out")])
    assert exc.value.code == 0
    zip_path = next((tmp_path / "out").glob("*.zip"))
    with pytest.raises(SystemExit):
        main(["restore", str(zip_path)])
    assert "Nothing was changed" in capsys.readouterr().out
