"""Databricks vector functions at their exact edge (mutation batch 166, a boundary re-score of batch 18,
docs/quality/mutation-testing.md): a degree given as the second argument is the degree used."""

import sqlglot.expressions as exp

from fabric_desktop import databricks_builtins_sql as dbs


def test_a_degree_given_as_the_second_argument_is_used():
    vector = exp.column("v")
    assert dbs._degree("vector_norm", [vector]) == 2.0
    assert dbs._degree("vector_norm", [vector, exp.Literal.number(1)]) == 1.0
