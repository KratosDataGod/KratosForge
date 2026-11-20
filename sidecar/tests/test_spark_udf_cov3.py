"""spark_udf.py's helpers that the kernel tests don't reach: values that don't fit, arguments that aren't
columns, the iterator pandas_udf over Arrow arrays, odd pandas/numpy-like values and Spark 2's GROUPED_AGG.
Pure functions; no Spark session is started."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from fabric_desktop import spark_udf as su


def T():
    return su._types()


def test_a_float_column_takes_only_floats():
    assert su._fits(1.5, T().DoubleType()) == 1.5
    assert su._fits(2, T().DoubleType()) is None
    assert su._fits("1.5", T().FloatType()) is None


def test_a_struct_takes_a_dict_or_a_row_of_the_right_length_and_nothing_else():
    st = T().StructType([T().StructField("a", T().IntegerType()), T().StructField("b", T().StringType())])
    assert su._fits({"a": 1, "b": "x"}, st) == {"a": 1, "b": "x"}
    assert su._fits((1, "x"), st) == {"a": 1, "b": "x"}
    assert su._fits((1,), st) is None
    assert su._fits("a,b", st) is None


def test_a_type_with_no_rule_takes_the_value_as_it_is():
    assert su._fits("anything", T().NullType()) == "anything"


def test_a_udf_argument_must_be_a_column_or_a_name():
    with pytest.raises(TypeError, match="not int. Use lit"):
        su._arg_expr(5)


def test_an_iterator_pandas_udf_runs_over_arrow_arrays():
    def plus_one(batches):
        for s in batches:
            yield s + 1

    fn = su.UserDefinedFunction(plus_one, "long", vectorized=True, iterator=True)
    assert fn._arrow()(pa.array([1, 2])).to_pylist() == [2, 3]


def test_an_iterator_over_two_columns_gets_tuples_and_one_that_yields_nothing_gives_no_rows():
    def add(batches):
        for a, b in batches:
            yield a + b

    fn = su.UserDefinedFunction(add, "long", vectorized=True, iterator=True)
    assert fn._arrow()(pa.array([1, 2]), pa.array([10, 20])).to_pylist() == [11, 22]

    def nothing(batches):
        return iter(())

    empty = su.UserDefinedFunction(nothing, "string", vectorized=True, iterator=True)
    out = empty._arrow()(pa.array([], type=pa.int64()))
    assert out.to_pylist() == [] and out.type == pa.string()
    with pytest.raises(RuntimeError, match="returned 0 for 1 rows"):
        empty._arrow()(pa.array([1]))


class _ArrayLike:
    """Not a list and no ndim, but pandas reads it as an array: pd.isna gives an array, whose truth is unclear."""

    def __array__(self, dtype=None, copy=None):
        return np.array([1.0, np.nan])


class _BadItem:
    def item(self):
        raise ValueError("not one value")


class _OnlyToList:
    def tolist(self):
        return [1, 2]


def test_isnull_treats_a_value_pandas_cant_judge_as_not_null():
    assert su._isnull(_ArrayLike()) is False
    assert su._isnull(None) is True and su._isnull(float("nan")) is True
    assert su._isnull([None]) is False


def test_plain_keeps_a_value_whose_item_fails_and_lists_what_only_has_tolist():
    v = _BadItem()
    assert su._plain(v) is v
    assert su._plain(_OnlyToList()) == [1, 2]
    assert su._plain(np.int64(3)) == 3 and type(su._plain(np.int64(3))) is int


def test_spark2_grouped_agg_function_type_is_refused_as_an_aggregate():
    with pytest.raises(NotImplementedError, match="mean_of is a grouped-aggregate pandas_udf"):
        @su.pandas_udf("double", 202)
        def mean_of(s):
            return s.mean()
    with pytest.raises(NotImplementedError, match="grouped-aggregate"):
        su.pandas_udf(lambda s: s, "double", 202)
