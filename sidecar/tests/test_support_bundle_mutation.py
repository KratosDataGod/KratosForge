"""The support bundle: what mutation testing found unchecked in support_bundle.py. How much of a log the
tail keeps (lines and bytes), where it starts in a UTF-8 or UTF-16 file, a whole private key left as it
is, the data folder's facts, how the sign-in app and the platform setting are summarized, about.json's
layout, the 99th name from one second, and a failed write whose file is already gone."""

from __future__ import annotations

import json
import platform
import zipfile
from pathlib import Path

import pytest

from fabric_desktop import app_settings, policy_watch, support_bundle
from tests.test_support_bundle import pc  # noqa: F401  (a fixture)

OWN_APP = "11111111-2222-4333-8444-555555555555"


def test_a_log_keeps_its_last_two_thousand_lines(tmp_path):
    log = tmp_path / "sidecar.log"
    log.write_text("".join(f"line {i}\n" for i in range(2001)), encoding="utf-8")
    kept = support_bundle._tail(log).splitlines()
    assert support_bundle.LOG_LINES == 2000 and len(kept) == 2000
    assert kept[0] == "line 1" and kept[-1] == "line 2000"


def test_a_log_reads_only_its_last_four_mebibytes(tmp_path):
    log = tmp_path / "sidecar.log"
    line = b"kept123\n"
    log.write_bytes(b"gone\n" + line * (4 * 1024 * 1024 // len(line)))
    text = support_bundle._tail(log, lines=10 ** 7)
    assert support_bundle.TAIL_BYTES == 4 * 1024 * 1024
    assert "gone" not in text and text.count("kept123") == 4 * 1024 * 1024 // len(line) - 1


def test_a_utf8_tail_starts_at_the_byte_the_limit_names(tmp_path):
    log = tmp_path / "sidecar.log"
    log.write_bytes(b"aaa\nbb\ncc\n")
    assert support_bundle._tail(log, max_bytes=7) == "bb\ncc\n"   # starts on the newline after aaa


def test_a_utf16_tail_starts_on_a_whole_code_unit(tmp_path):
    log = tmp_path / "sidecar.log"
    log.write_bytes(b"\xff\xfe" + "ab\ncd\n".encode("utf-16-le"))
    assert support_bundle._tail(log) == "ab\ncd\n"
    assert support_bundle._tail(log, max_bytes=11) == "cd\n"     # start 3 becomes 4: the "b" of "ab"


def test_a_whole_private_key_at_the_very_start_is_left_for_redaction():
    label = "PRIVATE " + "KEY"  # built here, so no scanner takes this file for one holding a key
    text = f"-----BEGIN {label}-----\nMIIabc\n-----END {label}-----\nafter\n"
    assert support_bundle._without_cut_key(text) == text


def test_the_data_folders_facts(tmp_path):
    folder = tmp_path / "Fabric Desktop" / "donnees-é"
    assert support_bundle.path_facts(folder) == {
        "data_folder_length": len(str(folder)), "data_folder_ascii": False, "data_folder_spaces": True,
        "data_folder_network": False, "long_paths_enabled": None}
    plain = Path("/plain/folder")
    assert support_bundle.path_facts(plain)["data_folder_spaces"] is False


def test_about_reads_the_folder_its_given(tmp_path, pc):  # noqa: F811
    other = tmp_path / "other data"
    other.mkdir()
    (other / policy_watch.SEEN).write_text(json.dumps({"changed_at": "2026-11-01T00:00:00Z", "sha256": "ab"}))
    out = support_bundle.about(other)
    assert out["paths"]["data_folder_length"] == len(str(other)) and out["paths"]["data_folder_spaces"] is True
    assert out["policy_record"]["last_change"]["changed_at"] == "2026-11-01T00:00:00Z"
    assert out["python"] == platform.python_version()


def test_the_sign_in_app_is_named_by_whose_it_is(pc, monkeypatch):  # noqa: F811
    assert support_bundle.about(pc)["settings"]["client_id"]["value"] == "Microsoft's Fabric sign-in"
    monkeypatch.setenv("FABRIC_DESKTOP_CLIENT_ID", OWN_APP)
    assert support_bundle.about(pc)["settings"]["client_id"]["value"] == "own app registration"


def test_the_platform_setting_shows_only_its_environments(pc):  # noqa: F811
    app_settings.policy_path().write_text(json.dumps({"platform": {"prod": {"workspace_id": "w"}, "dev": {}}}))
    assert support_bundle.about(pc)["settings"]["platform"]["value"] == ["dev", "prod"]


def test_about_json_is_indented_by_two(tmp_path, pc):  # noqa: F811
    path = support_bundle.create(tmp_path / "out", data_dir=pc)
    with zipfile.ZipFile(path) as z:
        about = z.read("about.json").decode()
    assert about.startswith('{\n  "engine_version": ')


class _At:
    @staticmethod
    def now(tz=None):
        from datetime import datetime
        return datetime(2026, 11, 23, 1, 2, 3, tzinfo=tz)


def test_the_ninety_ninth_name_of_a_second_is_the_last(tmp_path, pc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(support_bundle, "datetime", _At)
    out = tmp_path / "out"
    out.mkdir()
    stamp = "fabric-desktop-support-20261123-010203"
    for n in range(1, 99):
        (out / (f"{stamp}.zip" if n == 1 else f"{stamp}-{n}.zip")).write_bytes(b"")
    assert support_bundle.create(out, data_dir=pc).name == f"{stamp}-99.zip"
    with pytest.raises(FileExistsError, match="already 99 support bundles"):
        support_bundle.create(out, data_dir=pc)
    assert len(list(out.iterdir())) == 99


def test_a_failed_write_whose_file_is_already_gone_raises_its_own_error(tmp_path, pc, monkeypatch):  # noqa: F811
    def broken(handle, *args, **kwargs):
        Path(handle.name).unlink()
        raise OSError("disk full")

    monkeypatch.setattr(support_bundle.zipfile, "ZipFile", broken)
    with pytest.raises(OSError, match="disk full"):
        support_bundle.create(tmp_path / "out", data_dir=pc)
    assert list((tmp_path / "out").iterdir()) == []


def test_contents_lines_wrap_at_a_hundred_characters():
    assert support_bundle._wrap("a" * 99 + " b") == ["a" * 99, "b"]
    assert support_bundle._wrap("a" * 98 + " b") == ["a" * 98 + " b"]
