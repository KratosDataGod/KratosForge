"""WR-301 (#1669): every Spark read option for JSON, CSV and Parquet is honoured or refused by name, and
docs/spark-read-options.md says which, up to date."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import spark_local, spark_read_options

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return spark_read_options.rows()


def test_the_docs_page_is_up_to_date(rows):
    page = (ROOT / "docs" / "spark-read-options.md").read_text(encoding="utf-8")
    assert page == spark_read_options.markdown(rows), (
        "docs/spark-read-options.md is stale: run `python -m fabric_desktop.spark_read_options` in sidecar/ "
        "and commit it")


def test_every_option_the_reader_takes_is_one_of_sparks():
    data = spark_read_options.load()
    for fmt, takes in spark_local._TAKES.items():
        spark = {n.lower() for n in data[fmt]} | set(data["aliases"].get(fmt, {}))
        assert [n for n in takes if n.lower() not in spark] == [], fmt


def test_each_option_has_one_status_and_both_kinds_appear(rows):
    assert {r["status"] for r in rows} == {"honoured", "refused"}
    assert len({(r["format"], r["name"].lower()) for r in rows}) == len(rows)


@pytest.mark.parametrize("fmt", spark_read_options.FORMATS)
def test_a_refused_option_stops_the_read_and_names_itself(rows, fmt, tmp_path):
    reader = spark_local.LakehouseReader.__new__(spark_local.LakehouseReader)
    for r in (r for r in rows if r["format"] == fmt and r["status"] == "refused"):
        reader._options, reader._names = {r["name"].lower(): "x"}, {r["name"].lower(): r["name"]}
        with pytest.raises(NotImplementedError, match=r["name"]):
            reader._refusal(fmt)
