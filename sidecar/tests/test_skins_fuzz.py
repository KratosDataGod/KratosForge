"""Fuzz the skin file a person imports (skins.parse): seeded mutations of a valid skin, torn, not UTF-8,
a BOM, NUL bytes, wrong JSON types at any depth, deep nesting, huge strings and numbers, duplicate keys
and missing fields. Each must be read as a skin or refused with SkinError, and a skin that is read must
hold only what the rules allow, so a later export (skins.dump) writes it back."""

from __future__ import annotations

import copy
import json
import re
import unicodedata

import pytest

from fabric_desktop import skins

from tests.settings_fuzz import deep_mutations, mutations


def _valid() -> dict:
    skin = copy.deepcopy(skins.DATABRICKS)
    skin["id"] = "my-skin"
    return skin


def _texts(skin: dict) -> list[str]:
    out = [skin["name"], skin.get("description", "")]
    out += [t.get("basis", "") for t in skin.get("targets", [])]
    workflow = skin.get("workflow", {})
    out += list(workflow.get("labels", {}).values())
    for placed in workflow.get("commands", {}).values():
        out += list(placed.get("labels", {}).values())
    return out


def _check_read(label: str, raw: bytes) -> None:
    try:
        got = skins.parse(raw, skins.BASE)
    except skins.SkinError as exc:
        assert exc.problems and all(isinstance(p, str) and p for p in exc.problems), label
        return
    for text in _texts(got):
        assert isinstance(text, str), label
        assert not any(unicodedata.category(c) in ("Cc", "Cs", "Cf", "Zl", "Zp") for c in text), (label, text)
    for t in got.get("targets", []):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", t["reviewed"]), (label, t)
    skins.dump(got).encode("utf-8")  # an export of what was read writes it back


@pytest.mark.parametrize("seed", [1, 2])
def test_any_skin_file_is_read_or_refused_by_name(seed):
    for label, raw in mutations(_valid(), seed, 200):
        _check_read(label, raw)


@pytest.mark.parametrize("seed", [3, 4])
def test_a_skin_damaged_anywhere_inside_is_read_or_refused_by_name(seed):
    for label, raw in deep_mutations(_valid(), seed, 1500):
        _check_read(label, raw)


@pytest.mark.parametrize("reviewed", [20261103, "20261103", "2026-W44-2", "2026-307", 2026.5, True])
def test_reviewed_must_be_a_written_date(reviewed):
    skin = _valid()
    skin["targets"][0]["reviewed"] = reviewed
    with pytest.raises(skins.SkinError, match="reviewed must be a date like 2026-11-03"):
        skins.parse(json.dumps(skin), skins.BASE)


@pytest.mark.parametrize("text", ["Run\x85cell", "Run cell", "Run cell", "Run\x9bcell"])
def test_labels_refuse_c1_controls_and_line_separators(text):
    skin = _valid()
    skin["workflow"]["labels"]["home"] = text
    with pytest.raises(skins.SkinError, match="can't hold control characters"):
        skins.parse(json.dumps(skin), skins.BASE)


def test_a_lone_surrogate_is_refused_not_kept_for_export_to_trip_on():
    raw = json.dumps(_valid()).replace('"Databricks-familiar"', '"Mine\\ud800"')
    with pytest.raises(skins.SkinError, match="name has a broken character, because it holds half of a UTF-16 pair"):
        skins.parse(raw, skins.BASE)
