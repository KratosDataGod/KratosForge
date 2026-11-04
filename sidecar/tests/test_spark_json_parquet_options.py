"""spark.read.json and spark.read.parquet honour Spark's options or refuse them by name, never drop
them (#1669, following EA-013's CSV fix)."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop.fabric_run import compute_check
from fabric_desktop.notebook import Kernel

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}


def run(code):
    out = Kernel().run("from pyspark.sql.types import StructType, StructField, LongType, StringType\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


@pytest.fixture
def lines(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"a": 1, "b": "x"}\n{"a": 2\n{"a": 3, "b": "z"}\n')
    return str(p)


@pytest.fixture
def document(tmp_path):
    p = tmp_path / "doc.json"
    p.write_text('[\n  {"a": 1},\n  {"a": 2}\n]\n')
    return str(p)


@pytest.fixture
def parts(tmp_path):
    pq.write_table(pa.table({"a": [1]}), tmp_path / "p1.parquet")
    pq.write_table(pa.table({"a": [2], "b": ["y"]}), tmp_path / "p2.parquet")
    return str(tmp_path / "p1.parquet"), str(tmp_path / "p2.parquet")


def test_json_permissive_reads_a_malformed_line_as_nulls(lines):
    status, text = run(f"print([tuple(r) for r in spark.read.option('mode', 'PERMISSIVE').json({lines!r}).collect()])")
    assert status == "ok", text
    assert "[(1, 'x'), (None, None), (3, 'z')]" in text


def test_json_failfast_stops_at_a_malformed_line(lines):
    status, text = run(f"spark.read.json({lines!r}, mode='FAILFAST').collect()")
    assert status == "error" and "Malformed" in text, text


def test_json_multiline_true_reads_one_document_per_file(document):
    status, text = run(f"print(spark.read.option('multiLine', True).json({document!r}).count())")
    assert status == "ok" and text.strip() == "2", text


def test_json_multiline_false_reads_json_lines_only(document):
    status, text = run(f"spark.read.json({document!r}, multiLine=False).collect()")
    assert status == "error", text


def test_json_utf8_is_taken_and_a_schema_keyword_is_read_schema(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text('{"a": 1, "b": "x"}\n')
    status, text = run("s = StructType([StructField('b', StringType()), StructField('a', LongType())])\n"
                       f"print(spark.read.json({str(p)!r}, schema=s, encoding='UTF-8').dtypes)")
    assert status == "ok", text
    assert "[('b', 'string'), ('a', 'bigint')]" in text


def test_parquet_merge_schema_reads_every_files_columns(parts):
    status, text = run(f"print(sorted(spark.read.option('mergeSchema', 'true').parquet(*{parts!r}).columns))")
    assert status == "ok" and "['a', 'b']" in text, text


def test_parquet_without_merge_schema_keeps_one_files_columns(parts):
    status, text = run(f"print(spark.read.parquet(*{parts!r}, mergeSchema=False).columns)")
    assert status == "ok" and "['a']" in text, text


def test_parquet_corrected_rebase_mode_is_taken(parts):
    status, text = run(f"print(spark.read.option('datetimeRebaseMode', 'CORRECTED').parquet({parts[0]!r}).count())")
    assert status == "ok" and text.strip() == "1", text


def test_load_goes_through_the_same_options(parts, lines):
    status, text = run(f"print(sorted(spark.read.format('parquet').option('mergeSchema', True).load({parts[0]!r}).columns))\n"
                       f"spark.read.format('json').option('dateFormat', 'yyyy').load({lines!r})")
    assert status == "error" and "['a']" in text and "dateFormat" in text, text


@pytest.mark.parametrize("call, words", [
    ("option('primitivesAsString', True).json(J)", "doesn't take the JSON option 'primitivesAsString'"),
    ("option('encoding', 'UTF-16').json(J)", "UTF-8 only"),
    ("option('mode', 'DROPMALFORMED').json(J)", "DROPMALFORMED"),
    ("option('mode', 'LENIENT').json(J)", "has no JSON mode"),
    ("options(multiLine=True, mode='PERMISSIVE').json(J)", "PERMISSIVE with multiLine"),
    ("json(J, allowComments=True)", "allowComments"),
    ("option('recursiveFileLookup', True).parquet(P)", "doesn't take the Parquet option 'recursiveFileLookup'"),
    ("option('datetimeRebaseMode', 'LEGACY').parquet(P)", "CORRECTED"),
    ("parquet(P, int96RebaseMode='EXCEPTION')", "int96RebaseMode"),
    ("option('header', True).parquet(P)", "Parquet option 'header'"),
])
def test_options_this_pc_cannot_honour_are_refused(lines, parts, call, words):
    status, text = run(f"J, P = {lines!r}, {parts[0]!r}\nspark.read.{call}")
    assert status == "error" and words in text and "wasn't read" in text, text


@pytest.mark.parametrize("source", [
    "df = spark.read.option('primitivesAsString', 'true').json('Files/x.json')",
    "df = spark.read.json('Files/x.json', mode='DROPMALFORMED')",
    "df = spark.read.format('json').option('dateFormat', 'yyyy').load('Files/x.json')",
    "df = spark.read.option('pathGlobFilter', '*.parquet').parquet('Files/p')",
    "df = spark.read.format('parquet').option('datetimeRebaseMode', 'LEGACY').load('Files/p')",
])
def test_runs_on_check_blocks_what_the_reader_refuses(source):
    local = compute_check([{"type": "code", "source": source, "meta": {}}], LH)["local"]
    assert local["status"] == "blocked", local


@pytest.mark.parametrize("source", [
    "df = spark.read.json('Files/x.json', multiLine=True, mode='FAILFAST')",
    "df = spark.read.option('mergeSchema', 'true').parquet('Files/p')",
    "df = spark.read.format('parquet').option('datetimeRebaseMode', 'CORRECTED').load('Files/p')",
])
def test_runs_on_check_passes_what_the_reader_takes(source):
    local = compute_check([{"type": "code", "source": source, "meta": {}}], LH)["local"]
    assert local["status"] != "blocked", local
