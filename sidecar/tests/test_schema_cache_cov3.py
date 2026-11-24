"""The completion names cache (schema_cache): a write that fails is logged, never raised, and leaves the
last saved names as they were."""

from __future__ import annotations

import pytest

from fabric_desktop import schema_cache


def test_a_cache_write_that_fails_is_logged_and_keeps_the_names_saved_before(tmp_path):
    schema_cache.record_tables(tmp_path, "lh1", {"dbo": ["sales"]})
    warned = []

    def full(path, text):
        raise OSError("disk full")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(schema_cache._log, "warning", lambda *a: warned.append(a))
        mp.setattr(schema_cache.jsonfile, "write_text", full)
        schema_cache.record_columns(tmp_path, "lh1", "sales", [{"name": "amount", "type": "double"}])  # doesn't raise
        assert len(warned) == 1 and warned[0][0] == "Couldn't save the table names for completion: %s"
        assert str(warned[0][1]) == "disk full"
    assert schema_cache.load(tmp_path, "lh1") == {"tables": {"dbo": ["sales"]}, "columns": {}}
