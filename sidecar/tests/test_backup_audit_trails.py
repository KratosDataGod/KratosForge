"""Restore leaves the audit trails as they are: this PC's (app/audit), the integrations' (app/integrations/audit)
and the agent's (its home's audit). A backup keeps them, marked in its manifest as never restored; every
restore passes over them by name, with the reason, whatever the zip holds. This PC's data folder is the
test's tmp_path."""

import hashlib
import json
import zipfile
from datetime import datetime, timezone

import pytest

from fabric_desktop import audit, audit_chain, backup, cli, data_format

REASON = "restore leaves the audit trails as they are"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def _files(folder) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()} \
        if folder.exists() else {}


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """This PC's data folder in tmp_path with both audit trails, a backup of it, and lines and changes since."""
    root = tmp_path / "la" / "FabricDesktop"
    monkeypatch.setenv("LOCALAPPDATA", str(root.parent))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: root)
    root.mkdir(parents=True)
    data_format.check(root)
    (root / "app-settings.json").write_text(json.dumps({"channel": "preview"}), encoding="utf-8")
    app_log = audit.AuditLog(root / "audit")
    tools_log = audit.AuditLog(root / "integrations" / "audit")
    for log in (app_log, tools_log):
        log.record("before_the_backup", "ok", 0.0)
        log.record("before_the_backup", "ok", 0.0)
    zip_path = backup.create(tmp_path / "out", root, now=NOW)["path"]
    (root / "app-settings.json").write_text(json.dumps({"channel": "stable"}), encoding="utf-8")
    for log in (app_log, tools_log):
        log.record("after_the_backup", "ok", 0.0)
    return root, zip_path


def test_a_backup_keeps_the_audit_trails_marked_never_restored(pc):
    root, zip_path = pc
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {"app/audit/head.json", "app/integrations/audit/head.json"} <= names
    assert {(m["path"], m["reason"]) for m in manifest["not_restored"]} == {
        ("app/audit/", REASON), ("app/integrations/audit/", REASON)}


def test_a_restore_of_a_backup_with_the_audit_trails_restores_everything_else_and_leaves_them_as_they_are(pc):
    root, zip_path = pc
    trails = {name: _files(root / name) for name in ("audit", "integrations/audit")}
    assert all(trails.values()) and all("head.json" in t for t in trails.values())
    result = backup.restore(zip_path, root, apply=True)
    assert json.loads((root / "app-settings.json").read_text(encoding="utf-8")) == {"channel": "preview"}
    assert {name: _files(root / name) for name in trails} == trails
    assert not any(f["path"].startswith(("app/audit/", "app/integrations/audit/")) for f in result["files"])
    assert {(n["path"], n["reason"]) for n in result["not_restored"]} >= {
        ("app/audit/head.json", REASON), ("app/integrations/audit/head.json", REASON)}
    assert result["audit_entries_not_restored"] == 4
    safety = root / backup.SAFETY
    assert not [p for p in safety.rglob("*") if "audit" in p.relative_to(safety).parts]


def test_after_a_cli_restore_the_live_trail_is_whole_and_ends_with_the_restores_own_lines(pc, capsys):
    root, zip_path = pc
    log = audit.AuditLog(root / "audit")
    before = [(e["action"], e["outcome"]) for e in log.entries()]
    tools = _files(root / "integrations" / "audit")
    with pytest.raises(SystemExit) as done:
        cli.main(["restore", zip_path, "--yes"])
    assert done.value.code == 0, capsys.readouterr().out
    entries = list(log.entries())
    assert [(e["action"], e["outcome"]) for e in entries] == before + [
        ("backup_restore", "started"), ("backup_restore", "ok")]
    assert audit_chain.verify(log.folder)["ok"]
    assert entries[-1]["target"]["audit_entries_not_restored"] == 4
    assert _files(root / "integrations" / "audit") == tools


def test_the_preview_says_the_audit_trails_arent_restored(pc, capsys):
    root, zip_path = pc
    with pytest.raises(SystemExit) as done:
        cli.main(["restore", zip_path])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "Restore leaves the audit trails as they are" in out
    assert "app/audit/" in out and "app/integrations/audit/" in out and "4 audit entries" in out


TRAIL_NAMES = ["app/audit/audit-2026-11.jsonl", "app/audit/head.json", "app/audit/.lock",
               "app/audit/audit-2026-11.jsonl.anchor", "app/Audit/audit-2026-10.jsonl",
               "app/integrations/audit/audit-2026-11.jsonl",
               "app/integrations/audit/head.json", "agent/audit/audit-2026-11.jsonl", "agent/audit/head.json",
               "agent/integrations/audit/head.json"]


def _crafted(path, members: dict[str, bytes]):
    """A zip that passes every integrity check: each member listed in its manifest with its size and hash."""
    files = [{"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in members.items()]
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
        z.writestr("manifest.json", json.dumps({"schema": backup.SCHEMA, "created": NOW.isoformat(),
                                                "files": files, "cleaned": []}))
    return path


def test_restore_leaves_the_audit_folders_as_they_are(tmp_path):
    root, home = tmp_path / "FabricDesktop", tmp_path / "agent"
    root.mkdir()
    home.mkdir()
    line = json.dumps({"action": "planted", "outcome": "ok"}).encode() + b"\n"
    members = {name: line for name in TRAIL_NAMES} | {"app/app-settings.json": b"{}"}
    zip_path = _crafted(tmp_path / "crafted.zip", members)
    preview = backup.restore(zip_path, root, home)
    assert [f["path"] for f in preview["files"]] == ["app/app-settings.json"]
    assert sorted((n["path"], n["reason"]) for n in preview["not_restored"]) == sorted(
        (name, REASON) for name in TRAIL_NAMES)
    done = backup.restore(zip_path, root, home, apply=True)
    assert (root / "app-settings.json").read_bytes() == b"{}"
    assert sorted(n["path"] for n in done["not_restored"]) == sorted(TRAIL_NAMES)
    written = [p for base in (root, home) for p in base.rglob("*") if p.is_file()]
    assert written == [root / "app-settings.json"]
    assert "Restore leaves the audit trails as they are" in backup.describe(preview)


@pytest.mark.parametrize("name", ["app/./audit/planted.jsonl", "app//audit/planted.jsonl", "app/audit//head.json",
                                  "app/audit./planted.jsonl", "app/AUDIT /planted.jsonl"])
def test_a_backup_entry_with_an_empty_dot_or_windows_alias_part_is_refused_by_name(tmp_path, name):
    root = tmp_path / "FabricDesktop"
    root.mkdir()
    zip_path = _crafted(tmp_path / "crafted.zip", {name: b"{}\n", "app/app-settings.json": b"{}"})
    with pytest.raises(backup.BackupError, match="never restored") as refused:
        backup.restore(zip_path, root, apply=True)
    assert repr(name) in str(refused.value)
    assert list(root.rglob("*")) == []


def test_a_backup_entry_with_an_empty_path_part_is_refused(tmp_path):
    root, outside = tmp_path / "FabricDesktop", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    name = "app/" + (outside / "audit" / "head.json").as_posix()  # app//<folder>/...
    zip_path = _crafted(tmp_path / "crafted.zip", {name: b"{}"})
    with pytest.raises(backup.BackupError, match="never restored"):
        backup.restore(zip_path, root, apply=True)
    assert list(outside.rglob("*")) == [] and list(root.rglob("*")) == []
