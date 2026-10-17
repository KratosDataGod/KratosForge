"""Backup and restore: what goes in, what never does, and checks before anything is written."""

import json
import sqlite3
import zipfile
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
    assert sqlite3.connect(app / "workloads.sqlite").execute("select name from w").fetchall() == [("orders",)]
    assert "set it again" in backup.describe(done)


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
    sqlite3.connect(home / "queue.sqlite3").execute("create table q (id int)").connection.commit()
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
