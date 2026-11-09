"""WR-301 (#1669): every Spark read option for JSON, CSV and Parquet is honoured or refused by name, and
docs/spark-read-options.md says which, up to date."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_desktop import spark_local, spark_read_options
from fabric_desktop.notebook import Kernel

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
    """Through a notebook's own spark.read, before any file is opened."""
    names = [r["name"] for r in rows if r["format"] == fmt and r["status"] == "refused"]
    path = (tmp_path / f"none.{fmt}").as_posix()
    k = Kernel()
    out = k.run("stopped = {}\n"
                f"for name in {names!r}:\n"
                "    try:\n"
                f"        spark.read.format({fmt!r}).option(name, 'x').load('file:{path}')\n"
                "    except NotImplementedError as exc:\n"
                "        stopped[name] = str(exc)\n")
    assert out["status"] == "ok", out
    stopped = k.namespace["stopped"]
    assert sorted(stopped) == sorted(names)
    assert all(name in why and "wasn't read" in why for name, why in stopped.items())
