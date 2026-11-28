"""Mutation batch 514, a re-score of backup.py (batches 4 and 151): the audit trails a restore leaves
alone and the lines they hold, the manifest's size limit, a manifest's parts of the wrong kind, Windows
aliases and skipped names in any case, names too long for this PC, and a folder where a file goes."""

from __future__ import annotations

import errno
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fabric_desktop import backup

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
MIB = 1024 * 1024


def _backup(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(text.encode("utf-8"))
    return Path(backup.create(tmp_path / "out", src, now=NOW)["path"])


def _crafted(path: Path, manifest: dict, members: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (members or {}).items():
            z.writestr(name, data)
        z.writestr(backup.MANIFEST, json.dumps(manifest))
    return path


def _entry(name: str, data: bytes) -> dict:
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# ---- audit trails


def test_the_audit_trail_a_name_is_in_is_found_by_its_first_two_parts():
    assert backup._audit_trail("app/audit/2026.jsonl") == "app/audit/"
    assert backup._audit_trail("app/audit") == "app/audit/"
    assert backup._audit_trail("agent/integrations/audit/x.jsonl") == "agent/integrations/audit/"
    assert backup._audit_trail("other/audit/x.jsonl") is None
    assert backup._audit_trail("app/notes.txt") is None
    assert backup._audit_trail("app") is None


def test_a_preview_counts_the_lines_of_the_audit_trails_jsonl_files_only(tmp_path):
    zip_path = _backup(tmp_path, {"audit/a.jsonl": '{"n": 1}\n\n{"n": 2}\n', "audit/notes.txt": "one\ntwo\nthree\n",
                                  "notes.txt": "x\n"})
    out = backup.restore(zip_path, tmp_path / "fresh")
    assert out["audit_entries_not_restored"] == 2
    assert [f["path"] for f in out["files"]] == ["app/notes.txt"]


def test_a_preview_with_one_audit_line_says_entry_and_names_the_trail():
    result = {"files": [], "created": "2026-11-26T12:00:00+00:00", "cleaned": [], "applied": False,
              "not_restored": [{"path": "app/audit/a.jsonl", "reason": backup.AUDIT_REASON},
                               {"path": "app/audit/b.jsonl", "reason": backup.AUDIT_REASON}],
              "audit_entries_not_restored": 1}
    text = backup.describe(result)
    assert "the backup's app/audit/ (1 audit entry) aren't restored" in text
    result["audit_entries_not_restored"] = 2
    assert "(2 audit entries)" in backup.describe(result)


# ---- the manifest


def _padded_manifest_zip(path: Path, size: int) -> Path:
    body = json.dumps({"schema": backup.SCHEMA, "created": "2026-11-26T12:00:00+00:00", "files": []})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr(backup.MANIFEST, body + " " * (size - len(body)))
    return path


def test_a_manifest_of_exactly_64_mb_is_read_and_one_byte_more_is_not(tmp_path):
    manifest, problems = backup.validate(_padded_manifest_zip(tmp_path / "at.zip", 64 * MIB))
    assert problems == [] and manifest["schema"] == backup.SCHEMA
    manifest, problems = backup.validate(_padded_manifest_zip(tmp_path / "over.zip", 64 * MIB + 1))
    assert manifest == {}
    assert problems == [("Its manifest.json wasn't read, because it's over 64 MB, far larger than any backup's. "
                         "Pick a backup KratosForge made.")]


def test_a_manifest_listing_something_that_isnt_an_entry_cant_be_used(tmp_path):
    zip_path = _crafted(tmp_path / "b.zip", {"schema": backup.SCHEMA, "created": "x", "files": ["app/notes.txt"]})
    manifest, problems = backup.validate(zip_path)
    assert manifest == {} and problems[0].startswith("Its manifest.json can't be used")


def test_a_cleaned_note_without_what_was_removed_cant_be_used(tmp_path):
    zip_path = _crafted(tmp_path / "b.zip", {"schema": backup.SCHEMA, "created": "x", "files": [],
                                             "cleaned": [{"path": "app/settings.json"}]})
    manifest, problems = backup.validate(zip_path)
    assert manifest == {} and problems[0].startswith("Its manifest.json can't be used")


# ---- names


@pytest.mark.parametrize("part", ["NUL", "com1.txt", "draft~1.txt", "notes.", "notes "])
def test_each_kind_of_windows_alias_is_one(part):
    assert backup._windows_alias(part) is True


def test_a_plain_name_is_no_windows_alias():
    assert backup._windows_alias("notes.txt") is False


def test_a_skipped_name_in_another_case_is_refused_in_a_manifest(tmp_path):
    data = b"x"
    zip_path = _crafted(tmp_path / "b.zip", {"schema": backup.SCHEMA, "created": "x",
                                             "files": [_entry("app/Debug.LOG", data)]}, {"app/Debug.LOG": data})
    _, problems = backup.validate(zip_path)
    assert problems == ["'app/Debug.LOG' would be written outside the app's folders or is never restored"]


def test_a_skipped_folder_named_in_another_case_is_refused():
    # EBWebView is the one skipped folder whose name isn't all lower case.
    assert backup._safe("app/ebwebview/Default/Cookies") is False
    assert backup._safe("app/EBWEBVIEW/Default/Cookies") is False
    assert backup._safe("app/notebooks/Default/Cookies") is True


def test_a_top_level_file_named_like_a_skipped_folder_is_backed_up_and_restored(tmp_path):
    zip_path = _backup(tmp_path, {"Logs": "a file, not the logs folder"})
    out = backup.restore(zip_path, tmp_path / "fresh")
    assert [f["path"] for f in out["files"]] == ["app/Logs"]


def test_a_name_whose_staging_name_is_exactly_255_characters_can_be_restored(tmp_path):
    name = "n" * (255 - len(".restoring"))
    zip_path = _backup(tmp_path, {name: "x"})
    out = backup.restore(zip_path, tmp_path / "fresh")
    assert [f["path"] for f in out["files"]] == [f"app/{name}"]


def test_a_name_whose_staging_name_is_256_characters_is_refused_by_name(tmp_path):
    name = "n" * (256 - len(".restoring"))
    zip_path = _backup(tmp_path, {name: "x"})
    with pytest.raises(backup.BackupError, match="its path is too long for this PC") as caught:
        backup.restore(zip_path, tmp_path / "fresh")
    assert str(caught.value).startswith(f"Nothing was restored, because app/{name} can't be written to ")


def test_name_too_long_is_said_for_its_error_number_or_its_file_name(tmp_path):
    dest = tmp_path / "short.txt"
    by_number = backup._unwritable("app/short.txt", dest, OSError(errno.ENAMETOOLONG, "too long", str(dest)))
    assert by_number is not None and "(its path is too long for this PC)" in str(by_number)
    long_name = str(tmp_path / ("n" * 256))
    by_name = backup._unwritable("app/short.txt", dest, OSError(errno.ENOENT, "not there", long_name))
    assert by_name is not None and "(its path is too long for this PC)" in str(by_name)
    assert backup._unwritable("app/short.txt", dest, OSError(errno.EACCES, "denied", str(dest))) is None


def test_a_folder_where_a_restored_file_goes_is_named_and_nothing_changes(tmp_path):
    zip_path = _backup(tmp_path, {"notes.txt": "backed up"})
    target = tmp_path / "fresh"
    (target / "notes.txt").mkdir(parents=True)
    (target / "notes.txt" / "inside.txt").write_text("kept")
    with pytest.raises(backup.BackupError, match=r"\(a folder is where the file goes\)"):
        backup.restore(zip_path, target, apply=True, now=NOW)
    assert (target / "notes.txt" / "inside.txt").read_text() == "kept"
    assert not (target / "notes.txt.restoring").exists()
