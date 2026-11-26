"""OSSIE-007a, one TMDL writer: semantic.py builds the Direct Lake model as TMSL (``model_bim``) and writes it
with ``tmdl.from_bim``. The model must read back the same as the hand-written TMDL it replaces; the text may
differ only in whitespace and quoting (and the M expression's fence). The known risks of the change are pinned
here: the first update's text diff, file names cleaned up, ``table_names`` past 20 MB, and a column called
true or false."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import semantic, tmdl
from fabric_desktop.semantic import ModelTable, Relationship, quote, tag, tmdl_type


# ---- the hand-written writer this story removed, frozen as it was, to compare against

def _old_about(text, indent):
    return [f"{indent}/// {line}".rstrip() for line in str(text or "").strip().splitlines()] if str(text or "").strip() else []


def _old_table(t, expression, about=None):
    about = about or {}
    lines = [*_old_about(about.get(""), ""), f"table {quote(t.name)}", f"\tlineageTag: {tag('table', t.entity)}"]
    date_key = t.date_column if any(c == t.date_column and tmdl_type(d) == "dateTime" for c, d in t.columns) else None
    if date_key:
        lines.append("\tdataCategory: Time")
    lines.append("")
    if not t.is_dimension:
        lines += [*_old_about(f"Rows in {t.name}.", "\t"), f"\tmeasure {quote(t.name + ' rows')} = COUNTROWS({quote(t.name)})",
                  "\t\tformatString: #,0", f"\t\tlineageTag: {tag('measure', t.entity)}", ""]
    for col, summed_type in semantic._summed(t):
        lines += [*_old_about(f"Sum of {col}." + (f" {about[col]}" if about.get(col) else ""), "\t"),
                  f"\tmeasure {quote(f'{t.name} total {col}')} = SUM({quote(t.name)}[{col.replace(']', ']]')}])",
                  f"\t\tformatString: {'#,0' if summed_type == 'int64' else '#,0.00'}",
                  f"\t\tlineageTag: {tag('measure', t.entity, col)}", ""]
    for col, delta_type in t.columns:
        dtype = tmdl_type(delta_type)
        if dtype is None:
            continue
        lines += [*_old_about(about.get(col), "\t"), f"\tcolumn {quote(col)}", f"\t\tdataType: {dtype}"]
        if semantic._hidden(t, col):
            lines.append("\t\tisHidden")
        if col == date_key:
            lines.append("\t\tisKey")
        if dtype == "dateTime" and delta_type.lower() == "date":
            lines.append("\t\tformatString: Short Date")
        lines += [f"\t\tlineageTag: {tag('column', t.entity, col)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {col}", ""]
    lines += [f"\tpartition {quote(t.name)} = entity", "\t\tmode: directLake", "\t\tsource",
              f"\t\t\tentityName: {t.table}", f"\t\t\tschemaName: {t.schema}", f"\t\t\texpressionSource: {expression}", ""]
    return "\n".join(lines)


def _old_build(tables, rels, sql_server, sql_endpoint_id, descriptions=None):
    expression = "DatabaseQuery"
    parts = {
        "definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2),
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1604\n",
        "definition/model.tmdl": "\n".join(
            ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: en-US", ""] + [f"ref table {quote(t.name)}" for t in tables]) + "\n",
        "definition/expressions.tmdl": "\n".join([
            f"expression {expression} =", "\t\tlet",
            f'\t\t    database = Sql.Database("{sql_server}", "{sql_endpoint_id}")', "\t\tin", "\t\t    database",
            f"\tlineageTag: {tag('expression', sql_endpoint_id)}", ""]),
    }
    for t in tables:
        parts[f"definition/tables/{t.name}.tmdl"] = _old_table(t, expression, (descriptions or {}).get(t.entity))
    if rels:
        lines = []
        for r in rels:
            lines += [f"relationship {tag('rel', r.from_table, r.from_column, r.to_table, r.to_column)}"]
            if not r.active:
                lines.append("\tisActive: false")
            lines += [f"\tfromColumn: {quote(r.from_table)}.{quote(r.from_column)}",
                      f"\ttoColumn: {quote(r.to_table)}.{quote(r.to_column)}", ""]
        parts["definition/relationships.tmdl"] = "\n".join(lines)
    return parts


# ---- representative models

def _fact(name="fact_sales", cols=None, **kw):
    cols = cols or [("order_id", "long"), ("customer_sk", "long"), ("qty", "long"), ("amount", "decimal(10,2)"),
                    ("price", "double"), ("note", "string"), ("_run_id", "string"), ("ordered", "date")]
    return ModelTable(name, f"gold.{name}", "dbo", name, columns=cols, keys=["order_id"], **kw)


def _dim(name="dim_customer", cols=None, **kw):
    cols = cols or [("customer_sk", "long"), ("name", "string"), ("joined", "timestamp"), ("tags", "array<string>")]
    kw.setdefault("surrogate_key", "customer_sk")
    return ModelTable(name, f"gold.{name}", "dbo", name, columns=cols, is_dimension=True, **kw)


def _date():
    return _dim("dim_date", [("date_sk", "long"), ("day", "date"), ("month", "string")], surrogate_key="date_sk",
                date_column="day")


def _rel(a, col, b, col_b=None, active=True):
    return Relationship(a, col, b, col_b or col, active=active)


CASES = {
    "empty": ([], [], None),
    "fact-only": ([_fact()], [], None),
    "fact-dim-rels": ([_fact(), _dim(), _date()],
                      [_rel("fact_sales", "customer_sk", "dim_customer"), _rel("fact_sales", "ordered", "dim_date", "day"),
                       _rel("fact_sales", "order_id", "dim_customer", "customer_sk", active=False)], None),
    "hidden-and-formats": ([_fact(cols=[("a_sk", "long"), ("_loaded_at", "timestamp"), ("n", "integer"), ("f", "float"),
                                        ("d", "decimal(38,18)"), ("on", "date"), ("at", "timestamp_ntz"), ("b", "boolean"),
                                        ("raw", "binary"), ("m", "map<string,int>")])], [], None),
    "descriptions": ([_fact(), _dim()], [], {
        "gold.fact_sales": {"": "One row per order line.\n\n  Second paragraph.  ", "qty": "Units.\r\nNot cases.",
                            "note": "   ", "amount": "Net of tax and fees"},
        "gold.dim_customer": {"": "  Customers  ", "name": "/// already a comment", "joined": "```"}}),
    "quoted-names": ([_fact("fact sales", [("order id", "long"), ("it's", "double"), ("a-b", "long"), ("x.y", "double"),
                                           ("odd]name", "double"), ("[b]", "long")]), _dim("dim 'cust'")],
                     [_rel("fact sales", "order id", "dim 'cust'", "customer_sk")], None),
    "unicode": ([_fact("fakta_försäljning", [("belopp_€", "double"), ("数量", "long"), ("Ünïcödé", "string")]),
                 _dim("顧客", [("顧客_sk", "long"), ("名前", "string")], surrogate_key="顧客_sk")],
                [_rel("fakta_försäljning", "顧客_sk", "顧客")], None),
    "reserved-words": ([_fact("table", [("column", "long"), ("measure", "double"), ("ref", "string"), ("model", "long"),
                                        ("partition", "double"), ("expression", "string"), ("relationship", "long")]),
                        _dim("model", [("model_sk", "long"), ("database", "string")], surrogate_key="model_sk")],
                       [_rel("table", "model", "model", "model_sk")], None),
    "number-like-names": ([_fact("2024", [("1", "long"), ("-5", "double"), ("0.5", "double")])], [], None),
}


def _read(parts):
    bim, report = tmdl.to_bim(parts)
    assert report == []
    return bim


@pytest.mark.parametrize("case", list(CASES))
def test_old_and_new_text_read_back_as_the_same_model(case):
    tables, rels, about = CASES[case]
    old = _read(_old_build(tables, rels, "srv.example", "ep-1", about))
    new = _read(semantic.build(tables, rels, "srv.example", "ep-1", about))
    assert tmdl.differences(old, new) == [] and tmdl.differences(new, old) == []
    assert old == new  # the same down to key order, not only by the comparison


@pytest.mark.parametrize("case", ["fact-dim-rels", "descriptions", "quoted-names", "unicode"])
def test_the_built_model_is_what_model_bim_says(case):
    tables, rels, about = CASES[case]
    bim = semantic.model_bim(tables, rels, "srv", "ep", about)
    assert tmdl.differences(bim, _read(semantic.build(tables, rels, "srv", "ep", about))) == []


def _plain(text):
    """Non-blank lines with runs of whitespace as one space and quote marks taken out: what is left once
    whitespace and quoting go."""
    return [" ".join(line.split()).replace("'", "") for line in text.split("\n") if line.strip()]


@pytest.mark.parametrize("case", ["fact-dim-rels", "hidden-and-formats", "descriptions", "quoted-names", "unicode",
                                  "reserved-words"])
def test_the_text_differs_only_in_whitespace_and_quoting(case):
    tables, rels, about = CASES[case]
    old, new = _old_build(tables, rels, "srv", "ep", about), semantic.build(tables, rels, "srv", "ep", about)
    assert set(old) == set(new)  # these names need no cleaning, so the files keep their paths
    for path in old:
        if path != "definition/expressions.tmdl":
            assert _plain(old[path]) == _plain(new[path]), path


def test_the_m_expression_is_fenced_now_and_reads_back_the_same():
    old, new = _old_build([], [], "srv", "ep"), semantic.build([], [], "srv", "ep")
    assert new["definition/expressions.tmdl"].startswith("expression DatabaseQuery = ```\n\tlet\n")
    assert _read(old)["model"]["expressions"] == _read(new)["model"]["expressions"]
    assert _read(new)["model"]["expressions"][0]["expression"] == 'let\n    database = Sql.Database("srv", "ep")\nin\n    database'


# ---- risk 1: an existing model's first update has a whitespace-only diff, and none after it

def test_the_first_update_of_an_existing_model_changes_whitespace_only_and_the_next_changes_nothing():
    tables, rels, about = CASES["fact-dim-rels"]
    before = _old_build(tables, rels, "srv", "ep", about)
    first = semantic.build(tables, rels, "srv", "ep", about)
    changed = sorted(p for p in before if before[p] != first[p])
    assert "definition/tables/fact_sales.tmdl" in changed and "definition.pbism" not in changed
    assert all(_plain(before[p]) == _plain(first[p]) for p in changed if p != "definition/expressions.tmdl")
    assert semantic.build(tables, rels, "srv", "ep", about) == first  # stable from then on


# ---- risk 2: table file names are cleaned up; tables are still found by lineage tag

@pytest.mark.parametrize("name, path", [("sales/eu", "sales_eu"), ("CON", "_CON"), ("a:b", "a_b"), (" spaced. ", "spaced")])
def test_an_unsafe_table_name_gets_a_clean_file_name_and_is_still_found(name, path):
    t = _fact(name, [("amount", "double")])
    parts = semantic.build([t], [], "srv", "ep")
    assert f"definition/tables/{path}.tmdl" in parts
    assert f"definition/tables/{name}.tmdl" not in parts or name == path
    assert semantic.table_names(parts, [t.entity]) == {t.entity: name}
    assert semantic.table_names(_old_build([t], [], "srv", "ep"), [t.entity]) == {t.entity: name}  # old layout too


def test_two_names_one_file_name_apart_get_numbered_files():
    a, b = _fact("x/y", [("v", "long")]), _fact("x_y", [("v", "long")])
    parts = semantic.build([a, b], [], "srv", "ep")
    assert {"definition/tables/x_y.tmdl", "definition/tables/x_y (2).tmdl"} <= set(parts)
    assert semantic.table_names(parts, [a.entity, b.entity]) == {a.entity: "x/y", b.entity: "x_y"}


# ---- risk 3: table_names reads each table file on its own, so the 20 MB limit is per file

def test_table_names_reads_a_model_over_20_mb_when_each_table_file_is_under_it():
    a, b = _fact("a", [("v", "long")]), _fact("b", [("v", "long")])
    parts = semantic.build([a, b], [], "srv", "ep")
    for p in ("definition/tables/a.tmdl", "definition/tables/b.tmdl"):
        parts[p] = "/// " + "x" * 11_000_000 + "\n" + parts[p]
    assert sum(map(len, parts.values())) > tmdl.MAX_CHARS
    assert semantic.table_names(parts, [a.entity, b.entity]) == {a.entity: "a", b.entity: "b"}


def test_table_names_refuses_one_table_file_over_20_mb_instead_of_guessing():
    t = _fact("big", [("v", "long")])
    text = semantic.build([t], [], "srv", "ep")["definition/tables/big.tmdl"]
    with pytest.raises(ValueError, match=r"definition/tables/big\.tmdl couldn't be read \(.*20 MB"):
        semantic.table_names({"definition/tables/big.tmdl": "/// " + "x" * tmdl.MAX_CHARS + "\n" + text}, [t.entity])


# ---- risk 4: a column called true or false

@pytest.mark.parametrize("name", ["true", "false"])
def test_a_column_called_true_or_false_now_reads_back_as_its_name(name):
    t = _fact("t", [(name, "long"), ("v", "double")])
    old, new = _read(_old_build([t], [], "srv", "ep")), _read(semantic.build([t], [], "srv", "ep"))
    col = next(c for c in new["model"]["tables"][0]["columns"] if c["name"] == name)
    assert col["sourceColumn"] == name  # text, as the model has it
    assert next(c for c in old["model"]["tables"][0]["columns"] if c["name"] == name)["sourceColumn"] is (name == "true")
    assert tmdl.differences(old, new) == [f"table 't' column {name!r} sourceColumn"]


# ---- what the one writer refuses or checks

def test_a_name_with_a_line_break_is_refused_instead_of_written_broken():
    with pytest.raises(ValueError, match="line break"):
        semantic.build([_fact("bad\nname", [("v", "long")])], [], "srv", "ep")


def test_anything_that_would_not_read_back_is_refused(monkeypatch):
    real = tmdl.from_bim

    def lossy(bim):
        files, _ = real(bim)
        return files, [{"object": "table 'fact_sales' column 'qty'", "kind": "lost", "message": "x"}]
    monkeypatch.setattr(tmdl, "from_bim", lossy)
    with pytest.raises(ValueError, match=r"wouldn't read back from TMDL as built \(table 'fact_sales' column 'qty'\)"):
        semantic.build([_fact()], [], "srv", "ep")


def test_measures_keep_dax_quoting_while_tmdl_names_use_tmdl_quoting():
    parts = semantic.build([_fact("fact-sales", [("amount", "double")])], [], "srv", "ep")
    text = parts["definition/tables/fact-sales.tmdl"]
    assert "\ttable fact-sales" not in text and text.startswith("table fact-sales\n")
    assert "measure 'fact-sales rows' = COUNTROWS('fact-sales')" in text
    assert "SUM('fact-sales'[amount])" in text


def test_semantic_py_writes_no_tmdl_text_of_its_own():
    with open(semantic.__file__, encoding="utf-8") as f:
        source = f.read()
    for text in ("\\t", "lineageTag:", "ref table", "partition {", "/// "):
        assert text not in source, text
