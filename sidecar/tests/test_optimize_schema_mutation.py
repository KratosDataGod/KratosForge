"""The central lakehouse's table schema (optimize/schema.py), an edge the mutation run (batch 82)
found unchecked: a migration can't be changed once made. Nothing is written."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.optimize import schema


def test_a_migration_cant_be_changed_once_made():
    m = schema.Migration(9, "Add a table", {"extra": {"x": schema.S}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.version = 10  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        schema.MIGRATIONS[0].tables = {}  # type: ignore[misc]
    assert m.version == 9 and schema.MIGRATIONS[0].tables is schema.TABLES_V1
