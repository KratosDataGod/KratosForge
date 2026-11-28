"""Mutation-testing follow-ups for controlplane/manifest.py (batch 465): the name, text and size limits at
their edges, a local environment's ids, a key that isn't text, a plural credential word, the operational
schema version's floor, what an update keeps of compatibility, the summary's labels, the bytes a manifest
writes, and a request with nothing wrong."""
from __future__ import annotations

import json

import pytest

from fabric_desktop.controlplane import manifest as mf

WS = "11111111-1111-4111-8111-111111111111"
LH = "22222222-2222-4222-8222-222222222222"
OID = "55555555-5555-4555-8555-555555555555"
AT = "2026-11-19T20:00:00Z"


def fabric(**extra):
    kwargs = dict(name="Contoso data platform", provider="fabric",
                  namespace={"workspace_id": WS, "display_name": "KratosForge"},
                  analytical_store={"workspace_id": WS, "lakehouse_id": LH, "display_name": "KratosForgeLakehouse"},
                  families={"fdopt_": 2}, owner_object_id=OID, services=["optimize"], at=AT)
    return mf.new_manifest(**(kwargs | extra))


def local(ws="ws-1", lh="lake.main_1", **extra):
    kwargs = dict(name="On this PC", provider="local", namespace={"workspace_id": ws},
                  analytical_store={"workspace_id": ws, "lakehouse_id": lh}, at=AT)
    return mf.new_manifest(**(kwargs | extra))


# ---- limits


def test_a_name_of_120_characters_is_kept_and_121_is_refused():
    assert fabric(name="n" * 120)["name"] == "n" * 120
    with pytest.raises(mf.ManifestError, match="at most 120 characters"):
        fabric(name="n" * 121)


def test_a_failed_migration_step_of_400_characters_is_kept_and_401_is_refused():
    m = fabric()
    assert mf.updated(m, {"migration": {"state": "failed", "step": "s" * 400}}, at=AT)["migration"]["step"] == "s" * 400
    with pytest.raises(mf.ManifestError, match="migration.step must be one line of text, at most 400 characters"):
        mf.updated(m, {"migration": {"state": "failed", "step": "s" * 401}}, at=AT)


def _padded(size: int) -> bytes:
    data = mf.to_json(fabric())
    return data[:-2] + b" " * (size - len(data)) + data[-2:]


def test_a_manifest_file_of_64_kb_is_read_and_one_byte_more_isnt():
    assert mf.from_json(_padded(64 * 1024))["name"] == "Contoso data platform"
    with pytest.raises(mf.ManifestError, match="^The manifest is larger than 64 KB"):
        mf.from_json(_padded(64 * 1024 + 1))


# ---- local ids


def test_a_local_environment_takes_folder_safe_ids():
    m = local()
    assert m["provider"] == "local" and m["analytical_store"]["lakehouse_id"] == "lake.main_1"


@pytest.mark.parametrize("bad", ["", "bad id", "-lead", "x" * 65, 5])
def test_a_local_id_that_isnt_folder_safe_is_refused(bad):
    with pytest.raises(mf.ManifestError, match="namespace.workspace_id"):
        local(ws=bad)


def test_an_optional_local_id_may_be_empty():
    assert mf._ref("local", "", "x", required=False) is None
    assert mf._ref("local", None, "x", required=False) is None
    with pytest.raises(mf.ManifestError, match="must be a local id"):
        mf._ref("local", "bad id", "x", required=False)


# ---- keys


def test_a_key_that_isnt_text_is_refused():
    m = fabric()
    m["extra"] = {5: "x"}
    with pytest.raises(mf.ManifestError, match=r"^manifest.extra: keys must be text$"):
        mf.validate(m)


def test_a_plural_four_letter_credential_word_is_refused():
    assert mf._secret_key("pwds") and mf._secret_key("db_pwds")
    m = fabric()
    m["pwds"] = ["x"]
    with pytest.raises(mf.ManifestError, match="never holds credentials"):
        mf.validate(m)


# ---- schema


def test_the_operational_schema_version_starts_at_one():
    assert fabric(operational_version=1)["schema"]["operational"] == 1
    m = fabric()
    m["schema"]["operational"] = 0
    with pytest.raises(mf.ManifestError, match="schema.operational must be a whole number"):
        mf.validate(m)


# ---- update, summary, bytes, requests


def test_an_update_keeps_the_minimum_app_version():
    m = fabric()
    m["compatibility"]["min_app_version"] = "0.1.0"
    out = mf.updated(m, {"name": "Renamed"}, at=AT)
    assert out["compatibility"]["min_app_version"] == "0.1.0"
    assert out["compatibility"]["written_by_app_version"] == mf.__version__


def test_the_summary_names_the_workspace():
    s = mf.summary(fabric())
    assert s["workspace_id"] == WS and s["workspace_name"] == "KratosForge"


def test_the_bytes_are_sorted_two_space_json_in_utf8():
    data = mf.to_json(fabric(name="Café data"))
    assert "Café data".encode() in data
    text = data.decode("utf-8")
    assert text.startswith('{\n  "analytical_store": {\n    "')
    keys = list(json.loads(text, object_pairs_hook=lambda pairs: [k for k, _ in pairs]))
    assert keys == sorted(keys)


def test_a_request_with_nothing_wrong_is_empty_text():
    assert mf.check_request_fields(name="Data", workspace_name="WS", lakehouse_name="LH", owner_object_id=OID) == ""
