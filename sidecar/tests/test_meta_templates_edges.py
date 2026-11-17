"""Metadata project templates: init refusals, the type names a sample reads as, and an unknown template.
Files under a temporary folder only."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import polars as pl
import pytest

from fabric_desktop.meta import templates


def test_init_refuses_an_existing_project_and_an_unknown_preset(tmp_path):
    templates.init_project(tmp_path / "p", "3", "Chris", False)
    with pytest.raises(FileExistsError, match="already has a metadata project"):
        templates.init_project(tmp_path / "p")
    with pytest.raises(ValueError, match="preset must be one of"):
        templates.init_project(tmp_path / "q", "nine")


def test_a_sample_reads_as_the_project_type_names():
    sample = pl.DataFrame({"d": [dt.date(2026, 1, 1)], "ts": [dt.datetime(2026, 1, 1)], "ok": [True],
                           "n": [1], "x": [1.5], "money": pl.Series([Decimal("1.25")], dtype=pl.Decimal(10, 2)),
                           "s": ["a"]})
    assert {c: templates._dtype_name(t) for c, t in sample.schema.items()} == {
        "d": "date", "ts": "datetime", "ok": "bool", "n": "int64", "x": "float64", "money": "decimal(10,2)",
        "s": "string"}


def test_an_unknown_template_is_refused(tmp_path):
    project = templates.init_project(tmp_path / "p", "3", "Chris", False)
    with pytest.raises(ValueError, match="template must be one of"):
        templates.entity_text(project, "spreadsheet", "bronze.x", "Chris")
