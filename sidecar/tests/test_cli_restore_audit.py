"""`fabric-desktop restore --yes` replaces this PC's data, so it is audited and fails closed: a "started" line
naming the backup is written before any file changes, the restore doesn't run when that line can't be
written, and how it ended is recorded after. This PC's data folder is the test's tmp_path."""

import hashlib
import json
import zipfile

import pytest

from fabric_desktop import audit, audit_chain, backup, cli, data_format


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """This PC's data folder (LOCALAPPDATA and service.data_dir) in tmp_path, with a backup of it in hand
    and a changed file since, so a restore has something to replace."""
    root = tmp_path / "la" / "FabricDesktop"
    monkeypatch.setenv("LOCALAPPDATA", str(root.parent))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: root)
    (root / "notebooks").mkdir(parents=True)
    (root / "notebooks" / "sales.ipynb").write_text('{"cells": []}', encoding="utf-8")
    (root / "app-settings.json").write_text(json.dumps({"channel": "preview"}), encoding="utf-8")
    data_format.check(root)  # as the CLI does for a command that uses the data folder
    zip_path = backup.create(tmp_path / "out", root)["path"]
    (root / "app-settings.json").write_text(json.dumps({"channel": "stable"}), encoding="utf-8")
    return root, zip_path


def _restore(*argv: str) -> int:
    with pytest.raises(SystemExit) as done:
        cli.main(["restore", *argv])
    return done.value.code


def _lines(root) -> list[dict]:
    log = audit.AuditLog(root / "audit")
    return list(log.entries()) if log.folder.exists() else []


def _data(root) -> dict[str, str]:
    """Every file in the data folder but the audit trail, by its hash."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "audit" not in p.relative_to(root).parts}


def test_a_restore_writes_its_started_line_naming_the_backup_before_any_file_changes(pc, monkeypatch, capsys):
    root, zip_path = pc
    seen = []
    real = backup.restore

    def noted(*a, **k):
        seen.append(([(e["action"], e["outcome"]) for e in _lines(root)], k.get("apply"),
                     json.loads((root / "app-settings.json").read_text(encoding="utf-8"))["channel"]))
        return real(*a, **k)
    monkeypatch.setattr(backup, "restore", noted)
    assert _restore(zip_path, "--yes") == 0
    assert seen == [([("backup_restore", "started")], True, "stable")]
    assert json.loads((root / "app-settings.json").read_text(encoding="utf-8")) == {"channel": "preview"}
    started, ended = _lines(root)
    assert (started["action"], started["outcome"]) == ("backup_restore", "started")
    assert (ended["action"], ended["outcome"]) == ("backup_restore", "ok")
    assert started["target"]["backup"] == zip_path == ended["target"]["backup"]
    assert all(e["who"].endswith(" (cli)") for e in (started, ended))
    assert audit.AuditLog(root / "audit").verify()["ok"]


def test_the_started_line_names_the_backup_file_never_its_contents(pc):
    root, zip_path = pc
    assert _restore(zip_path, "--yes") == 0
    text = b"".join(p.read_bytes() for p in (root / "audit").iterdir() if p.is_file())
    assert b"preview" not in text and b"cells" not in text


def test_a_restore_doesnt_run_and_changes_nothing_when_its_started_line_cant_be_written(pc, monkeypatch, capsys):
    root, zip_path = pc
    before = _data(root)

    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit_chain, "write", full)
    assert _restore(zip_path, "--yes") == 2
    out = capsys.readouterr().out
    assert out.startswith("error: The audit log couldn't be written, so this didn't run")
    assert "disk full" in out
    assert _data(root) == before
    assert not (root / backup.SAFETY).exists()
    assert _lines(root) == []


def test_a_restore_that_fails_records_why(pc, capsys):
    root, zip_path = pc
    with zipfile.ZipFile(zip_path, "a") as z:  # a file the manifest doesn't list: the backup is refused
        z.writestr("app/extra.json", "{}")
    before = _data(root)
    assert _restore(zip_path, "--yes") == 1
    assert "This backup can't be restored" in capsys.readouterr().out
    assert _data(root) == before
    started, ended = _lines(root)
    assert (started["outcome"], ended["action"], ended["outcome"]) == ("started", "backup_restore", "error")
    assert ended["reason"] == "refused" and "can't be restored" in ended["error"]
    assert ended["target"]["backup"] == zip_path


def test_a_restore_that_stops_on_an_unexpected_error_records_it(pc, monkeypatch):
    root, zip_path = pc

    def broken(*a, **k):
        raise OSError("the disk went away")
    monkeypatch.setattr(backup, "restore", broken)
    with pytest.raises(OSError):
        cli.main(["restore", zip_path, "--yes"])
    started, ended = _lines(root)
    assert (started["outcome"], ended["outcome"], ended["reason"]) == ("started", "error", "failed")
    assert "the disk went away" in ended["error"]


def test_checking_a_backup_without_yes_changes_nothing_and_writes_no_line(pc, capsys):
    root, zip_path = pc
    before = _data(root)
    assert _restore(zip_path) == 0
    assert _data(root) == before
    assert _lines(root) == []
