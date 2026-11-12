"""Mutation-testing pins for schema_cache (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

from fabric_desktop import schema_cache as S


def test_a_lakehouse_id_is_cut_to_64_characters_for_its_file_name(tmp_path):
    long_id = "a" * 64 + "bcdefg"
    assert S._path(tmp_path, long_id) == tmp_path / S.FOLDER / ("a" * 64 + ".json")
