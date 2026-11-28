"""Metadata runs on Fabric capacity (fabric_compute.py): the bundle's config file, the runner
notebook's format, run parameters, and run_bundle's defaults, results folder and clean-up."""

from __future__ import annotations

import json
import shutil

import pytest

from fabric_desktop import fabric_compute as fc
from fabric_desktop.meta import Engine, LocalStore
from fabric_desktop.meta.templates import init_project
from test_fabric_compute import write_bundle

_REAL_RMTREE = shutil.rmtree  # saved once: one test below replaces it


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def bundle(tmp_path):
    proj = tmp_path / "proj"
    init_project(proj, "3", "Chris", True)
    stores = (LocalStore(proj / ".local" / "dev", "dev"), LocalStore(proj / ".local" / "keys-dev", "dev"))
    return write_bundle(fc.bundle_files(proj, {"env": "dev"}), tmp_path / "bundle"), stores


def test_the_config_file_is_sorted_and_indented_so_the_same_config_hashes_the_same(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    one = fc.bundle_files(proj, {"env": "dev", "workspace_id": "w"})
    other = fc.bundle_files(proj, {"workspace_id": "w", "env": "dev"})
    assert one["config.json"] == other["config.json"] == b'{\n  "env": "dev",\n  "workspace_id": "w"\n}'
    assert fc.bundle_hash(one) == fc.bundle_hash(other)


def test_the_runner_is_an_nbformat_4_5_notebook():
    nb = fc.runner_ipynb("ws1", "lhmeta")
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 5)


def test_run_parameters_send_text_for_no_entities_and_no_backfill():
    params = fc.run_parameters(None, None, False, "fd-1")["parameters"]
    assert params["entities"] == {"value": "", "type": "string"}
    assert params["backfill"] == {"value": "", "type": "string"}
    assert params["cascade"] == {"value": False, "type": "bool"}
    assert fc.run_parameters([], "full", 0, "r")["parameters"]["backfill"] == {"value": "full", "type": "string"}


def test_one_entity_runs_alone_unless_cascade_is_asked_for(bundle, tmp_path):
    b, stores = bundle
    out = fc.run_bundle(b, entities="bronze.orders", stores=stores, results_dir=tmp_path / "r")
    assert [r["entity"] for r in out["results"]] == ["bronze.orders"]


def test_the_backfill_asked_for_reaches_the_engine(bundle, tmp_path, monkeypatch):
    b, stores = bundle
    seen = []
    monkeypatch.setattr(Engine, "run_many", lambda self, names, backfill=None: seen.append((names, backfill)) or [])
    fc.run_bundle(b, entities="bronze.orders", backfill="full", stores=stores, results_dir=tmp_path / "r")
    fc.run_bundle(b, stores=stores, results_dir=tmp_path / "r")
    assert seen == [(["bronze.orders"], "full"), (None, None)]


def test_the_results_folder_is_made_with_its_parents_and_the_file_is_indented(bundle, tmp_path):
    b, stores = bundle
    folder = tmp_path / "a" / "b" / "results"
    fc.run_bundle(b, entities="bronze.orders", request_id="fd-9", stores=stores, results_dir=folder)
    text = (folder / "fd-9.json").read_text(encoding="utf-8")
    assert text.startswith('{\n  "request_id": "fd-9",\n  "env": "dev",')
    assert json.loads(text)["results"][0]["entity"] == "bronze.orders"


def test_a_work_copy_that_cant_all_be_removed_never_fails_a_finished_run(bundle, tmp_path, monkeypatch):
    """On Windows a file still open (an antivirus scan, a log) can stop the temp copy being removed."""
    b, stores = bundle

    def locked(path, ignore_errors=False, **kwargs):
        if not ignore_errors:
            raise PermissionError(f"{path} is in use")
        _REAL_RMTREE(path, ignore_errors=True)

    monkeypatch.setattr(fc.shutil, "rmtree", locked)
    out = fc.run_bundle(b, entities="bronze.orders", stores=stores, results_dir=tmp_path / "r")
    assert [r["status"] for r in out["results"]] == ["ok"]
