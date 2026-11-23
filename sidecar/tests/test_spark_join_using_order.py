"""A join on column names (``df.join(other, "k")`` or ``on=["k1", "k2"]``) as Spark 3.5's: the keys come
first, in ``on``'s order, then the rest of the left's columns, then the right's (spark_compat._keys_first).
The key is the left's for an inner or left join, the right's for a right join and either side's for a
full join; a semi or anti join keeps the left's columns as they are. spark.sql's ``SELECT *`` over
JOIN ... USING or NATURAL JOIN puts them in the same order (spark_local._using_star)."""

import pytest

from fabric_desktop.notebook import Kernel

SETUP = """
a = spark.createDataFrame([(1, 2, "x"), (3, 4, "y")], ["l", "k", "v"])
b = spark.createDataFrame([("p", 2, 9), ("q", 5, 8)], ["w", "k", "z"])
a.createOrReplaceTempView("ta")
b.createOrReplaceTempView("tb")
"""


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture(scope="module")
def kernel():
    k = Kernel()
    out = k.run(SETUP)
    assert out["status"] == "ok", out
    return k


def frame(kernel, expr):
    """``expr``'s columns and its rows, sorted."""
    out = kernel.run(f"_f = {expr}\nprint(_f.columns, sorted(map(tuple, _f.collect()), key=str))")
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


@pytest.mark.parametrize(("how", "expected"), [
    ("inner", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9)]"),
    ("left", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (4, 3, 'y', None, None)]"),
    ("right", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (5, None, None, 'q', 8)]"),
    ("full", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (4, 3, 'y', None, None), (5, None, None, 'q', 8)]"),
    ("outer", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (4, 3, 'y', None, None), (5, None, None, 'q', 8)]"),
])
def test_the_key_comes_first(kernel, how, expected):
    assert frame(kernel, f"a.join(b, 'k', '{how}')") == expected
    assert frame(kernel, f"a.join(b, on=['k'], how='{how}')") == expected


@pytest.mark.parametrize(("how", "expected"), [
    ("left_semi", "['l', 'k', 'v'] [(1, 2, 'x')]"),
    ("semi", "['l', 'k', 'v'] [(1, 2, 'x')]"),
    ("left_anti", "['l', 'k', 'v'] [(3, 4, 'y')]"),
    ("leftanti", "['l', 'k', 'v'] [(3, 4, 'y')]"),
])
def test_a_semi_or_anti_join_keeps_the_lefts_columns_as_they_are(kernel, how, expected):
    assert frame(kernel, f"a.join(b, 'k', '{how}')") == expected


def test_several_keys_come_first_in_the_order_given(kernel):
    kernel.run("a2 = spark.createDataFrame([(1, 2, 3)], ['x', 'k2', 'k1'])\n"
               "b2 = spark.createDataFrame([(3, 7, 2)], ['k1', 'y', 'k2'])")
    assert frame(kernel, "a2.join(b2, ['k1', 'k2'])") == "['k1', 'k2', 'x', 'y'] [(3, 2, 1, 7)]"
    assert frame(kernel, "a2.join(b2, ['k2', 'k1'], 'left')") == "['k2', 'k1', 'x', 'y'] [(2, 3, 1, 7)]"


def test_a_key_already_first_and_an_expression_join_keep_their_order(kernel):
    assert frame(kernel, "b.select('k', 'w').join(a, 'k')") == "['k', 'w', 'l', 'v'] [(2, 'p', 1, 'x')]"
    assert frame(kernel, "a.join(b, a.k == b.k)") == "['l', 'k', 'v', 'w', 'k', 'z'] [(1, 2, 'x', 'p', 2, 9)]"


def test_each_sides_columns_are_still_found_after_the_join(kernel):
    assert frame(kernel, "a.join(b, 'k').select(a.v, b.z, 'k')") == "['v', 'z', 'k'] [('x', 9, 2)]"
    assert frame(kernel, "a.join(b, 'k', 'right').select(b.k)") == "['k'] [(2,), (5,)]"
    assert frame(kernel, "a.alias('s').join(b.alias('r'), 'k').select('s.l', 'r.z')") == "['l', 'z'] [(1, 9)]"
    assert frame(kernel, "a.join(a, 'k')") == "['k', 'l', 'v', 'l', 'v'] [(2, 1, 'x', 1, 'x'), (4, 3, 'y', 3, 'y')]"


def test_each_sides_column_of_a_shared_name_resolves_and_drops_after_a_keys_first_join(kernel):
    # The left goes in keys first under its own alias, so a.v and b.v still name their own side's column
    # (no hidden alias, no v_1 renaming), and drop() removes the one named.
    kernel.run("b3 = spark.createDataFrame([('p', 2, 9), ('q', 5, 8)], ['v', 'k', 'z'])")
    assert frame(kernel, "a.join(b3, 'k').select(a.v, b3.v, a.l, b3.z)") == "['v', 'v', 'l', 'z'] [('x', 'p', 1, 9)]"
    assert frame(kernel, "a.join(b3, 'k').drop(b3.v)") == "['k', 'l', 'v', 'z'] [(2, 1, 'x', 9)]"
    assert frame(kernel, "a.join(b3, 'k').drop(a.v)") == "['k', 'l', 'v', 'z'] [(2, 1, 'p', 9)]"
    assert frame(kernel, "a.join(b3, 'k', 'left').drop(b3.v)") == "['k', 'l', 'v', 'z'] [(2, 1, 'x', 9), (4, 3, 'y', None)]"
    assert frame(kernel, "a.join(b3, 'k', 'full').drop(a.v)") == \
        "['k', 'l', 'v', 'z'] [(2, 1, 'p', 9), (4, 3, None, None), (5, None, 'q', 8)]"
    assert frame(kernel, "a.join(b3, 'k').drop('v')") == "['k', 'l', 'z'] [(2, 1, 9)]"


@pytest.mark.parametrize("how", ["inner", "left", "right", "full"])
def test_dropping_either_sides_key_drops_the_one_merged_key(kernel, how):
    kernel.run("b3 = spark.createDataFrame([('p', 2, 9), ('q', 5, 8)], ['v', 'k', 'z'])")
    rows = {"inner": "[(1, 'x', 'p', 9)]", "left": "[(1, 'x', 'p', 9), (3, 'y', None, None)]",
            "right": "[(1, 'x', 'p', 9), (None, None, 'q', 8)]",
            "full": "[(1, 'x', 'p', 9), (3, 'y', None, None), (None, None, 'q', 8)]"}[how]
    for side in ("a", "b3"):
        assert frame(kernel, f"a.join(b3, 'k', '{how}').drop({side}.k)") == f"['l', 'v', 'v', 'z'] {rows}"
    assert frame(kernel, "a.join(b3, ['k']).drop(b3.k, a.v)") == "['l', 'v', 'z'] [(1, 'p', 9)]"
    # an expression join keeps both keys, and each side's drops its own
    assert frame(kernel, "a.join(b3, a.k == b3.k).drop(b3.k)") == "['l', 'k', 'v', 'v', 'z'] [(1, 2, 'x', 'p', 9)]"


@pytest.mark.parametrize(("how", "expected"), [
    ("", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9)]"),
    ("LEFT", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (4, 3, 'y', None, None)]"),
    ("RIGHT OUTER", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (5, None, None, 'q', 8)]"),
    ("FULL OUTER", "['k', 'l', 'v', 'w', 'z'] [(2, 1, 'x', 'p', 9), (4, 3, 'y', None, None), (5, None, None, 'q', 8)]"),
])
def test_sql_star_over_a_using_or_natural_join_puts_the_keys_first(kernel, how, expected):
    assert frame(kernel, f'spark.sql("SELECT * FROM ta {how} JOIN tb USING (k)")') == expected
    assert frame(kernel, f'spark.sql("SELECT * FROM ta NATURAL {how} JOIN tb")') == expected


def test_sql_side_stars_and_a_semi_join_keep_each_sides_own_columns(kernel):
    assert frame(kernel, 'spark.sql("SELECT ta.*, tb.* FROM ta JOIN tb USING (k)")') == \
        "['l', 'k', 'v', 'w', 'k', 'z'] [(1, 2, 'x', 'p', 2, 9)]"
    assert frame(kernel, 'spark.sql("SELECT tb.*, *, 1 AS one FROM ta t JOIN tb USING (K)")') == \
        "['w', 'k', 'z', 'k', 'l', 'v', 'w', 'z', 'one'] [('p', 2, 9, 2, 1, 'x', 'p', 9, 1)]"
    assert frame(kernel, 'spark.sql("SELECT * FROM ta LEFT SEMI JOIN tb USING (k)")') == "['l', 'k', 'v'] [(1, 2, 'x')]"


def test_sql_star_over_chained_and_nested_using_joins(kernel):
    # each join's keys come first, then the joined frame's other columns; a column of the same name that
    # isn't a key stays twice, with no v_1 renaming
    assert frame(kernel, 'spark.sql("SELECT * FROM ta t1 JOIN tb t2 USING (k) JOIN ta t3 USING (l)")') == \
        "['l', 'k', 'v', 'w', 'z', 'k', 'v'] [(1, 2, 'x', 'p', 9, 2, 'x')]"
    assert frame(kernel, 'spark.sql("WITH c AS (SELECT * FROM ta JOIN tb USING (k)) SELECT * FROM c JOIN tb USING (w)")') \
        == "['w', 'k', 'l', 'v', 'z', 'k', 'z'] [('p', 2, 1, 'x', 9, 2, 9)]"
    assert frame(kernel, 'spark.sql("SELECT * FROM (SELECT * FROM ta) x JOIN tb USING (k) JOIN tb t3 ON t3.z = tb.z")') \
        == "['k', 'l', 'v', 'w', 'z', 'w', 'k', 'z'] [(2, 1, 'x', 'p', 9, 'p', 2, 9)]"
