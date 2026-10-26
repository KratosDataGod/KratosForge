"""Imported files nested too deeply to be what they claim get a plain reason, not a RecursionError."""

import pytest

from fabric_desktop import model_ossie, notebook_files, notebook_formats
from fabric_desktop.orchestration import definition

DEEP_JSON = '{"cells": ' + "[" * 200_000
DEEP_YAML = "version: x\nname: " + "[" * 50_000


def test_an_ipynb_import_nested_too_deep_says_it_isnt_json():
    with pytest.raises(ValueError, match="isn't valid JSON"):
        notebook_formats.parse_ipynb(DEEP_JSON)


def test_a_notebook_file_nested_too_deep_says_it_isnt_json():
    with pytest.raises(ValueError, match="isn't JSON"):
        notebook_files._parse(DEEP_JSON)


def test_a_pipeline_definition_nested_too_deep_is_refused():
    with pytest.raises(definition.DefinitionError, match="nested too deeply"):
        definition._parse("[" * 200_000, "The pipeline", [])


def test_an_ossie_file_nested_too_deep_is_refused():
    with pytest.raises(ValueError, match="nested too deeply"):
        model_ossie.parse(DEEP_YAML)
