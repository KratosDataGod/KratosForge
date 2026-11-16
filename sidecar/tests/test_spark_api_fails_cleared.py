"""WR-304: the last Fails rows of the Spark DataFrame API list (docs/spark-dataframe-api.md) run, or
stop with a message saying why, in a notebook on this PC."""

import pytest

from fabric_desktop import lakehouse, onelake, publish, spark_api_coverage
from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import Kernel

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", None)


def run(code):
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


PEOPLE = "df = spark.createDataFrame([(14, 'Tom'), (23, 'Alice'), (16, 'Bob')], ['age', 'name'])\n"


def test_a_sort_by_an_alias_the_select_left_out_sorts_as_spark_does():
    status, text = run(PEOPLE + "from pyspark.sql.functions import col, desc\n"
                       "a, b = df.alias('a'), df.alias('b')\n"
                       "j = a.join(b, col('a.name') == col('b.name'), 'inner')\n"
                       "print([tuple(r) for r in j.select('a.name', 'b.age').sort(desc('a.name')).collect()])\n"
                       "print([tuple(r) for r in j.select('a.name', 'b.age').orderBy('a.age').collect()])")
    assert status == "ok", text
    assert text.splitlines() == ["[('Tom', 14), ('Bob', 16), ('Alice', 23)]",
                                 "[('Tom', 14), ('Bob', 16), ('Alice', 23)]"]


def test_a_sort_by_a_column_nothing_has_still_stops():
    status, text = run(PEOPLE + "df.select('name').sort('nope').collect()")
    assert status == "error" and "nope" in text, text


def test_column_alias_keeps_metadata_in_the_schema():
    status, text = run(PEOPLE + "out = df.select(df.age.alias('age3', metadata={'max': 99}), 'name')\n"
                       "print(out.schema['age3'].metadata, out.schema['name'].metadata)\n"
                       "print(df.select(df.age.alias('age2')).schema['age2'].metadata)")
    assert status == "ok", text
    assert text.splitlines() == ["{'max': 99} {}", "{}"]
    status, text = run(PEOPLE + "df.age.alias('x', metadata=5)")
    assert status == "error" and "metadata is a dict" in text


def test_a_rate_stream_is_refused_by_name_before_its_missing_path():
    status, text = run("spark.readStream.format('rate').load()")
    assert status == "error" and "Streaming from 'rate' runs in Fabric" in text, text


def test_stream_state_imports_and_apply_in_pandas_with_state_refuses():
    status, text = run(PEOPLE + "from pyspark.sql.streaming.state import GroupState, GroupStateTimeout\n"
                       "print(GroupStateTimeout.NoTimeout, GroupState.NO_TIMESTAMP)\n"
                       "df.groupBy('name').applyInPandasWithState(lambda k, it, s: it, 'name string', 'n long', "
                       "'append', GroupStateTimeout.NoTimeout)")
    assert status == "error" and text.startswith("NoTimeout -1"), text
    assert "applyInPandasWithState keeps each group's state" in text


def test_input_file_name_imports_and_refuses_when_called():
    status, text = run("from pyspark.sql.functions import input_file_name\nprint('imported')\ninput_file_name()")
    assert status == "error" and text.startswith("imported") and "input_file_name() names the file" in text, text


@pytest.mark.parametrize("call, words", [("bucketBy(2, 'name')", "Delta tables have no buckets"),
                                         ("sortBy('age')", "sortBy only goes with bucketBy")])
def test_bucketing_a_table_is_refused_as_in_fabric(call, words):
    status, text = run(PEOPLE + f"df.write.{call}.saveAsTable('dbo.t')")
    assert status == "error" and words in text and "this PC refuses" in text, text


@pytest.mark.parametrize("source, words", [
    ("df = spark.read.parquet('Files/x').withColumn('f', input_file_name())", "input_file_name()"),
    ("df.groupBy('k').applyInPandasWithState(f, 'a long', 'n long', 'append', 'NoTimeout')", "applyInPandasWithState"),
])
def test_the_runs_on_check_blocks_the_new_refusals(source, words):
    local = compute_check([{"type": "code", "source": source, "meta": {}}], LH)["local"]
    assert local["status"] == "blocked" and words in local["reasons"][0]["text"], local


def test_the_coverage_run_attaches_a_scratch_lakehouse_and_puts_everything_back():
    before = (lakehouse._attached, onelake.TableRef.uri, lakehouse.storage_options, publish.storage_options)
    with spark_api_coverage._scratch_lakehouse():
        lh = lakehouse._attached
        assert lh is not None and lh.name == "Scratch" and lh.work_dir.is_dir()
        assert onelake.TableRef.uri is not before[1] and lakehouse.storage_options("tok") == {}
        work = lh.work_dir
    assert (lakehouse._attached, onelake.TableRef.uri, lakehouse.storage_options, publish.storage_options) == before
    assert not work.exists()
