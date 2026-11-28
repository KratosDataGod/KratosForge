"""policy_schema at its edges: the templates written to a repository's deploy/policy folder (by the
function and by running the module), byte for byte what admx() and adml() return, and a setting of a kind
the template has no box for, which gets a policy with no elements rather than a wrong one."""

from __future__ import annotations

import runpy
import sys
import warnings
from pathlib import Path

from fabric_desktop import policy_schema as ps


def test_write_templates_puts_the_admx_and_the_en_us_adml_under_deploy_policy(tmp_path):
    ps.write_templates(str(tmp_path))
    folder = tmp_path / "deploy" / "policy"
    assert (folder / "KratosForge.admx").read_bytes() == ps.admx().encode("utf-8")
    assert (folder / "en-US" / "KratosForge.adml").read_bytes() == ps.adml().encode("utf-8")
    ps.write_templates(tmp_path)  # again, over what's there: the folders already exist
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == [
        "KratosForge.admx", "en-US", "en-US/KratosForge.adml"]


def test_running_the_module_writes_the_templates_to_the_folder_given(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["policy_schema", str(tmp_path)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the module is already imported
        runpy.run_module("fabric_desktop.policy_schema", run_name="__main__")
    assert (tmp_path / "deploy" / "policy" / "KratosForge.admx").read_text(encoding="utf-8").startswith("<?xml")
    assert (tmp_path / "deploy" / "policy" / "en-US" / "KratosForge.adml").is_file()


def test_running_the_module_with_no_folder_writes_to_the_repository_it_is_in(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(sys, "argv", ["policy_schema"])
    monkeypatch.setattr("pathlib.Path.write_bytes", lambda self, data: written.append(self) or len(data))
    monkeypatch.setattr("pathlib.Path.mkdir", lambda self, *a, **k: None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("fabric_desktop.policy_schema", run_name="__main__")
    repo = Path(ps.__file__).resolve().parents[2]
    assert written == [repo / "deploy" / "policy" / "KratosForge.admx",
                       repo / "deploy" / "policy" / "en-US" / "KratosForge.adml"]


def test_a_kind_the_template_has_no_box_for_gets_an_empty_elements_block(monkeypatch):
    odd = ps.Setting("odd_setting", "duration", "fabric", "A kind the templates don't know.")
    monkeypatch.setattr(ps, "settings", lambda: [odd])
    admx = ps.admx()
    start = admx.index('<policy name="odd_setting"')
    block = admx[start:admx.index("</policy>", start)]
    assert 'presentation="$(presentation.odd_setting)"' in block
    assert "<elements>\n      </elements>" in block
    adml = ps.adml()
    assert '<string id="odd_setting_Help">' in adml
    assert 'presentation id="odd_setting"' not in adml
