"""Mutation kills for localdata/tsql_hints.py's text pass, take() (batch 93): the batches the other hint tests
don't write, such as an OPTION list that isn't closed, a hint next to one that's kept, and PRINT, and the notes
and refusals of the tree pass, check()."""

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import TokenError

from fabric_desktop.localdata import tsql, tsql_hints

TOKENIZE = tsql.KfTSQL().tokenize


def take(sql):
    return tsql_hints.take(sql, TOKENIZE)


def test_a_batch_the_tokenizer_cant_read_is_given_back_for_the_parse_to_report():
    def broken(_sql):
        raise TokenError("unterminated")

    sql = "SELECT 1 OPTION (FORCE ORDER)"
    assert tsql_hints.take(sql, broken) == sql


def test_print_is_a_command_token_but_not_update_statistics():
    sql = "PRINT 'no statistics here'"
    assert take(sql) == sql


def test_an_option_list_that_isnt_closed_is_left_for_the_parse():
    sql = "SELECT 1 OPTION (FORCE ORDER"
    assert take(sql) == sql


def test_an_option_list_with_nothing_to_carry_is_left_as_written():
    sql = "SELECT 1 OPTION ( LABEL = 'x' )"
    assert take(sql) == sql


def test_a_carried_hint_keeps_the_items_beside_it_as_written():
    assert take("SELECT 1 OPTION (FORCE DISTRIBUTED PLAN, LABEL='x')") == \
        "SELECT 1 OPTION (LABEL = '\x01kf-hint:FORCE DISTRIBUTED PLAN', LABEL='x')"
    assert take("SELECT 1 OPTION (LABEL='x', DISABLE_RESULT_SET_CACHE)") == \
        "SELECT 1 OPTION (LABEL='x', LABEL = '\x01kf-hint:DISABLE_RESULT_SET_CACHE')"


def test_create_statistics_without_a_name_is_refused_by_name():
    with pytest.raises(ValueError, match="isn't written as T-SQL has it, such as CREATE STATISTICS s ON dbo.t"):
        take("CREATE STATISTICS ON dbo.t (c)")


def test_create_statistics_that_stops_after_its_name_is_refused():
    with pytest.raises(ValueError, match="read here as CREATE STATISTICS s ON dbo.t"):
        take("CREATE STATISTICS s")


def test_update_statistics_whose_rest_cant_be_read_is_given_back_for_the_parse():
    calls = []

    def second_fails(sql):
        calls.append(sql)
        if len(calls) > 1:
            raise TokenError("unterminated")
        return TOKENIZE(sql)

    sql = "UPDATE STATISTICS dbo.t"
    assert tsql_hints.take(sql, second_fails) == sql
    assert len(calls) == 2 and calls[1].strip() == "dbo.t"


@pytest.mark.parametrize("sql", ["SELECT 1 OPTION", "SELECT 1 OPTION (LABEL='x'); DROP",
                                 "SELECT 1 OPTION (LABEL='x'); CREATE"])
def test_a_batch_ending_in_option_or_a_statistics_verb_is_left_for_the_parse(sql):
    assert take(sql) == sql


def test_a_table_called_statistics_is_updated_not_read_as_update_statistics():
    sql = "UPDATE [statistics] SET a = 1 OPTION (LABEL = 'x')"
    assert take(sql) == sql


def test_a_one_word_hint_beside_a_carried_one_is_kept():
    assert take("SELECT 1 OPTION (DISABLE_RESULT_SET_CACHE, RECOMPILE)") == \
        "SELECT 1 OPTION (LABEL = '\x01kf-hint:DISABLE_RESULT_SET_CACHE', RECOMPILE)"


@pytest.mark.parametrize("sql", ["DROP STATISTICS", "CREATE STATISTICS s ON dbo.t"])
def test_a_statistics_statement_cut_short_is_refused(sql):
    with pytest.raises(ValueError, match="This T-SQL can't be run on the local engine"):
        take(sql)


# ---- the tree pass

def parse(sql):
    return sqlglot.parse_one(sql, read=tsql.KfTSQL)


def test_maxrecursion_is_kept_for_the_cte_check_beside_a_label():
    tree = parse("WITH c AS (SELECT 1 AS n) SELECT n FROM c OPTION (LABEL = 'x', MAXRECURSION 10)")
    assert tsql_hints.check(tree) == [tsql_hints.LABEL_NOTE]
    owner = next(n for n in tree.walk() if isinstance(n, exp.Expression) and n.args.get("options"))
    assert [o.this.sql(dialect="tsql").upper() for o in owner.args["options"]] == ["MAXRECURSION"]


def test_an_index_hint_is_refused_by_its_name_alone():
    with pytest.raises(ValueError, match=r"the table hint INDEX isn't carried yet"):
        tsql_hints.check(parse("SELECT * FROM t WITH (INDEX(ix))"))


def test_a_carried_plan_hint_label_names_the_hint_in_its_note():
    note = tsql_hints._labelled(exp.Literal.string("\x01kf-hint:FORCE SINGLE NODE PLAN"))
    assert note.startswith("OPTION (FORCE SINGLE NODE PLAN) changes how a warehouse plans the query, not its rows.")
