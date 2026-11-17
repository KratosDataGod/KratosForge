"""Mutation batch 51: model_dictionary.py, its limits, delimiters and import preview (docs/quality/mutation-testing.md)."""

import pytest

from fabric_desktop import model_dictionary as md
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


HEAD = ",".join(md.HEADER)


def _row(table="t", name="c", kind="Column", typ="string", desc="", cls="", hidden="", entity="gold.t"):
    return ",".join([table, name, kind, typ, desc, cls, hidden, entity])


def _item(name="c", kind="column", description="", classification=""):
    return {"table": "t", "name": name, "kind": kind, "type": "", "entity": "gold.t", "hidden": False,
            "description": description, "classification": classification}


@pytest.mark.parametrize("given,expected", [("int64", "long"), ("INT32", "integer"), ("Decimal(18,2)", "decimal"),
                                            ("datetime", "timestamp"), ("", "string"), (None, "string"),
                                            ("geography", "string")])
def test_delta_type(given, expected):
    assert md.delta_type(given) == expected


def test_the_size_limit_is_five_million_characters():
    assert md.MAX_CHARS == 5_000_000
    body = HEAD + "\r\n"
    line = _row() + "\r\n"
    n = (md.MAX_CHARS - len(body)) // len(line)
    pad = md.MAX_CHARS - len(body) - n * len(line)
    ok = body + line * n + " " * pad
    assert len(ok) == md.MAX_CHARS and len(md.read_csv(ok)) == n
    with pytest.raises(ValueError, match="too large to be a data dictionary"):
        md.read_csv(ok + " ")


def test_a_very_long_cell_gets_the_same_error_as_any_other_bad_file():
    with pytest.raises(ValueError):
        md.read_csv(HEAD + "\r\n" + _row(desc="x" * 200_000) + "\r\n")


def test_the_delimiter_comes_from_the_first_line_and_a_tie_means_comma():
    rows = md.read_csv(HEAD + "\r\n" + _row(desc='"a;b;c;d;e;f;g;h;i;j"') + "\r\n")
    assert rows[0]["description"] == "a;b;c;d;e;f;g;h;i;j"
    # Semicolons later in the file don't count: only the first line says which delimiter Excel used.
    many = HEAD + "\r\n" + "\r\n".join([_row(desc='"x;;;;;;;;;;;;"')] * 3) + "\r\n"
    assert [r["table"] for r in md.read_csv(many)] == ["t", "t", "t"]
    tie = HEAD + ',";;;;;;;;"' + "\r\n" + _row() + "\r\n"
    assert md.read_csv(tie)[0]["entity"] == "gold.t"


def test_counts():
    items = [_item(kind="table", name="", description="T"), _item(kind="table", name=""),
             _item(name="a", description="A"), _item(name="b"), _item(name="c", description="C"),
             _item(name="m", kind="measure", description="M")]
    assert md.counts(items) == {"tables": 2, "tables_described": 1, "columns": 3, "columns_described": 2,
                                "measures": 1}


def test_hidden_columns_go_out_as_yes_and_others_blank():
    items = [_item(name="a") | {"hidden": True}, _item(name="b")]
    lines = md.to_csv(items).split("\r\n")
    assert lines[1].split(",")[6] == "Yes" and lines[2].split(",")[6] == ""


def test_import_rows_are_numbered_as_excel_numbers_them_and_unchanged_rows_are_counted():
    items = [_item(kind="table", name="", description="Old"), _item(name="a", description="Same"),
             _item(name="b")]
    rows = md.read_csv("\r\n".join([HEAD, _row(name="(table)", kind="Table", desc="Old"),
                                    _row(name="a", desc="Same"), _row(name="b"),
                                    _row(name="gone", entity="gold.t"), _row(name="z", entity="")]) + "\r\n")
    out = md.plan_import(items, rows)
    assert out["changes"] == [] and out["unchanged"] == 3
    assert out["skipped"] == [{"row": 5, "object": "t.gone", "reason": "gone isn't in gold.t any more"},
                              {"row": 6, "object": "t.z", "reason": "The Entity column is empty"}]


def test_a_table_row_is_found_by_its_kind_or_by_the_table_marker():
    items = [_item(kind="table", name="")]
    by_kind = md.plan_import(items, [{"kind": "table", "column or measure": "ignored", "entity": "gold.t",
                                      "table": "t", "description": "New"}])
    assert [(c["object"], c["new"]) for c in by_kind["changes"]] == [("t", "New")]
    by_marker = md.plan_import(items, [{"kind": "", "column or measure": "(table)", "entity": "gold.t",
                                        "table": "t", "description": "New"}])
    assert [(c["object"], c["new"]) for c in by_marker["changes"]] == [("t", "New")]


def test_a_row_with_a_change_is_not_counted_unchanged():
    items = [_item(name="a", description="Old")]
    out = md.plan_import(items, [{"kind": "column", "column or measure": "a", "entity": "gold.t", "table": "t",
                                  "description": "New"}])
    assert len(out["changes"]) == 1 and out["unchanged"] == 0


def test_the_layers_entries_hidden_flags_types_and_measure_text(svc):
    items = svc.meta_model_dictionary()["items"]
    assert all(x["hidden"] is False for x in items if x["kind"] in ("table", "measure"))
    by = {(x["table"], x["name"]): x for x in items}
    assert by[("fact_sales", "net_amount")]["type"] == "float64"
    assert by[("dim_customer", "name")]["type"] == "string"
    assert by[("fact_sales", "fact_sales total net_amount")]["description"] == \
        "Sum of net_amount. Written by KratosForge."
