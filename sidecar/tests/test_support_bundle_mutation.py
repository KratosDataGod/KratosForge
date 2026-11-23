"""Mutation batch 268: support_bundle.py, first score (docs/quality/mutation-testing.md)."""

import platform
from pathlib import Path

import pytest

from fabric_desktop import app_settings, support_bundle
from tests.test_support_bundle import pc  # noqa: F401  (a fixture)


def test_a_log_keeps_its_last_two_thousand_lines(tmp_path):
    log = tmp_path / "sidecar.log"
    log.write_text("".join(f"line {i}\n" for i in range(2500)), encoding="utf-8")
    kept = support_bundle._tail(log).splitlines()
    assert len(kept) == 2000 and kept[0] == "line 500" and kept[-1] == "line 2499"


def test_only_the_last_four_mebibytes_are_read(tmp_path):
    log = tmp_path / "sidecar.log"
    line = "x" * 4095 + "\n"
    log.write_text("first\n" + line * 1023 + "y" * 4094 + "\n", encoding="utf-8")  # 5 bytes past 4 MiB
    kept = support_bundle._tail(log)  # the read starts on the first line's newline, so only that is dropped
    assert kept.startswith("x") and kept.count("\n") == 1024 and kept.endswith("y\n")


@pytest.mark.parametrize("encoding,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
def test_a_utf16_tail_starts_on_a_whole_code_unit(tmp_path, encoding, bom):
    log = tmp_path / "agent.log"
    log.write_bytes(bom + "".join(f"row {i}\r\n" for i in range(50)).encode(encoding))
    for max_bytes in (101, 102, 103):
        kept = support_bundle._tail(log, max_bytes=max_bytes)
        assert "\ufffd" not in kept and kept.endswith("row 49\n")
        assert all(line.startswith("row ") for line in kept.splitlines())


def test_a_whole_utf16_file_is_read_from_its_first_line(tmp_path):
    log = tmp_path / "agent.log"
    log.write_bytes(b"\xff\xfe" + "one\ntwo\n".encode("utf-16-le"))
    assert support_bundle._tail(log) == "one\ntwo\n"


def test_a_key_that_begins_on_the_first_character_is_whole():
    text = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\nafter\n"
    assert support_bundle._without_cut_key(text) == text


def test_path_facts_describe_the_folder_without_naming_it():
    facts = support_bundle.path_facts(Path("C:/Users/Ann Lee/AppData/Local/FabricDesktop"))
    assert facts["data_folder_length"] == len(str(Path("C:/Users/Ann Lee/AppData/Local/FabricDesktop")))
    assert facts["data_folder_ascii"] is True and facts["data_folder_spaces"] is True
    assert facts["data_folder_network"] is False


def test_about_summarizes_the_sign_in_app_and_platforms(pc, monkeypatch):  # noqa: F811
    settings = {
        "client_id": {"value": app_settings.MICROSOFT_CLIENT_ID, "source": "default"},
        "platform": {"value": {"fabric": {}, "databricks": {"host": "x"}}, "source": "user"},
    }
    monkeypatch.setattr(app_settings, "load", lambda: settings)
    about = support_bundle.about(pc)
    assert about["settings"]["client_id"]["value"] == "Microsoft's Fabric sign-in"
    assert about["settings"]["platform"]["value"] == ["databricks", "fabric"]
    assert about["python"] == platform.python_version()
    assert about["paths"]["data_folder_length"] == len(str(pc))
    settings["client_id"] = {"value": "11111111-2222-3333-4444-555555555555", "source": "user"}
    settings["platform"] = {"value": "not a dict", "source": "user"}
    about = support_bundle.about(pc)
    assert about["settings"]["client_id"]["value"] == "own app registration"
    assert about["settings"]["platform"]["value"] == []


def test_about_describes_the_folder_given(pc, tmp_path):  # noqa: F811
    other = tmp_path / "other"
    other.mkdir()
    (other / "policy-seen.json").write_text('{"changed_at": "2026-11-01T00:00:00Z"}', encoding="utf-8")
    about = support_bundle.about(other)
    assert about["policy_record"]["last_change"]["changed_at"] == "2026-11-01T00:00:00Z"
    assert about["paths"]["data_folder_length"] == len(str(other))


def test_a_bundle_tries_ninety_nine_names_before_giving_up(tmp_path, pc, monkeypatch):  # noqa: F811
    real_open = Path.open
    tried = []

    def taken(self, mode="r", *args, **kwargs):
        if mode == "xb":
            tried.append(self.name)
            raise FileExistsError(str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", taken)
    with pytest.raises(FileExistsError):
        support_bundle.create(tmp_path / "out", data_dir=pc)
    assert len(tried) == 99 and tried[-1].endswith("-99.zip")
