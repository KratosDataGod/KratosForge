"""Fuzz the model folders the Semantic model tab reads on this PC: semantic/<name>.SemanticModel/ with its TMDL
files (definition/) or its model.bim, and the Ossie files it opens from the project. test_tmdl_fuzz.py damages TMDL
as text; this damages the files as bytes on disk (not UTF-8, UTF-16, a BOM, NUL bytes, torn files, byte flips) and
model.bim as JSON (tests/settings_fuzz.py: wrong types, deep nesting, duplicate keys, huge values), then reads the
folder as Export does and converts it to Ossie.

The property: the folder is read and converted, or refused with a ValueError whose message names the file, never
another exception. Every loop uses fixed seeds, so a failure names its seed and reproduces exactly."""

from __future__ import annotations

import json
import random
import shutil
import warnings
from pathlib import Path

import pytest

from fabric_desktop import model_ossie, tmdl
from fabric_desktop.model_ossie_service import _folder_bim
from tests.settings_fuzz import mutations
from tests.test_model_ossie import _bim, _ossie, svc  # noqa: F401  (svc is a fixture)

pytestmark = pytest.mark.skipif(bool(model_ossie.unavailable()), reason="the Ossie converter can't run here")

PATH = "semantic/M.SemanticModel"
INSERTS = [b"\xef\xbb\xbf", b"\xff\xfe", b"\x00", b"\xc3", b"\xed\xa0\x80", b"\r", b"\r\n", b"\n\t", b"\xc2\x85",
           "\u2028".encode(), b"'", b"```", b"\n\t\tcolumn"]


def _export(folder: Path, case: str) -> bool:
    """Read ``folder`` as Export does and convert it; False when refused (by name, or the test fails)."""
    try:
        bim, report = _folder_bim(folder, "M", PATH)
    except ValueError as exc:
        assert type(exc) is ValueError and str(exc).strip(), f"{case}: {type(exc).__name__}: {exc}"
        if "codec" in str(exc) or "char " in str(exc):  # a decoder's or json's own words: then name the file
            assert str(exc).startswith(PATH), f"{case}: {exc}"
        return False
    try:
        model_ossie.export(bim, validate=True, read_report=report)
    except ValueError as exc:
        assert str(exc).strip(), case
        return False
    return True


def test_damaged_model_bim_files_are_read_or_refused_by_name(tmp_path):
    read = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for label, raw in mutations(_bim(), 11, 140):
            folder = tmp_path / label.replace(" ", "_")
            folder.mkdir()
            (folder / "model.bim").write_bytes(raw)
            read += _export(folder, label)
    assert read > 40


def test_damaged_tmdl_files_on_disk_are_read_or_refused_by_name(tmp_path):
    files, _ = tmdl.from_bim(_bim())
    r = random.Random(17)
    read = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for i in range(150):
            folder = tmp_path / "m"
            shutil.rmtree(folder, ignore_errors=True)
            damaged = set(r.sample(sorted(files), r.randint(1, 2)))
            for path, text in files.items():
                raw = text.encode()
                pick, at = (r.random() if path in damaged else 1), r.randint(0, len(raw))
                if pick < .3:
                    raw = raw[:at] + r.choice(INSERTS) + raw[at:]
                elif pick < .4:
                    raw = raw[:at]
                elif pick < .45:
                    raw = text.encode("utf-16")
                elif pick < .55:
                    flipped = bytearray(raw)
                    for _ in range(r.randint(1, 4)):
                        flipped[r.randrange(len(flipped))] = r.randrange(256)
                    raw = bytes(flipped)
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            read += _export(folder, f"case {i}")
    assert read > 60


# ---- the bugs the loops found, one by one

@pytest.mark.parametrize("raw", [b"model Model\n\tculture: \xff\n", "model Model\n".encode("utf-16")])
def test_a_tmdl_file_that_isnt_utf8_is_refused_naming_the_file(tmp_path, raw):
    (tmp_path / "definition").mkdir()
    (tmp_path / "definition" / "model.tmdl").write_bytes(raw)
    with pytest.raises(ValueError, match=f"^{PATH}/definition/model.tmdl isn't UTF-8 text"):
        _folder_bim(tmp_path, "M", PATH)


@pytest.mark.parametrize("raw, why", [(b"", "isn't valid JSON"), (b'{"model": {}}}', "isn't valid JSON"),
                                      (json.dumps(_bim()).encode("utf-16"), "isn't UTF-8 text")])
def test_a_model_bim_that_isnt_json_is_refused_naming_the_file(tmp_path, raw, why):
    (tmp_path / "model.bim").write_bytes(raw)
    with pytest.raises(ValueError, match=f"^{PATH}/model.bim {why}"):
        _folder_bim(tmp_path, "M", PATH)


@pytest.mark.parametrize("key, value", [("tables", 5), ("tables", 1.5), ("relationships", 3)])
def test_a_model_bim_with_a_part_of_the_wrong_type_is_refused_by_name(key, value):
    bim = _bim()
    bim["model"][key] = value
    with pytest.raises(ValueError, match="^This model couldn't be converted to Ossie, because part of it isn't the shape"):
        model_ossie.export(bim)


def test_a_model_nested_too_deeply_to_compare_still_exports_and_says_the_round_trip_didnt_run():
    bim = _bim()
    bim["model"]["annotations"] = [{"name": "x", "value": json.loads("[" * 3000 + "]" * 3000)}]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = model_ossie.export(bim, validate=True)
    assert out["round_trip"][0]["message"].startswith("the round-trip check didn't run")


def test_an_ossie_file_in_the_project_that_isnt_utf8_is_refused_naming_it(svc):  # noqa: F811
    target = Path(svc._project().root) / "semantic" / "bad.ossie.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(_ossie().encode("utf-16"))
    with pytest.raises(ValueError, match="^semantic/bad.ossie.yaml isn't UTF-8 text"):
        svc.meta_model_ossie_read("semantic/bad.ossie.yaml")


@pytest.mark.parametrize("path", ["C:/x.ossie.yaml", "C:x.ossie.yaml", "semantic/C:x.ossie.yaml",
                                  "semantic/a.ossie.yaml:stream.ossie.yaml"])
def test_an_ossie_path_with_a_drive_or_a_stream_is_refused(svc, path):  # noqa: F811
    """On Windows, Path(project, "C:", "x") is C:x, outside the project: a path with ":" is never joined."""
    with pytest.raises(ValueError, match="Save it as a path inside the project"):
        svc.meta_model_ossie_save(path, _ossie())
    with pytest.raises(ValueError, match="Save it as a path inside the project"):
        svc.meta_model_ossie_read(path)
    assert not list(Path(svc._project().root).rglob("*:*"))
