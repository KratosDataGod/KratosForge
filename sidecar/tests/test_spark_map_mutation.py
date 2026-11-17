"""Mutation-testing follow-up for spark_map.py (docs/quality/mutation-testing.md, batch 40).

Pins what surviving mutants changed: the batch size setting (bad, zero and negative values fall
back to 10,000), the schema check, mapInArrow's empty result and type check, and applyInPandas'
groups (null keys kept, sorted keys, a clean index, a function whose signature can't be read)."""

from types import SimpleNamespace

import pytest

from fabric_desktop import spark_map
from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture
def kernel():
    return Kernel()


def run(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def error(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "error", out
    return str(out["outputs"])


SETUP = """
import pandas as pd
import pyarrow as pa
df = spark.createDataFrame([(2, "a", 2.0), (None, "b", None), (1, "c", 4.0), (2, "d", 1.0)], ["k", "s", "v"])
"""

SIZES = """
big = spark.range(10001)
def sizes(it):
    for b in it:
        yield pa.RecordBatch.from_pydict({"n": [b.num_rows]})
print(sorted((r.n for r in big.mapInArrow(sizes, "n long").collect()), reverse=True))
"""


# ---- batch size ---------------------------------------------------------------------------------

def test_the_default_batch_is_ten_thousand_rows(kernel):
    assert run(kernel, SETUP + SIZES) == "[10000, 1]"


@pytest.mark.parametrize("setting", ["'many'", "0", "-5"])
def test_a_bad_zero_or_negative_batch_setting_falls_back_to_ten_thousand(kernel, setting):
    code = SETUP + f"spark.conf.set('spark.sql.execution.arrow.maxRecordsPerBatch', {setting})\n" + SIZES
    assert run(kernel, code) == "[10000, 1]"


def test_a_batch_setting_of_one_is_used(kernel):
    code = SETUP + "spark.conf.set('spark.sql.execution.arrow.maxRecordsPerBatch', 1)\n" + """
def sizes(it):
    for b in it:
        yield pa.RecordBatch.from_pydict({"n": [b.num_rows]})
print([r.n for r in df.mapInArrow(sizes, "n long").collect()])
"""
    assert run(kernel, code) == "[1, 1, 1, 1]"


# ---- schema and type checks ----------------------------------------------------------------------

def test_a_schema_that_is_neither_ddl_nor_a_struct_is_refused(kernel):
    assert "schema is a StructType or a DDL string" in error(kernel, SETUP + "df.mapInPandas(lambda it: it, 5)")


def test_map_in_arrow_refuses_a_non_batch(kernel):
    out = error(kernel, SETUP + "df.mapInArrow(lambda it: (b.to_pandas() for b in it), 'k long').collect()")
    assert "mapInArrow's function must yield pyarrow RecordBatches, not DataFrame" in out


def test_map_in_arrow_with_nothing_yielded_is_empty_with_the_schema(kernel):
    out = run(kernel, SETUP + """
r = df.mapInArrow(lambda it: iter(()), "n long, s string")
print(r.columns, r.collect())
""")
    assert out == "['n', 's'] []"


def test_apply_in_pandas_refuses_a_non_frame(kernel):
    out = error(kernel, SETUP + "df.groupBy('k').applyInPandas(lambda p: 1, 'k long').collect()")
    assert "applyInPandas' function must return a pandas DataFrame, not int" in out


# ---- applyInPandas groups --------------------------------------------------------------------------

def test_null_keys_are_a_group_and_groups_come_in_key_order(kernel):
    out = run(kernel, SETUP + """
seen = []
def first(key, pdf):
    seen.append(key)
    return pd.DataFrame({"k": [key[0]], "n": [len(pdf)]})
rows = df.groupBy("k").applyInPandas(first, "k long, n long").collect()
print([tuple(None if x is None else int(x) for x in k) for k in seen], [tuple(r) for r in rows])
""")
    assert out == "[(1,), (2,), (None,)] [(1, 1), (2, 2), (None, 1)]"


def test_each_group_has_a_clean_index_and_only_the_frame_columns(kernel):
    out = run(kernel, SETUP + """
seen = []
def look(pdf):
    seen.append((list(pdf.columns), list(pdf.index)))
    return pdf
df.groupBy("k").applyInPandas(look, "k long, s string, v double").collect()
print(seen)
""")
    assert out == ("[(['k', 's', 'v'], [0]), (['k', 's', 'v'], [0, 1]), (['k', 's', 'v'], [0])]")


def test_a_function_taking_the_key_also_gets_a_clean_index(kernel):
    out = run(kernel, SETUP + """
seen = []
def look(key, pdf):
    seen.append((list(pdf.columns), list(pdf.index)))
    return pdf
df.groupBy("k").applyInPandas(look, "k long, s string, v double").collect()
print(seen)
""")
    assert out == ("[(['k', 's', 'v'], [0]), (['k', 's', 'v'], [0, 1]), (['k', 's', 'v'], [0])]")


def test_a_function_whose_signature_cannot_be_read_gets_only_the_frame(kernel):
    out = run(kernel, SETUP + """
class Count:
    __signature__ = "not a signature"  # inspect.signature raises TypeError
    def __call__(self, pdf):
        return pd.DataFrame({"n": [len(pdf)]})
print(sorted(r.n for r in df.groupBy("k").applyInPandas(Count(), "n long").collect()))
""")
    assert out == "[1, 1, 2]"


# ---- _grouping_names --------------------------------------------------------------------------------

def _grouped(cols):
    return SimpleNamespace(_grouping=SimpleNamespace(_cols=cols))


def test_grouping_names_from_strings_and_other_values():
    assert spark_map._grouping_names(_grouped(["a", None, 7, '"b"'])) == ["a", "7", '"b"']


def test_grouping_names_reads_a_single_value_and_the_cols_attribute():
    assert spark_map._grouping_names(SimpleNamespace(_grouping="a")) == ["a"]
    assert spark_map._grouping_names(SimpleNamespace(_grouping=SimpleNamespace(cols=['"x"']))) == ['"x"']


def test_grouping_with_no_columns_is_refused():
    with pytest.raises(ValueError, match="needs groupBy with at least one column"):
        spark_map._grouping_names(_grouped([None]))
