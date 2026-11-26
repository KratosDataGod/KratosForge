"""``[source] rename`` on an entity read from another entity: the save gate, the run, lineage and change
impact agree on the names a rename leaves. Also the gate's checks of the watermark and the [scd] column
lists, which a run otherwise trips over (or silently ignores). Real Delta tables in a temp folder."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from fabric_desktop.meta import Engine, GateError, LocalStore, validate
from fabric_desktop.meta import engine as engine_mod
from fabric_desktop.meta import lineage
from fabric_desktop.meta.change_impact import column_impact
from fabric_desktop.meta.project import source_columns
from fabric_desktop.meta.templates import init_project

DAY1, DAY2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.p = init_project(root, "3", "Chris", True)
        self.dev = LocalStore(root / ".local" / "dev", "dev")

    def edit(self, name: str, old: str, new: str) -> Repo:
        f = self.root / "metadata" / "entities" / f"{name}.toml"
        text = f.read_text(encoding="utf-8")
        assert old in text, f"{old!r} not in {name}"
        f.write_text(text.replace(old, new, 1), encoding="utf-8")
        self.p.reload()
        return self

    def errors(self) -> list[str]:
        return validate(self.p, DAY2)

    def run(self, name: str, day: dt.date = DAY2, backfill: str | None = None):
        return Engine(self.p, self.dev, day).run(name, backfill=backfill)

    def table(self, name: str) -> pl.DataFrame:
        return Engine(self.p, self.dev, DAY2).read_entity(name)


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "project")
    results = Engine(r.p, r.dev, DAY1).run_many()
    assert [x.status for x in results] == ["ok"] * 6, [(x.entity, x.message) for x in results]
    return r


FACT_SOURCE = '[source]\nentity = "silver.orders"'


def rename_in_fact(repo: Repo, mapping: str) -> Repo:
    return repo.edit("gold.fact_sales", FACT_SOURCE, f"{FACT_SOURCE}\nrename = {{ {mapping} }}")


# ---- a rename to a contract column


def test_a_rename_to_a_contract_column_passes_the_gate_and_the_run_writes_the_new_name(repo):
    rename_in_fact(repo, 'net_amount = "net_sales"')
    repo.edit("gold.fact_sales", 'net_amount = "float64"', 'net_sales = "float64"')
    assert repo.errors() == []
    assert "net_sales" in source_columns(repo.p, repo.p.entities["gold.fact_sales"])
    assert "net_amount" not in source_columns(repo.p, repo.p.entities["gold.fact_sales"])
    res = repo.run("gold.fact_sales", backfill="full")  # gold's drift is strict: the new name isn't "new"
    assert res.status == "ok", res.message
    fact = repo.table("gold.fact_sales")
    assert "net_sales" in fact.columns  # (the old column stays in the table, as for any contract change)
    assert fact.filter(pl.col("order_id") == 100)["net_sales"].item() == pytest.approx(45.0)
    # lineage and change impact read the same names
    rec = lineage.entity_columns(repo.p, "gold.fact_sales")["net_sales"]
    assert (rec["how"], rec["label"], rec["source_column"]) == ("source", "declared", "net_amount")
    assert rec["inputs"] == [{"entity": "silver.orders", "column": "net_amount"}]
    broken = column_impact(repo.p, "silver.orders", "net_amount", "drop")
    assert any(i["name"] == "gold.fact_sales" and i["severity"] == "breaks" for i in broken["items"])


def test_dropping_a_renamed_column_from_the_contract_is_still_read_from_the_source(repo):
    rename_in_fact(repo, 'net_amount = "net_sales"')
    repo.edit("gold.fact_sales", 'net_amount = "float64"', 'net_sales = "float64"')
    e = repo.p.entities["gold.fact_sales"]
    from fabric_desktop.meta.change_impact import _still_read_from
    assert _still_read_from(repo.p, e, "net_sales") == "silver.orders"
    assert _still_read_from(repo.p, e, "net_amount") is None


# ---- a rename of a column the source doesn't have


def test_a_rename_of_a_column_the_source_does_not_have_is_refused_by_name(repo):
    rename_in_fact(repo, 'net_amt = "net_sales"')
    repo.edit("gold.fact_sales", 'net_amount = "float64"', 'net_sales = "float64"')
    errs = repo.errors()
    assert ("gold.fact_sales: source rename net_amt: silver.orders has no column net_amt, so nothing is "
            "renamed; use a column silver.orders writes, or take net_amt out of [source] rename") in errs
    assert "gold.fact_sales: contract column net_sales is not in the source and nothing derives it" in errs
    with pytest.raises(GateError):
        repo.run("gold.fact_sales", backfill="full")


# ---- a rename onto a name the source already has


def test_a_rename_onto_a_name_the_source_has_is_refused_because_the_run_fails_on_it(repo, monkeypatch):
    rename_in_fact(repo, 'modified_at = "order_date"')
    errs = repo.errors()
    assert ("gold.fact_sales: source rename gives two columns the name order_date (modified_at, and "
            "order_date itself), and a table can't hold both; rename order_date too, or pick another new name"
            ) in errs
    # Past the gate, the run fails on the same clash: the gate refuses only what the run can't do.
    monkeypatch.setattr(engine_mod, "validate", lambda *a, **k: [])
    res = repo.run("gold.fact_sales", backfill="full")
    assert res.status == "failed" and "duplicate" in res.message.lower()


def test_two_renames_onto_one_name_are_refused(repo):
    rename_in_fact(repo, 'modified_at = "day", order_date = "day"')
    assert ("gold.fact_sales: source rename gives two columns the name day (modified_at and order_date), and "
            "a table can't hold both; rename one of them to another name") in repo.errors()


def test_a_swap_of_two_names_is_no_clash_and_the_run_swaps_them(repo):
    rename_in_fact(repo, 'modified_at = "order_date", order_date = "modified_at"')
    assert repo.errors() == []
    assert repo.run("gold.fact_sales", backfill="full").status == "ok"
    silver = repo.table("silver.orders").sort("order_id")
    fact = repo.table("gold.fact_sales").sort("order_id")
    assert fact["order_date"].to_list() == silver.filter(pl.col("order_id").is_in(fact["order_id"].implode()))[
        "modified_at"].to_list()


# ---- chained entities, each renaming


def test_chained_entities_that_each_rename_pass_the_gate_and_run(tmp_path):
    r = Repo(tmp_path / "project")
    # silver.orders calls amount gross, and its watermark changed_at; gold.fact_sales calls net_amount net_sales
    # and maps changed_at back to modified_at.
    r.edit("silver.orders", '[source]\nentity = "bronze.orders"',
           '[source]\nentity = "bronze.orders"\nrename = { amount = "gross", modified_at = "changed_at" }')
    r.edit("silver.orders", 'amount = "float64"', 'gross = "float64"')
    r.edit("silver.orders", 'modified_at = "date"', 'changed_at = "date"')
    r.edit("silver.orders", 'watermark = "modified_at"', 'watermark = "changed_at"')
    r.edit("silver.orders", 'dedupe_keep = "newest:modified_at"', 'dedupe_keep = "newest:changed_at"')
    r.edit("silver.orders", "amount - coalesce", "gross - coalesce")
    r.edit("silver.orders", "or amount < 0", "or gross < 0")
    rename_in_fact(r, 'net_amount = "net_sales", changed_at = "modified_at"')
    r.edit("gold.fact_sales", 'net_amount = "float64"', 'net_sales = "float64"')
    assert r.errors() == []
    results = Engine(r.p, r.dev, DAY1).run_many()
    assert [x.status for x in results] == ["ok"] * 6, [(x.entity, x.message) for x in results]
    assert "gross" in r.table("silver.orders").columns
    fact = r.table("gold.fact_sales")
    assert {"net_sales", "modified_at"} <= set(fact.columns)
    # the second run is incremental on the renamed watermark, in both tables
    again = r.run("silver.orders")
    assert again.status == "ok" and "changed_at after 2026-10-01" in again.steps[0].detail
    up = lineage.upstream(r.p, "gold.fact_sales", "net_sales")
    assert {"entity": "silver.orders", "column": "net_amount"} in [{"entity": u["entity"], "column": u["column"]}
                                                                    for u in up]
    rec = lineage.entity_columns(r.p, "silver.orders")["gross"]
    assert rec["inputs"] == [{"entity": "bronze.orders", "column": "amount"}]
    # a second rename upstream breaks the one below it, and the gate says so
    r.edit("silver.orders", 'rename = { amount = "gross", modified_at = "changed_at" }',
           'rename = { amount = "gross", modified_at = "changed" }')
    r.edit("silver.orders", 'changed_at = "date"', 'changed = "date"')
    r.edit("silver.orders", 'watermark = "changed_at"', 'watermark = "changed"')
    r.edit("silver.orders", 'dedupe_keep = "newest:changed_at"', 'dedupe_keep = "newest:changed"')
    assert ("gold.fact_sales: source rename changed_at: silver.orders has no column changed_at, so nothing is "
            "renamed; use a column silver.orders writes, or take changed_at out of [source] rename") in r.errors()


# ---- file sources: unchanged


def own(errors: list[str], name: str) -> list[str]:
    return [x for x in errors if x.startswith(f"{name}:")]


def test_a_file_source_rename_is_checked_as_before(repo):
    repo.edit("bronze.orders", '[source]\nfiles = "landing/orders/*.csv"',
              '[source]\nfiles = "landing/orders/*.csv"\nrename = { amount = "gross" }')
    repo.edit("bronze.orders", 'amount = "float64"', 'gross = "float64"')
    # silver.orders, read from it, maps the name back: an entity source's rename next to a file source's
    repo.edit("silver.orders", '[source]\nentity = "bronze.orders"',
              '[source]\nentity = "bronze.orders"\nrename = { gross = "amount" }')
    e = repo.p.entities["bronze.orders"]
    assert source_columns(repo.p, e) == set(e.columns)  # a file's columns aren't known before a run
    assert repo.errors() == []
    res = repo.run("bronze.orders", backfill="full")
    assert res.status == "ok", res.message
    assert "renamed amount to gross" in res.steps[0].detail
    assert "gross" in repo.table("bronze.orders").columns
    assert repo.run("silver.orders", backfill="full").status == "ok"
    assert "amount" in repo.table("silver.orders").columns
    # a rename of a column the files may not have isn't refused on save: only the run sees the files
    repo.edit("bronze.orders", 'rename = { amount = "gross" }', 'rename = { amount = "gross", nope = "x" }')
    assert own(repo.errors(), "bronze.orders") == []


# ---- the watermark and the [scd] column lists


def test_a_watermark_that_is_not_a_contract_column_is_refused(repo):
    repo.edit("silver.orders", 'watermark = "modified_at"', 'watermark = "modified"')
    assert ("silver.orders: load watermark modified is not a contract column, so no run saves it and every "
            "run reads all the rows again; name a contract column the source gives, like a modified date"
            ) in repo.errors()


def test_a_watermark_made_by_the_entity_is_refused(repo):
    repo.edit("silver.orders", 'watermark = "modified_at"', 'watermark = "net_amount"')
    assert ("silver.orders: load watermark net_amount is not a column the source gives (silver.orders makes "
            "it), so a run can't pick out the new rows and reads them all again; name a contract column the "
            "source gives, like a modified date") in repo.errors()


def test_a_watermark_that_is_not_text_is_refused(repo):
    repo.edit("silver.orders", 'watermark = "modified_at"', "watermark = 5")
    assert 'silver.orders: load watermark names one column, like watermark = "modified_at"' in repo.errors()


@pytest.mark.parametrize("field, old, new", [("tracked", '["segment", "city"]', '["segment", "citty"]'),
                                             ("type1", '["email", "name"]', '["email", "nam"]')])
def test_scd_column_lists_name_columns_the_dimension_writes(repo, field, old, new):
    repo.edit("gold.dim_customer", f"{field} = {old}", f"{field} = {new}")
    bad = "citty" if field == "tracked" else "nam"
    assert (f"gold.dim_customer: [scd] {field} lists {bad}, which is not a column gold.dim_customer writes; "
            f"add it to [contract.columns] or take it out of {field}") in repo.errors()


def test_scd_column_lists_are_lists(repo):
    repo.edit("gold.dim_customer", 'type1 = ["email", "name"]', 'type1 = "email"')
    assert ('gold.dim_customer: [scd] type1 takes a list of contract columns, like type1 = ["email", "name"]'
            in repo.errors())
