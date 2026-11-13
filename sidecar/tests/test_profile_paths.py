"""Awkward Windows profiles: a user name with spaces and non-ASCII letters, folders Windows has moved
(OneDrive's known folder move, folder redirection), and programs that answer in a code page other than
UTF-8. Local only: no network, and the Windows-only lookups are stood in for off Windows."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import polars as pl
import pytest

import fabric_desktop
from fabric_desktop import backup, known_folders, lakehouse, onelake
from fabric_desktop import publish as publish_mod

PACKAGE = Path(fabric_desktop.__file__).parent
ODD = "José Müller-Ødegård 山田 (contoso)"  # spaces, accents, CJK and brackets, as Windows allows in a user name


@pytest.fixture
def profile(tmp_path):
    home = tmp_path / ODD
    (home / "AppData" / "Local" / "FabricDesktop").mkdir(parents=True)
    return home


def test_downloads_and_documents_follow_windows_when_the_home_is_the_profile(profile, monkeypatch):
    moved = {known_folders.PROFILE: profile, known_folders.DOWNLOADS: Path("D:/Redirected/Downloads"),
             known_folders.DOCUMENTS: profile / "OneDrive - Contoso" / "Documents"}
    monkeypatch.setattr(known_folders, "_lookup", lambda folder_id: moved.get(folder_id))
    monkeypatch.setattr(Path, "home", lambda: profile)
    assert known_folders.downloads() == Path("D:/Redirected/Downloads")
    assert known_folders.documents() == profile / "OneDrive - Contoso" / "Documents"


def test_a_home_pointed_elsewhere_keeps_its_own_folders(profile, tmp_path, monkeypatch):
    # A sandbox or a test that points the home at its own folder never writes into the real profile.
    real = {known_folders.PROFILE: profile, known_folders.DOWNLOADS: profile / "OneDrive" / "Downloads"}
    monkeypatch.setattr(known_folders, "_lookup", lambda folder_id: real.get(folder_id))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "sandbox")
    assert known_folders.downloads() == tmp_path / "sandbox" / "Downloads"


def test_no_answer_from_windows_falls_back_to_the_home(profile, monkeypatch):
    monkeypatch.setattr(known_folders, "_lookup", lambda folder_id: profile if folder_id == known_folders.PROFILE else None)
    monkeypatch.setattr(Path, "home", lambda: profile)
    assert known_folders.documents() == profile / "Documents"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own known folders")
def test_windows_answers_for_this_profile():
    assert known_folders._lookup(known_folders.PROFILE) == Path.home()
    assert known_folders.downloads().is_absolute() and known_folders.documents().is_absolute()


def _text_mode_calls_without_encoding() -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if not (name.startswith("subprocess.") or name in ("run", "Popen", "check_output")):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            text = ("text" in kw and ast.unparse(kw["text"]) != "False") or "universal_newlines" in kw
            if text and "encoding" not in kw and "errors" not in kw:
                found.append(f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}")
    return found


def test_every_program_whose_output_is_read_as_text_names_how_to_decode_it():
    """Without UTF-8 mode, text output is decoded with the PC's code page and strict errors: a name in a
    path that the program writes in another encoding (the Databricks CLI writes UTF-8; PowerShell and
    schtasks the console's code page) stops the read with UnicodeDecodeError. Each call names an
    encoding or ``errors``."""
    assert _text_mode_calls_without_encoding() == []


def test_a_backup_of_a_profile_with_an_awkward_name_restores(profile, tmp_path):
    data = profile / "AppData" / "Local" / "FabricDesktop"
    (data / "notebooks" / "Ventes 2026 – été").mkdir(parents=True)
    (data / "notebooks" / "Ventes 2026 – été" / "résumé 売上.ipynb").write_text('{"cells": []}', encoding="utf-8")
    (data / "app-settings.json").write_text(json.dumps({"folder": str(profile / "Documents")}), encoding="utf-8")
    zip_path = Path(backup.create(tmp_path / "out", data)["path"])
    (data / "notebooks" / "Ventes 2026 – été" / "résumé 売上.ipynb").write_text('{"cells": ["changed"]}', encoding="utf-8")
    assert backup.validate(zip_path)[1] == []
    backup.restore(zip_path, data, apply=True)
    assert (data / "notebooks" / "Ventes 2026 – été" / "résumé 売上.ipynb").read_text(encoding="utf-8") == '{"cells": []}'
    assert json.loads((data / "app-settings.json").read_text(encoding="utf-8")) == {"folder": str(profile / "Documents")}


def test_a_local_delta_table_under_an_awkward_profile_writes_and_reads(profile, monkeypatch):
    root = profile / "AppData" / "Local" / "FabricDesktop" / "lakehouses" / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    work = profile / "AppData" / "Local" / "FabricDesktop" / "work"
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", work, history_file=work / "history.jsonl"))
    try:
        lakehouse.write_table(pl.DataFrame({"id": [1, 2], "ville": ["Zürich", "東京"]}), "dbo.ventes")
        back = lakehouse.read_table("dbo.ventes").sort("id")
    finally:
        lakehouse.attach(None)
    assert back["ville"].to_list() == ["Zürich", "東京"]


def test_the_support_bundle_says_what_about_the_data_folder_can_trip_programs_without_naming_it(profile):
    from fabric_desktop import support_bundle
    data = profile / "AppData" / "Local" / "FabricDesktop"
    facts = support_bundle.path_facts(data)
    assert facts["data_folder_length"] == len(str(data)) and facts["data_folder_spaces"]
    assert facts["data_folder_ascii"] is False and facts["data_folder_network"] is False
    assert facts["long_paths_enabled"] in (None, True, False)
    assert support_bundle.path_facts(Path("//server/home/you"))["data_folder_network"] is True
    about = support_bundle.about(data_dir=data)
    assert about["paths"] == facts and "José" not in json.dumps(about["paths"])
