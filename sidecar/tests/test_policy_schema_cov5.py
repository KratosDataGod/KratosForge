"""The policy templates' writer (policy_schema.py): write_templates puts the ADMX and the en-US ADML
under deploy/policy of the folder it is given, as UTF-8 bytes, making the folders it needs and
replacing what was there; ``python -m fabric_desktop.policy_schema <repo>`` does the same."""

import runpy
import sys
import warnings

from fabric_desktop import policy_schema


def _templates(root):
    folder = root / "deploy" / "policy"
    return (folder / "KratosForge.admx").read_bytes(), (folder / "en-US" / "KratosForge.adml").read_bytes()


def test_write_templates_makes_the_folders_and_writes_both_files(tmp_path):
    policy_schema.write_templates(tmp_path)
    admx, adml = _templates(tmp_path)
    assert admx == policy_schema.admx().encode("utf-8")
    assert adml == policy_schema.adml().encode("utf-8")
    assert admx.startswith(b'<?xml version="1.0" encoding="utf-8"?>')


def test_write_templates_replaces_old_templates(tmp_path):
    folder = tmp_path / "deploy" / "policy" / "en-US"
    folder.mkdir(parents=True)
    (folder / "KratosForge.adml").write_text("old")
    (folder.parent / "KratosForge.admx").write_text("old")
    policy_schema.write_templates(str(tmp_path))
    assert _templates(tmp_path) == (policy_schema.admx().encode("utf-8"), policy_schema.adml().encode("utf-8"))


def test_running_the_module_writes_the_templates_where_it_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["policy_schema", str(tmp_path)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the module is already imported by the test
        runpy.run_module("fabric_desktop.policy_schema", run_name="__main__")
    assert _templates(tmp_path) == (policy_schema.admx().encode("utf-8"), policy_schema.adml().encode("utf-8"))
