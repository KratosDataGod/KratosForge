"""A metadata project's run engine (meta/engine.py), the paths the main tests don't reach: column
types it can't read, casts from text and naive times, lookbacks that don't fit the watermark, and
hooks that return something other than a frame or read a table not loaded yet. All on local disk."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.meta import engine as eng
from fabric_desktop.meta.engine import CheckStop
from fabric_desktop.meta.project import GateError

from test_meta import DAY2, HOOKS, add_hook, add_shipments, append_entity, edit_entity, repo, write  # noqa: F401 - repo is a fixture


# ---- types and casts


def test_column_types_by_name():
    assert eng.polars_type(" Decimal(18, 2) ") == pl.Decimal(18, 2)
    with pytest.raises(ValueError, match="unknown column type 'money'"):
        eng.polars_type("money")


def test_text_becomes_a_boolean_and_a_naive_time_becomes_utc():
    df = pl.DataFrame({"flag": [" Yes", "0", "maybe", None],
                       "at": [dt.datetime(2026, 1, 1, 12), None, None, None]})
    out = df.select(eng._cast(df, "flag", pl.Boolean).alias("flag"),
                    eng._cast(df, "at", pl.Datetime("us", "UTC")).alias("at"))
    assert out["flag"].to_list() == [True, False, None, None]
    assert out["at"][0] == dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)


# ---- lookbacks


def _entity(lookback):
    return SimpleNamespace(spec={"load": {"lookback": lookback}})


@pytest.mark.parametrize("lookback, dtype, says", [
    ("2 hours", pl.Int64, 'use a whole number'),
    (3, pl.Date, 'use a duration such as "2 hours"'),
    (3, pl.String, "a lookback needs a date, datetime or number watermark"),
])
def test_a_lookback_that_doesnt_fit_the_watermark_stops_the_run(lookback, dtype, says):
    with pytest.raises(CheckStop, match=says):
        eng._wm_minus(_entity(lookback), 5, dtype)


def test_a_lookback_moves_a_number_or_a_date_back():
    assert eng._wm_minus(_entity(3), 10, pl.Int64) == 7
    assert eng._wm_minus(_entity("2 hours"), dt.date(2026, 1, 5), pl.Date) == dt.date(2026, 1, 4)
    assert eng._wm_minus(_entity(None), 10, pl.Int64) == 10


# ---- runs refused before anything is read


def test_a_run_of_an_entity_that_isnt_there_or_a_project_that_doesnt_save_is_refused(repo):  # noqa: F811
    with pytest.raises(KeyError, match="no entity named silver.nope"):
        repo.engine(DAY2).run("silver.nope")
    write(repo.root, "metadata/keys/broken.toml", "name = ")
    with pytest.raises(GateError, match="save gate"):
        repo.reload().engine(DAY2).run("silver.orders")


def test_a_link_with_nowhere_to_go_says_how_to_fix_it(repo):  # noqa: F811
    with pytest.raises(CheckStop, match="link crm can't be resolved here"):
        repo.engine(DAY2)._no_links("crm")


# ---- hooks


@pytest.mark.parametrize("code, ok, says", [
    (("import polars as pl\ndef allocate_freight(df, ctx):\n"
     "    return df.lazy().with_columns(pl.lit(0.0).alias('freight_alloc'))\n"), True, ""),
    ("def allocate_freight(df, ctx):\n    return df.to_dicts()\n", False, "must return a Polars DataFrame, not list"),
])
def test_a_hook_may_return_a_lazy_frame_but_nothing_else(repo, code, ok, says):  # noqa: F811
    add_shipments(repo)
    repo.reload().engine(DAY2).run_many(["bronze.shipments", "silver.shipments"])
    write(repo.root, "transforms/orders.py", code)
    add_hook(repo, HOOKS)
    write(repo.root, "tests/test_orders.py", "def test_allocate_freight():\n    pass\n")
    res = repo.reload().engine(DAY2).run("silver.orders", backfill="full")
    assert (res.status == "ok") if ok else (res.status == "failed" and says in res.message), res.message


def test_a_hook_reading_a_table_not_loaded_yet_says_so(repo):  # noqa: F811
    add_shipments(repo)
    write(repo.root, "transforms/orders.py",
          "def allocate_freight(df, ctx):\n    ctx.read_table('silver.shipments')\n    return df\n")
    add_hook(repo, HOOKS)
    write(repo.root, "tests/test_orders.py", "def test_allocate_freight():\n    pass\n")
    res = repo.reload().engine(DAY2).run("silver.orders", backfill="full")
    assert res.status == "failed" and "silver.shipments has not been loaded in dev yet" in res.message, res.message


def test_a_hook_before_write_runs_last(repo):  # noqa: F811
    write(repo.root, "transforms/orders.py", "import polars as pl\ndef allocate_freight(df, ctx):\n"
                                             "    return df.with_columns(pl.lit(1.5).alias('freight_alloc'))\n")
    add_hook(repo, HOOKS.replace("after_clean", "before_write").replace('reads = ["silver.shipments"]\n', ""))
    write(repo.root, "tests/test_orders.py", "def test_allocate_freight():\n    pass\n")
    res = repo.reload().engine(DAY2).run("silver.orders", backfill="full")
    assert res.status == "ok" and any(s.name == "hook before_write" for s in res.steps), res.message
    assert set(repo.table("silver.orders")["freight_alloc"].to_list()) == {1.5}


# ---- what a run finds


def test_a_range_check_quarantines_values_below_and_above(repo):  # noqa: F811
    before = repo.engine(DAY2).run("silver.orders", backfill="full").quarantined
    append_entity(repo.root, "silver.orders", '[[check]]\nname = "sane_amount"\ncolumn = "amount"\n'
                                              'range = { min = 0, max = 50 }\non_fail = "quarantine"\n')
    res = repo.reload().engine(DAY2).run("silver.orders", backfill="full")
    assert res.status == "ok", res.message
    assert next(s for s in res.steps if s.name == "checks").detail.startswith("4 checks")
    assert res.quarantined > before


def test_files_that_match_nothing_stop_the_run(repo):  # noqa: F811
    edit_entity(repo.root, "bronze.customers", 'files = "landing/customers/*.csv"', 'files = "landing/nobody/*.csv"')
    res = repo.reload().engine(DAY2).run("bronze.customers")
    assert res.status == "failed" and "no files match landing/nobody/*.csv" in res.message
