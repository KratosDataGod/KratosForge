"""Schema drift review: detection, the config edits and the schema-change kinds the mutation run
found untested (docs/quality/mutation-testing.md, batch 29). Stand-in entities and Polars types;
nothing is read or written."""

from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.meta import drift


def _entity(spec, columns=None, derived=(), hook_adds=(), mode=(), keys=(), name="silver.orders", layer="silver"):
    return SimpleNamespace(name=name, layer=layer, spec=spec, columns=dict(columns or {}), derived=list(derived),
                           hook_adds=list(hook_adds), mode_columns=lambda: set(mode), keys=list(keys))


def _project(policy="evolve", entities=None):
    return SimpleNamespace(layer=lambda name: SimpleNamespace(drift=policy), entities=entities or {})


# ---------------------------------------------------------------- type names


@pytest.mark.parametrize("dtype, name", [
    (pl.Int64, "int64"), (pl.Int32, "int32"), (pl.Int16, "int32"), (pl.Int8, "int32"), (pl.UInt32, "int64"),
    (pl.UInt64, "int64"), (pl.Float64, "float64"), (pl.Float32, "float32"), (pl.String, "string"),
    (pl.Boolean, "bool"), (pl.Date, "date"), (pl.Datetime("ms"), "timestamp"), (pl.Decimal(10, 2), "string"),
    (pl.UInt8, "string"),
])
def test_contract_type_names(dtype, name):
    assert drift.type_name(dtype) == name


@pytest.mark.parametrize("dtype, kind", [(pl.Int8, "number"), (pl.Float32, "number"), (pl.Decimal(5, 1), "number"),
                                         (pl.Boolean, "bool"), (pl.Date, "date"), (pl.String, "text"),
                                         (pl.Time, "text")])
def test_kinds(dtype, kind):
    assert drift._kind(dtype) == kind


# ---------------------------------------------------------------- expected columns


def test_an_entity_expects_its_own_columns_less_what_it_makes_itself():
    e = _entity({"source": {"from": "crm.orders"}},
                columns={"id": "int64", "total": "float64", "score": "int64", "_current": "bool", "name": "string"},
                derived=["total"], hook_adds=["score"], mode=["_current"])
    assert drift.expected(_project(), e) == {"id": "int64", "name": "string"}


# ---------------------------------------------------------------- detect


def test_detect_without_a_source_frame_reports_the_policies_and_checks_nothing():
    e = _entity({"source": {"files": "landing/*.csv"}, "contract": {"drift": "strict"}})
    out = drift.detect(_project("evolve"), e, None)
    assert out == {"entity": "silver.orders", "source": "landing/*.csv", "added": [], "dropped": [], "retyped": [],
                   "policy": "strict", "own_policy": "strict", "layer_policy": "evolve", "checked": False}


@pytest.mark.parametrize("source, name", [({"entity": "bronze.orders", "files": "f", "from": "t"}, "bronze.orders"),
                                          ({"files": "f", "from": "t"}, "f"), ({"from": "t"}, "t"), ({}, "")])
def test_detect_names_the_source_entity_then_files_then_table(source, name):
    out = drift.detect(_project(), _entity({"source": source}), None)
    assert out["source"] == name


def test_the_layer_policy_applies_when_the_entity_has_none():
    out = drift.detect(_project("ignore_new"), _entity({"source": {"from": "t"}}), None)
    assert out["policy"] == "ignore_new" and out["own_policy"] is None and out["layer_policy"] == "ignore_new"


@pytest.mark.parametrize("spec", [{"source": {"from": "t"}, "step": "notebook"}, {}])
def test_a_step_or_an_entity_without_a_source_is_checked_but_has_no_differences(spec):
    e = _entity(spec, columns={"id": "int64"})
    out = drift.detect(_project(), e, pl.DataFrame({"x": [1]}))
    assert out["checked"] is True and out["added"] == out["dropped"] == out["retyped"] == []


def test_detect_finds_added_dropped_and_retyped_columns():
    e = _entity({"source": {"from": "crm.orders"}}, columns={"id": "int64", "amount": "float64", "day": "date",
                                                              "name": "string"}, keys=["id"])
    df = pl.DataFrame({"id": ["a"], "amount": [1.5], "name": ["x"], "city": ["Oslo"], "_run_id": ["r"],
                       "_loaded_at": ["t"]})
    out = drift.detect(_project(), e, df)
    assert out["checked"] is True
    assert out["added"] == [{"column": "city", "type": "string"}]
    assert out["dropped"] == [{"column": "day", "type": "date", "key": False}]
    assert out["retyped"] == [{"column": "id", "contract": "int64", "source": "string"}]


def test_a_dropped_key_column_is_flagged():
    e = _entity({"source": {"from": "t"}}, columns={"id": "int64", "v": "int64"}, keys=["id"])
    out = drift.detect(_project(), e, pl.DataFrame({"v": [1]}))
    assert out["dropped"] == [{"column": "id", "type": "int64", "key": True}]


def test_a_renamed_source_column_is_read_by_its_contract_name():
    e = _entity({"source": {"from": "t", "rename": {"cust_name": "name"}}}, columns={"name": "string"})
    out = drift.detect(_project(), e, pl.DataFrame({"cust_name": ["a"]}))
    assert out["added"] == out["dropped"] == out["retyped"] == []


def test_a_source_column_a_derived_expression_reads_is_not_new():
    e = _entity({"source": {"from": "t"}, "derive": [{"column": "total", "expr": "price * qty"}]},
                columns={"price": "float64", "total": "float64"}, derived=["total"])
    out = drift.detect(_project(), e, pl.DataFrame({"price": [1.0], "qty": [2], "extra": [1]}))
    assert out["added"] == [{"column": "extra", "type": "int64"}]
    assert out["dropped"] == []


def test_a_same_kind_type_change_is_not_retyped_and_untyped_or_derived_columns_are_skipped():
    e = _entity({"source": {"from": "t"}}, columns={"a": "int64", "b": "", "c": "string", "d": "int64"}, derived=["d"])
    df = pl.DataFrame({"a": [1.5], "b": [1], "c": ["x"], "d": ["y"]})
    out = drift.detect(_project(), e, df)
    assert out["retyped"] == []


def test_reading_from_an_entity_only_reports_a_missing_column_this_entity_uses():
    up = SimpleNamespace(columns={"id": "int64", "spare": "string", "used": "string"}, derived=[], hook_adds=[],
                         mode_columns=lambda: set())
    project = _project(entities={"bronze.orders": up})
    e = _entity({"source": {"entity": "bronze.orders"}}, columns={"id": "int64", "used": "string"})
    out = drift.detect(project, e, pl.DataFrame({"id": [1]}))
    assert out["dropped"] == [{"column": "used", "type": "string", "key": False}]
    assert out["added"] == []


def test_an_expected_column_with_no_type_drops_with_an_empty_type():
    up = SimpleNamespace(columns={}, derived=["full"], hook_adds=[], mode_columns=lambda: set())
    e = _entity({"source": {"entity": "u"}}, columns={"full": ""})
    out = drift.detect(_project(entities={"u": up}), e, pl.DataFrame({"x": [1]}))
    assert out["dropped"] == [{"column": "full", "type": "", "key": False}]


# ---------------------------------------------------------------- the config edits


def test_section_bounds_stop_at_the_next_table_and_skip_trailing_blanks():
    lines = ["[source]", "a = 1", "", "", "[load]", "b = 2", ""]
    assert drift._section(lines, "[source]") == (0, 2)
    assert drift._section(lines, "[load]") == (4, 6)
    assert drift._section(["  [load]  "], "[load]") == (0, 1)
    assert drift._section(["[load]", "", ""], "[load]") == (0, 1)


def test_columns_are_removed_retyped_and_added_in_place_and_other_sections_kept():
    text = ('[source]\nfrom = "t"\n\n[contract.columns]\nid = "int32"\nold = "string"\n  "q" = "int64"\n\n'
            '[load]\nmode = "merge"\n')
    out = drift.apply(text, add={"new": "date"}, retype={"id": "int64"}, remove=["old"])
    assert out == ('[source]\nfrom = "t"\n\n[contract.columns]\nid = "int64"\n  "q" = "int64"\nnew = "date"\n\n'
                   '[load]\nmode = "merge"\n')


def test_a_file_without_a_contract_gets_columns_at_the_end_and_nothing_else_changes_without_decisions():
    assert drift.apply('[source]\nfrom = "t"\n', remove=["x"]) == '[source]\nfrom = "t"\n\n[contract.columns]\n'
    text = '[source]\nfrom = "t"\n'
    assert drift.apply(text) == text
    assert drift.apply(text, add={}, retype={}, remove=[], rename={}) == text


def test_comment_and_blank_lines_in_the_columns_section_are_kept():
    text = '[contract.columns]\n# the id\nid = "int32"\n'
    assert drift.apply(text, retype={"id": "int64"}) == '[contract.columns]\n# the id\nid = "int64"\n'


def test_a_policy_replaces_the_old_one_at_the_top_of_the_contract():
    text = '[contract]\nkeys = ["id"]\n  drift = "strict"\n\n[load]\nmode = "merge"\n'
    out = drift.apply(text, policy="evolve")
    assert out == '[contract]\ndrift = "evolve"\nkeys = ["id"]\n\n[load]\nmode = "merge"\n'
    assert drift.apply(text, policy="") == '[contract]\nkeys = ["id"]\n\n[load]\nmode = "merge"\n'
    assert drift.apply(text, policy=None) == text


def test_a_rename_replaces_the_old_rename_line_in_the_source_section():
    text = '[source]\nfrom = "t"\nrename = { "a" = "b" }\n\n[load]\nmode = "merge"\n'
    out = drift.apply(text, rename={"d": "c"})
    assert out == '[source]\nfrom = "t"\nrename = { "a" = "b", "c" = "d" }\n\n[load]\nmode = "merge"\n'


def test_a_rename_without_a_source_section_starts_the_file():
    assert drift.apply('[load]\nmode = "x"\n', rename={"a": "b"}) == (
        '[source]\nrename = { "b" = "a" }\n\n[load]\nmode = "x"\n')


# ---------------------------------------------------------------- exact types and widening


@pytest.mark.parametrize("dtype, text", [(pl.Int8, "int8"), (pl.UInt8, "uint8"), (pl.UInt16, "uint16"),
                                         (pl.UInt32, "uint32"), (pl.UInt64, "uint64"), (pl.Float32, "float32"),
                                         (pl.Float64, "float64"), (pl.String, "string"), (pl.Boolean, "bool"),
                                         (pl.Date, "date"), (pl.Time, "time"), (pl.Int32, "int32"), (pl.Int64, "int64")])
def test_exact_type_text(dtype, text):
    assert drift.type_text(dtype) == text


@pytest.mark.parametrize("old, new, widens", [
    (pl.Int8, pl.Int16, True), (pl.Int16, pl.Int16, False), (pl.UInt8, pl.UInt16, True), (pl.UInt16, pl.Int32, True),
    (pl.Int16, pl.UInt32, False), (pl.UInt32, pl.UInt16, False), (pl.Int64, pl.UInt64, False),
    (pl.Int32, pl.Float64, True), (pl.Int16, pl.Float64, True), (pl.UInt64, pl.Float64, False),
    (pl.Int32, pl.Float32, False), (pl.Float64, pl.Float32, False), (pl.Float32, pl.Float64, True),
    (pl.Int32, pl.Decimal(10, 0), True), (pl.Int32, pl.Decimal(10, 1), False), (pl.Int8, pl.Decimal(12, 2), True),
    (pl.Int64, pl.Decimal(20, 0), True), (pl.Int64, pl.Decimal(21, 2), False), (pl.Int64, pl.Decimal(None, 18), True),
    (pl.Int64, pl.Decimal(None, 19), False),
    (pl.Decimal(10, 2), pl.Decimal(10, 2), False), (pl.Decimal(10, 2), pl.Decimal(11, 3), True),
    (pl.Decimal(10, 2), pl.Decimal(11, 2), True), (pl.Decimal(10, 2), pl.Decimal(12, 1), False),
    (pl.Decimal(10, 2), pl.Decimal(11, 4), False), (pl.Decimal(None, 2), pl.Decimal(38, 2), False),
    (pl.Date, pl.Datetime("us"), True), (pl.Date, pl.Datetime("us", "UTC"), False), (pl.Datetime("us"), pl.Date, False),
    (pl.String, pl.Int64, False), (pl.Float64, pl.Decimal(38, 2), False), (pl.Decimal(10, 2), pl.Float64, False),
    (pl.UInt8, pl.Decimal(10, 0), True), (pl.Int16, pl.Decimal(10, 0), True), (pl.Int16, pl.Decimal(10, 1), False),
    (pl.UInt32, pl.Decimal(10, 0), True), (pl.UInt32, pl.Float64, True), (pl.UInt64, pl.Decimal(20, 0), True),
    (pl.UInt64, pl.Decimal(20, 1), False),
])
def test_widening(old, new, widens):
    assert drift.widens(old, new) is widens


# ---------------------------------------------------------------- schema changes


def test_a_new_table_is_created_and_not_breaking():
    assert drift.schema_changes(None, {"id": pl.Int64, "d": pl.Date}) == [
        {"change": "created", "columns": "id, d", "breaking": False, "detail": "id int64, d date"}]


def test_no_change_is_an_empty_list():
    assert drift.schema_changes({"id": pl.Int64}, {"id": pl.Int64}) == []


def test_every_kind_of_change_is_listed_once_in_order_with_its_breaking_flag():
    before = {"a": pl.Int32, "b": pl.Int64, "c": pl.Int64, "d": pl.String, "e": pl.Int16, "f": pl.Int32}
    after = {"a": pl.Int64, "b": pl.Int32, "c": pl.String, "e": pl.Int32, "f": pl.Int32, "g": pl.Date, "h": pl.Boolean}
    assert drift.schema_changes(before, after) == [
        {"change": "added", "columns": "g, h", "breaking": False, "detail": "g date, h bool"},
        {"change": "widened", "columns": "a, e", "breaking": False, "detail": "a int32 to int64, e int16 to int32"},
        {"change": "narrowed", "columns": "b", "breaking": True, "detail": "b int64 to int32"},
        {"change": "retyped", "columns": "c", "breaking": True, "detail": "c int64 to string"},
        {"change": "dropped", "columns": "d", "breaking": True, "detail": "d string"},
    ]


def test_only_a_narrowing_is_reported_alone():
    assert [c["change"] for c in drift.schema_changes({"x": pl.Float64}, {"x": pl.Float32})] == ["narrowed"]
    assert [c["change"] for c in drift.schema_changes({"x": pl.Int8}, {})] == ["dropped"]


def test_decimal_text_in_a_change():
    (c,) = drift.schema_changes({"m": pl.Decimal(10, 2)}, {"m": pl.Decimal(12, 2)})
    assert c["detail"] == "m decimal(10,2) to decimal(12,2)" and c["change"] == "widened"
