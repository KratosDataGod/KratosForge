"""Column lineage edges that mutation testing found untested (meta/lineage.py), on stand-in entities."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop.meta import lineage
from fabric_desktop.meta.project import SCD2_COLUMNS


def entity(name, columns, spec=None, mode="append", mode_columns=(), surrogate_key=None, sources=("bronze.raw",)):
    return SimpleNamespace(name=name, columns=list(columns), spec=spec or {"source": {"entity": list(sources)}},
                           mode=mode, mode_columns=lambda: set(mode_columns), surrogate_key=surrogate_key,
                           sources=lambda: list(sources))


def project(*entities):
    return SimpleNamespace(entities={e.name: e for e in entities})


def raw():
    return entity("bronze.raw", ["a", "b", "k"], spec={"source": {"files": "landing/*.csv"}})


def test_derive_and_lookup_entries_without_their_column_are_skipped():
    e = entity("silver.s", ["a", "b"], spec={"source": {"entity": ["bronze.raw"]},
                                             "derive": [{"expr": "a + 1"}, "a"], "lookup": [{"dim": "d"}, "b"]})
    cols = lineage.entity_columns(project(raw(), e), "silver.s")
    assert cols["a"]["how"] == "source" and cols["b"]["how"] == "source"


def test_a_derive_that_reads_its_own_name_reads_the_source_column():
    e = entity("silver.s", ["a"], spec={"source": {"entity": ["bronze.raw"]}, "derive": [{"column": "a", "expr": "a * 2"}]})
    rec = lineage.entity_columns(project(raw(), e), "silver.s")["a"]
    assert rec["inputs"] == [{"entity": "bronze.raw", "column": "a"}]
    assert "note" not in rec


def test_an_unreadable_derive_says_so():
    e = entity("silver.s", ["a", "z"], spec={"source": {"entity": ["bronze.raw"]}, "derive": [{"column": "z", "expr": "a +* )"}]})
    rec = lineage.entity_columns(project(raw(), e), "silver.s")["z"]
    assert rec["inputs"] == [] and rec["note"] == "the expression couldn't be read"


def test_mode_columns_are_added_once_after_the_contract_columns():
    e = entity("silver.s", ["b", "a"], mode_columns={"a", "k"})
    assert list(lineage.entity_columns(project(raw(), e), "silver.s")) == ["b", "a", "k", "_run_id", "_loaded_at"]


def test_type_2_history_columns_only_on_a_type_2_entity_and_only_once():
    t2 = entity("gold.d", ["a", SCD2_COLUMNS[0]], mode="scd2")
    cols = list(lineage.entity_columns(project(raw(), t2), "gold.d"))
    assert cols == ["a", *SCD2_COLUMNS, "_run_id", "_loaded_at"]
    plain = entity("gold.p", ["a"], mode="append")
    assert list(lineage.entity_columns(project(raw(), plain), "gold.p")) == ["a", "_run_id", "_loaded_at"]


def test_a_lookup_names_the_dimension_key_only_when_the_dimension_has_one():
    spec = {"source": {"entity": ["bronze.raw"]}, "lookup": [{"as": "d_sk", "dim": "gold.d", "on": "k"}]}
    fact = entity("gold.f", ["k", "d_sk"], spec=spec)
    with_key = entity("gold.d", ["k"], surrogate_key="d_sk")
    assert lineage.entity_columns(project(raw(), fact, with_key), "gold.f")["d_sk"]["inputs"] == [
        {"entity": "gold.d", "column": "d_sk"}, {"entity": "gold.f", "column": "k"}]
    no_key = entity("gold.d", ["k"], surrogate_key=None)
    assert lineage.entity_columns(project(raw(), fact, no_key), "gold.f")["d_sk"]["inputs"] == [
        {"entity": "gold.f", "column": "k"}]
    assert lineage.entity_columns(project(raw(), fact), "gold.f")["d_sk"]["inputs"] == [
        {"entity": "gold.f", "column": "k"}]


def test_upstream_of_an_entity_the_project_doesnt_have_is_empty():
    assert lineage.upstream(project(raw()), "silver.gone", "a") == []


def test_upstream_stops_at_a_cycle():
    one = entity("s.one", ["a"], sources=("s.two",))
    two = entity("s.two", ["a"], sources=("s.one",))
    steps = lineage.upstream(project(one, two), "s.one", "a")
    assert [(s["entity"], s["column"]) for s in steps] == [("s.one", "a"), ("s.two", "a")]


def _branching():
    """s.top reads a (from s.left) and b (from s.right); both of those start in bronze.raw, and s.left's
    b comes from s.right too, so the walk meets s.right.b twice."""
    left = entity("s.left", ["a", "b"], spec={"source": {"entity": ["s.right"]},
                                              "derive": [{"column": "a", "expr": "b + 1"}]}, sources=("s.right",))
    right = entity("s.right", ["b", "a"])
    top = entity("s.top", ["c", "a"], spec={"source": {"entity": ["s.left"]}, "derive": [{"column": "c", "expr": "a + b"}]},
                 sources=("s.left",))
    return project(raw(), left, right, top)


def test_upstream_walks_depth_first_nearest_first_and_visits_each_column_once():
    steps = lineage.upstream(_branching(), "s.top", "c")
    assert [(s["entity"], s["column"]) for s in steps] == [
        ("s.top", "c"), ("s.top", "a"), ("s.left", "a"), ("s.left", "b"), ("s.right", "b"), ("bronze.raw", "b")]
    gone = entity("s.gone", ["x", "q"], spec={"source": {"entity": ["s.top"]}, "derive": [{"column": "x", "expr": "q || c"}]},
                  sources=("s.top",))
    steps = lineage.upstream(project(*_branching().entities.values(), gone), "s.gone", "x")
    assert [(s["entity"], s["column"], s["how"]) for s in steps] == [
        ("s.gone", "x", "derived"), ("s.gone", "q", "source"), ("s.top", "c", "derived"), ("s.top", "a", "source"),
        ("s.left", "a", "derived"), ("s.left", "b", "source"), ("s.right", "b", "source"), ("bronze.raw", "b", "source")]
    assert lineage.upstream(_branching(), "s.top", "nope")[0]["note"] == "s.top doesn't write nope"


def test_upstream_follows_a_chain_thousands_of_entities_long():
    """A project whose entities read one another in a chain longer than Python's recursion limit is
    traced to its start (it used to raise RecursionError)."""
    chain, before = [raw()], "bronze.raw"
    for i in range(3000):
        chain.append(entity(f"s.e{i}", ["a"], sources=(before,)))
        before = f"s.e{i}"
    steps = lineage.upstream(project(*chain), before, "a")
    assert len(steps) == 3001 and (steps[0]["entity"], steps[-1]["entity"]) == ("s.e2999", "bronze.raw")
