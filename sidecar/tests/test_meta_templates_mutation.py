"""Metadata project templates (meta/templates.py): what mutation testing (batch 19,
docs/quality/mutation-testing.md) showed no test pinned. The defaults of a new project, who owns the
sample key tables and source systems, and the TOML each entity template writes. Files under a
temporary folder only."""

from __future__ import annotations

import tomllib

import polars as pl
import pytest

from fabric_desktop.meta import keys as keys_mod
from fabric_desktop.meta import templates


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _entity(project_root, name):
    return tomllib.loads((project_root / "metadata" / "entities" / f"{name}.toml").read_text(encoding="utf-8"))


# ---- a new project


def test_a_new_project_has_the_sample_by_default(tmp_path):
    root = tmp_path / "p"
    templates.init_project(root)
    assert sorted(p.stem for p in (root / "metadata" / "entities").glob("*.toml")) == sorted(templates.SAMPLE_ENTITIES)
    assert (root / ".local/dev/LH_Bronze/Files/landing/orders/orders_2026-10-01.csv").is_file()


def test_a_project_goes_into_a_folder_that_already_exists(tmp_path):
    root = tmp_path / "p"
    (root / "metadata" / "entities").mkdir(parents=True)
    (root / "notes.txt").write_text("mine", encoding="utf-8")
    project = templates.init_project(root, "3", "Chris", False)
    assert project.root == root and (root / "layers.toml").is_file()
    assert (root / "notes.txt").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize("preset, sample", [("2", True), ("5", True), ("3", False)])
def test_only_the_three_layer_preset_with_sample_gets_the_sample(tmp_path, preset, sample):
    root = tmp_path / "p"
    templates.init_project(root, preset, "Chris", sample)
    assert list((root / "metadata" / "entities").glob("*.toml")) == []
    assert not (root / ".local").exists()
    assert keys_mod.load_key_tables(root) == ({}, [])
    assert keys_mod.load_source_systems(root) == ([], [])


@pytest.mark.parametrize("owner, expected", [("", "me"), ("Chris", "Chris")])
def test_the_sample_keys_and_source_systems_belong_to_the_owner(tmp_path, owner, expected):
    root = tmp_path / "p"
    templates.init_project(root, "3", owner, True)
    tables, errors = keys_mod.load_key_tables(root)
    assert errors == []
    assert {n: (t.spec.get("owner"), t.spec.get("description")) for n, t in tables.items()} == {
        "customer": (expected, "A customer, from any source"), "order": (expected, "A sales order")}
    systems, errors = keys_mod.load_source_systems(root)
    assert errors == []
    assert [(s.id, s.code, s.name, s.owner) for s in systems] == [
        (1, "CRM", "Customer export", expected), (2, "SHOP", "Online shop export", expected)]
    assert _entity(root, "bronze.orders")["owner"] == expected


# ---- new entities


@pytest.fixture
def project(tmp_path):
    return templates.init_project(tmp_path / "p", "3", "Chris", True)


def test_without_keys_the_first_column_is_the_key(project):
    sample = pl.DataFrame({"code": ["a"], "n": [1]})
    text, _ = templates.entity_text(project, "files", "bronze.things", "Chris", sample=sample)
    assert tomllib.loads(text)["contract"]["keys"] == ["code"]
    text, _ = templates.entity_text(project, "files", "bronze.things", "Chris", keys=["n"], sample=sample)
    assert tomllib.loads(text)["contract"]["keys"] == ["n"]


def test_a_landing_entity_reads_its_source_or_a_placeholder(project):
    text, _ = templates.entity_text(project, "landing", "bronze.things", "Chris")
    assert tomllib.loads(text)["source"] == {"landing": "<source>/things/*.csv"}
    text, _ = templates.entity_text(project, "landing", "bronze.things", "Chris", source="crm/things/*.parquet")
    assert tomllib.loads(text)["source"] == {"landing": "crm/things/*.parquet"}


@pytest.mark.parametrize("template", ["clean", "fact"])
def test_clean_and_fact_load_incrementally_on_the_upstream_watermark(project, template):
    text, _ = templates.entity_text(project, template, "silver.orders2", "Chris", source="bronze.orders")
    assert tomllib.loads(text)["load"] == {"mode": "incremental", "watermark": "modified_at"}


@pytest.mark.parametrize("template", ["clean", "fact"])
def test_clean_and_fact_load_in_full_without_a_watermark(project, template):
    text, _ = templates.entity_text(project, template, "silver.other", "Chris", source="nowhere.at_all")
    assert tomllib.loads(text)["load"] == {"mode": "full"}


def test_a_type_2_dimension_tracks_the_first_three_non_key_columns(project):
    text, _ = templates.entity_text(project, "dim2", "gold.dim_customer2", "Chris", source="silver.customers",
                                    keys=["customer_id"])
    scd = tomllib.loads(text)["scd"]
    assert scd == {"surrogate_key": "customer2_sk", "tracked": ["name", "segment", "city"], "type1": []}


def test_a_custom_step_is_owned_by_its_owner_or_me(project):
    text, _ = templates.entity_text(project, "custom", "gold.mine", "")
    assert tomllib.loads(text)["step"]["owner"] == "me"
    text, _ = templates.entity_text(project, "custom", "gold.mine", "Chris")
    assert tomllib.loads(text)["step"]["owner"] == "Chris"
