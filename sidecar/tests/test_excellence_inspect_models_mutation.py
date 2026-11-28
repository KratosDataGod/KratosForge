"""Mutation batch 261: excellence/inspect_models.py (docs/quality/mutation-testing.md). The survivors were
the caps on what a hostile model can put in the evidence (50 names a list, 256 characters a name), the
exact answer for a definition with no model, and a relationship missing one of its tables being left out
rather than breaking the whole inspection."""

from __future__ import annotations

from fabric_desktop.excellence import inspect_models


def _col(name: str, kind: str = "int64", hidden: bool = False) -> dict:
    return {"name": name, "dataType": kind, "isHidden": hidden}


def test_a_name_is_kept_up_to_256_characters_and_cut_with_an_ellipsis_after():
    assert inspect_models._cut("a" * 256) == "a" * 256
    cut = inspect_models._cut("a" * 257)
    assert cut == "a" * 255 + "…" and len(cut) == 256
    assert inspect_models._cut(None) == ""


def test_a_definition_without_a_model_gives_exactly_that_error():
    for bim in ({"model": "x"}, {}, [], None):
        assert inspect_models.inspect(bim) == {"read": True, "error": "The definition has no model."}


def test_fifty_bidirectional_relationships_are_named_and_all_are_counted():
    tables = [{"name": "Fact", "columns": [_col(f"k{i:02d}", hidden=True) for i in range(51)]},
              {"name": "Dim", "columns": [_col("id", hidden=True)]}]
    rels = [{"fromTable": "Fact", "fromColumn": f"k{i:02d}", "toTable": "Dim", "toColumn": "id",
             "crossFilteringBehavior": "bothDirections"} for i in range(51)]
    got = inspect_models.inspect({"model": {"tables": tables, "relationships": rels}})
    assert got["bidirectional_count"] == 51 and len(got["bidirectional"]) == 50
    assert got["bidirectional"][0] == "Fact.k00 to Dim.id"


def test_a_rule_keeps_fifty_objects_and_counts_them_all():
    tables = [{"name": "Fact", "columns": [_col(f"k{i:02d}") for i in range(52)]},
              {"name": "Dim", "columns": [_col("id", hidden=True)]}]
    rels = [{"fromTable": "Fact", "fromColumn": f"k{i:02d}", "toTable": "Dim", "toColumn": "id"} for i in range(52)]
    got = inspect_models.inspect({"model": {"tables": tables, "relationships": rels}})
    sm004 = got["rules"]["SM004"]
    assert sm004["count"] == 52 and len(sm004["objects"]) == 50
    assert sm004["objects"] == sorted(sm004["objects"])


def test_a_relationship_missing_a_table_is_left_out_and_the_rest_is_inspected():
    tables = [{"name": "Fact", "columns": [_col("k", hidden=True)]}, {"name": "Dim", "columns": [_col("id", hidden=True)]}]
    rels = [{"fromTable": "Fact", "fromColumn": "k", "toTable": "Dim", "toColumn": "id"},
            {"fromTable": "Fact", "fromColumn": "k"},  # no toTable
            {"toTable": "Dim", "toColumn": "id"}]  # no fromTable
    got = inspect_models.inspect({"model": {"tables": tables, "relationships": rels}})
    assert "error" not in got
    assert got["relationships"] == 1 and got["types_unknown"] == [] and got["columns_unknown"] == []
