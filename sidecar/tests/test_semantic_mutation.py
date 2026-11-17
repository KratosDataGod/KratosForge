"""Mutation batch 64: semantic.py (see docs/quality/mutation-testing.md).

Pins what the mutation run showed no test held: a quoted name is unquoted only when both ends are
quoted, which entities count as dimensions, which lookups and same-named keys become relationships
(never one from a table to itself, and a lookup doesn't hide another dimension's key), Short Date
only on date columns, and the exact definition.pbism text."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from fabric_desktop import semantic


def entity(name, *, mode="append", sk=None, columns=(), lookup=(), table=None):
    return NS(name=name, layer="gold", schema="dbo", table=table or name.split(".")[1], keys=[], surrogate_key=sk,
              mode=mode, spec={"lookup": list(lookup)} if lookup else {}, columns=list(columns), date_column=None)


def project(*entities):
    return NS(entities={e.name: e for e in entities})


def rels_of(*entities):
    _, rels = semantic.plan(project(*entities), "gold")
    return [(r.from_table, r.from_column, r.to_table, r.to_column) for r in rels]


@pytest.mark.parametrize("name, plain", [("'it''s'", "it's"), ("'abc", "'abc"), ("abc'", "abc'"), ("abc", "abc")])
def test_a_name_is_unquoted_only_when_both_ends_are_quoted(name, plain):
    assert semantic._unquote(name) == plain


@pytest.mark.parametrize("mode, sk, dimension", [("scd1", "c_sk", True), ("scd2", "c_sk", True), ("scd1", None, False),
                                                 ("scd2", "", False), ("append", "c_sk", False), ("merge", None, False)])
def test_only_a_slowly_changing_entity_with_a_surrogate_key_is_a_dimension(mode, sk, dimension):
    (t,), _ = semantic.plan(project(entity("gold.dim_c", mode=mode, sk=sk)), "gold")
    assert t.is_dimension is dimension


def test_a_lookup_joins_the_dimension_s_surrogate_key():
    dim = entity("gold.dim_c", mode="scd2", sk="c_sk", columns=["c_sk"])
    fact = entity("gold.fact_s", columns=["cust"], lookup=[{"dim": "gold.dim_c", "as": "cust"}])
    assert rels_of(dim, fact) == [("fact_s", "cust", "dim_c", "c_sk")]


def test_a_lookup_to_a_table_with_no_surrogate_key_or_no_column_name_adds_nothing():
    plain = entity("gold.ref_c", columns=["code"])
    dim = entity("gold.dim_c", mode="scd2", sk="c_sk", columns=["c_sk"])
    fact = entity("gold.fact_s", columns=["code"], lookup=[{"dim": "gold.ref_c", "as": "code"}, {"dim": "gold.dim_c"},
                                                           {"dim": "gold.dim_c", "as": ""}, {"dim": "gold.gone", "as": "x"}])
    assert rels_of(plain, dim, fact) == []


def test_a_dimension_never_joins_itself_by_its_own_key():
    dim = entity("gold.dim_c", mode="scd1", sk="c_sk", columns=["c_sk", "name"])
    assert rels_of(dim) == []


def test_a_column_named_like_a_dimension_s_key_joins_it_unless_a_lookup_already_did():
    dim_c = entity("gold.dim_c", mode="scd1", sk="c_sk", columns=["c_sk"])
    dim_p = entity("gold.dim_p", mode="scd2", sk="p_sk", columns=["p_sk"])
    fact = entity("gold.fact_s", columns=["c_sk", "p_sk", "cust"], lookup=[{"dim": "gold.dim_c", "as": "cust"}])
    assert rels_of(dim_c, dim_p, fact) == [("fact_s", "cust", "dim_c", "c_sk"), ("fact_s", "c_sk", "dim_c", "c_sk"),
                                           ("fact_s", "p_sk", "dim_p", "p_sk")]
    fact2 = entity("gold.fact_s", columns=["c_sk"], lookup=[{"dim": "gold.dim_c", "as": "c_sk"}])
    assert rels_of(dim_c, fact2) == [("fact_s", "c_sk", "dim_c", "c_sk")]


def test_a_non_dimension_s_key_name_makes_no_relationship():
    ref = entity("gold.ref_c", mode="append", sk="r_sk", columns=["r_sk"])
    fact = entity("gold.fact_s", columns=["r_sk"])
    assert rels_of(ref, fact) == []


@pytest.mark.parametrize("delta_type, short", [("date", True), ("DATE", True), ("timestamp", False),
                                               ("timestamp_ntz", False), ("string", False), ("long", False)])
def test_only_a_date_column_gets_the_short_date_format(delta_type, short):
    t = semantic.ModelTable("t", "gold.t", "dbo", "t", columns=[("when", delta_type)], is_dimension=True)
    assert ("\t\tformatString: Short Date" in semantic._table_tmdl(t, "x").split("\n")) is short


def test_the_definition_file_is_indented_two_spaces():
    parts = semantic.build([], [], "srv", "ep")
    assert parts["definition.pbism"] == '{\n  "version": "4.0",\n  "settings": {}\n}'
    assert json.loads(parts["definition.pbism"]) == {"version": "4.0", "settings": {}}
