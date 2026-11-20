"""WR-122: the Spark 3.5 functions docs/spark-functions.md listed as not checked, value-checked against Spark
3.5.3 (see spark_values.py).

aes_encrypt and aes_decrypt, input_file_name and its block functions, java_method and reflect, raise_error and
sql_keywords: the queries in tools/spark_value_cases/functions.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_functions.json. What this PC can't answer as Spark does is refused by name below,
and listed in docs/sql-compatibility.md. The session functions (current_user, user, session_user, version) have
no fixed answer to compare, so they are checked here to be refused by name."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("aes", r"aes_(en|de)crypt\(", r"aes_(en|de)crypt\(\) can't run on this PC.*AES isn't available",
            "`aes_encrypt`, `aes_decrypt`, `try_aes_encrypt`, `try_aes_decrypt`"),
    Refused("input file", r"input_file_(name|block_start|block_length)\(",
            r"input_file_\w+\(\) can't run on this PC.*track the file each row was read from",
            "`input_file_name`, `input_file_block_start`, `input_file_block_length`"),
    Refused("java", r"(java_method|reflect)\(", r"(java_method|reflect)\(\) can't run on this PC.*runs no Java",
            "`java_method`, `reflect`"),
    Refused("sql_keywords", r"sql_keywords\(\)", r"sql_keywords\(\) can't run on this PC.*list of SQL keywords",
            "`sql_keywords()`"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_functions_listed_as_not_checked_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("functions"), REFUSED)


@pytest.mark.parametrize("query, name", [
    ("SELECT current_user()", "current_user"), ("SELECT current_user", "current_user"), ("SELECT user()", "user"),
    ("SELECT session_user()", "session_user"), ("SELECT session_user", "session_user"),
    ("SELECT version()", "version"), ("SELECT id, upper(version()) FROM range(2)", "version"),
])
def test_a_session_function_fabric_answers_for_itself_is_refused_by_name(spark, query, name):
    with pytest.raises(ValueError, match=rf"^{name}\(\) can't run on this PC the way it runs in Fabric, because in "
                                         r"Fabric it .* here it would .*DuckDB.*, so nothing ran\. Run the cell in "
                                         r"Fabric\.$"):
        spark.sql(query).collect()


def test_a_column_named_like_a_session_function_is_read_as_the_column(spark):
    rows = spark.sql("SELECT version, user FROM VALUES (1, 'a') AS t(version, user)").collect()
    assert [tuple(r) for r in rows] == [(1, "a")]


def test_typeof_raise_error_is_void_without_stopping(spark):
    assert spark.sql("SELECT typeof(raise_error('x')), typeof(raise_error(NULL))").collect()[0][:] == ("void", "void")
