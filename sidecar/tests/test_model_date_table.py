"""A dimension marked as the semantic model's date table ([model] date_column): its Unknown member gets a
placeholder date instead of a blank one, and Update model marks the table with that column as its key."""

import datetime as dt
import json
import textwrap
from pathlib import Path

import polars as pl
from deltalake import write_deltalake

from fabric_desktop import semantic
from fabric_desktop.meta import Engine, LocalStore, Project
from fabric_desktop.meta.engine import UNKNOWN_DATE, unknown_member
from fabric_desktop.meta.project import validate
from fabric_desktop.meta.templates import init_project

DAY1, DAY2, DAY3 = dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3)


class Dates:
    """A calendar source feeding a Type 1 date dimension."""

    def __init__(self, tmp: Path, model: str = 'date_column = "day"', day_type: str = "date", mode: str = "scd1"):
        self.root, self.src = tmp / "project", tmp / "calendar"
        init_project(self.root, "3", "Chris", sample=False)
        self.write(model, day_type, mode)
        self.store = LocalStore(self.root / ".local" / "dev", "dev")

    def write(self, model: str, day_type: str = "date", mode: str = "scd1") -> None:
        text = f"""
            name = "gold.dim_date"
            owner = "Chris"
            [load]
            mode = "{mode}"
            [scd]
            surrogate_key = "date_sk"
            tracked = ["month"]
            [source]
            table = {json.dumps(self.src.as_posix())}
            [contract]
            keys = ["date_id"]
            [contract.columns]
            date_sk = "int64"
            date_id = "int64"
            day = "{day_type}"
            month = "string"
            [model]
            {model}
        """
        p = self.root / "metadata" / "entities" / "gold.dim_date.toml"
        p.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")

    def load(self, days: list[dt.date], run_day: dt.date):
        df = pl.DataFrame({"date_id": [int(d.strftime("%Y%m%d")) for d in days], "day": days,
                           "month": [d.strftime("%B") for d in days]})
        write_deltalake(str(self.src), df.to_arrow(), mode="overwrite")
        res = Engine(Project(self.root), self.store, run_day).run("gold.dim_date")
        assert res.status == "ok", res.message
        return next(s.detail for s in res.steps if s.name == "write")

    def unknown(self):
        df = Engine(Project(self.root), self.store).read_entity("gold.dim_date")
        rows = df.filter(pl.col("date_sk") == -1)
        assert rows.height == 1
        return rows.row(0, named=True)


def _errors(tmp_path, **kw):
    d = Dates(tmp_path, **kw)
    return [x for x in validate(Project(d.root)) if "gold.dim_date" in x]


def test_date_column_is_checked(tmp_path):
    assert _errors(tmp_path / "ok") == []
    assert "must be a contract column of type date" in " ".join(_errors(tmp_path / "type", day_type="string"))
    assert "must be a contract column" in " ".join(_errors(tmp_path / "missing", model='date_column = "nope"'))
    assert "names a date column" in " ".join(_errors(tmp_path / "kind", model="date_column = 3"))
    assert "is for a dimension" in " ".join(_errors(tmp_path / "fact", mode="incremental"))


def test_the_unknown_member_gets_the_placeholder_date(tmp_path):
    d = Dates(tmp_path)
    d.load([DAY1, DAY2], DAY1)
    assert d.unknown()["day"] == UNKNOWN_DATE == dt.date(1899, 12, 30)
    assert d.unknown()["month"] == "Unknown"
    e = Project(d.root).entities["gold.dim_date"]
    stamp = unknown_member(pl.Schema({"date_sk": pl.Int64, "day": pl.Datetime("us")}), e)
    assert stamp["day"].to_list() == [dt.datetime(1899, 12, 30)]


def test_a_dimension_without_the_setting_keeps_a_blank_date(tmp_path):
    d = Dates(tmp_path, model="")
    d.load([DAY1], DAY1)
    assert d.unknown()["day"] is None


def test_an_existing_table_is_rewritten_once_then_merges_again(tmp_path):
    d = Dates(tmp_path, model="")
    d.load([DAY1], DAY1)
    assert d.unknown()["day"] is None
    d.write('date_column = "day"')
    assert "rewrote the table once to give the Unknown member the date 1899-12-30" in d.load([DAY2], DAY2)
    assert d.unknown()["day"] == UNKNOWN_DATE
    assert "Delta MERGE" in d.load([DAY3], DAY3)
    assert d.unknown()["day"] == UNKNOWN_DATE


def test_type2_date_dimension(tmp_path):
    d = Dates(tmp_path, mode="scd2")
    d.load([DAY1], DAY1)
    assert d.unknown()["day"] == UNKNOWN_DATE


def _tmdl(t):
    return semantic._table_tmdl(t, "x")


def test_update_model_marks_the_date_table():
    t = semantic.ModelTable(name="dim_date", entity="gold.dim_date", schema="gold", table="dim_date",
                            columns=[("date_sk", "long"), ("day", "date"), ("month", "string")], keys=["date_id"],
                            surrogate_key="date_sk", is_dimension=True, date_column="day")
    text = _tmdl(t)
    assert "\tdataCategory: Time\n" in text
    day = text[text.index("column day"):]
    day = day[:day.index("\n\n")]
    assert "\t\tisKey" in day
    assert text.count("isKey") == 1
    # The model's tables are still found by their lineage tag.
    assert semantic.table_names({"definition/tables/dim_date.tmdl": text}, ["gold.dim_date"]) == {"gold.dim_date": "dim_date"}
    # A column that isn't a date in the table marks nothing.
    t.columns = [("date_sk", "long"), ("day", "string")]
    assert "dataCategory" not in _tmdl(t) and "isKey" not in _tmdl(t)
    t.date_column = None
    t.columns = [("date_sk", "long"), ("day", "date")]
    assert "dataCategory" not in _tmdl(t)
