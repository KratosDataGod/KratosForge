"""spark.read.csv honours Spark's CSV options or refuses them with a message, never drops them (EA-013)."""

from __future__ import annotations

import pytest

from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import Kernel


def run(code):
    out = Kernel().run("from pyspark.sql.types import StructType, StructField, IntegerType, StringType\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


@pytest.fixture
def csv(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("id,code,amt\n1,007,NA\n2,010,3.5\n")
    return str(p)


def test_every_column_is_a_string_unless_inferschema(csv):
    status, text = run(f"print(spark.read.option('header', 'true').csv({csv!r}).dtypes)\n"
                       f"print(spark.read.csv({csv!r}, header=True, inferSchema=True).dtypes)")
    assert status == "ok", text
    plain, inferred = text.strip().splitlines()
    assert plain == "[('id', 'string'), ('code', 'string'), ('amt', 'string')]"
    assert "('id', 'bigint')" in inferred or "('id', 'int')" in inferred


def test_no_header_by_default(csv):
    status, text = run(f"print(spark.read.csv({csv!r}).columns, spark.read.csv({csv!r}).count())")
    assert status == "ok", text
    assert "3" in text.split()[-1]


def test_null_value_quote_and_comment(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("id;name;note\n#skipped;x;y\n1;'a;b';NA\n2;;z\n")
    status, text = run(f"r = spark.read.options(header=True, sep=';', quote=\"'\", comment='#', nullValue='NA')"
                       f".csv({str(p)!r})\nprint([tuple(x) for x in r.collect()])")
    assert status == "ok", text
    assert "[('1', 'a;b', None), ('2', None, 'z')]" in text


def test_modes_with_a_schema(csv):
    schema = "StructType([StructField('id', IntegerType()), StructField('code', IntegerType()), StructField('amt', IntegerType())])"
    status, text = run(f"s = {schema}\n"
                       f"print(spark.read.schema(s).option('header', True).csv({csv!r}).collect())\n"
                       f"print(spark.read.schema(s).option('header', True).option('mode', 'DROPMALFORMED').csv({csv!r}).collect())")
    assert status == "ok", text
    permissive, dropped = text.strip().splitlines()
    assert "amt=None" in permissive and permissive.count("Row(") == 2
    assert dropped == "[]"  # NA and 3.5 aren't integers, so both rows are malformed
    status, text = run(f"s = {schema}\nspark.read.schema(s).option('header', True).option('mode', 'FAILFAST').csv({csv!r}).collect()")
    assert status == "error", text


@pytest.mark.parametrize("option, words", [
    ("option('ignoreLeadingWhiteSpace', True)", "doesn't take the CSV option"),
    ("option('encoding', 'ISO-8859-1')", "UTF-8 only"),
    ("option('mode', 'FAILFAST')", "needs read.schema()"),
    ("option('mode', 'LENIENT')", "has no CSV mode"),
])
def test_options_this_pc_cannot_honour_are_refused(csv, option, words):
    status, text = run(f"spark.read.{option}.csv({csv!r})")
    assert status == "error" and words in text and "wasn't read" in text, text


def test_utf8_and_multiline_are_taken(csv):
    status, text = run(f"print(spark.read.options(encoding='UTF-8', multiLine=True, header=True).csv({csv!r}).count())")
    assert status == "ok" and text.strip() == "2", text


@pytest.mark.parametrize("source", ["df = spark.read.option('encoding', 'latin1').csv('Files/x.csv')",
                                    "df = spark.read.format('csv').option('mode', 'FAILFAST').load('Files/x.csv')",
                                    "df = spark.read.csv('Files/x.csv', samplingRatio=0.1)"])
def test_runs_on_check_blocks_what_the_reader_refuses(source):
    lh = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}
    local = compute_check([{"type": "code", "source": source, "meta": {}}], lh)["local"]
    assert local["status"] == "blocked", local
