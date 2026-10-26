"""The data dictionary: what it lists, the CSV round trip, the import preview and save, and descriptions in TMDL."""

import pytest

from fabric_desktop import model_dictionary as md
from fabric_desktop import semantic
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return s


def _edit(text, table, column, description="", classification=""):
    rows = text.split("\r\n")
    for i, line in enumerate(rows):
        cells = line.split(",")
        if len(cells) == 8 and cells[0] == table and cells[1] == column:
            cells[4], cells[5] = description, classification
            rows[i] = ",".join(cells)
    return "\r\n".join(rows)


def test_the_dictionary_lists_the_last_layers_tables_columns_and_measures(svc):
    out = svc.meta_model_dictionary()
    assert out["layer"] == "gold" and out["model"] == "SM_Gold" and out["problem"] is None
    kinds = {(x["table"], x["kind"]) for x in out["items"]}
    assert ("fact_sales", "measure") in kinds and ("dim_customer", "table") in kinds
    assert not any(x["kind"] == "measure" and x["table"] == "dim_customer" for x in out["items"]), "dimensions get no measures"
    sk = next(x for x in out["items"] if x["name"] == "customer_sk" and x["table"] == "dim_customer")
    assert sk["hidden"] is True
    rows = next(x for x in out["items"] if x["name"] == "fact_sales rows")
    assert rows["description"].startswith("Rows in fact_sales.") and "Written by KratosForge" in rows["description"]
    assert out["counts"]["columns_described"] == 0 and out["counts"]["tables"] == 2


def test_the_csv_opens_in_excel_and_reads_back(svc):
    items = svc.meta_model_dictionary()["items"]
    text = md.to_csv(items)
    assert text.startswith(",".join(md.HEADER) + "\r\n")
    back = md.read_csv("﻿" + text)
    assert len(back) == len(items) and back[0]["entity"] == items[0]["entity"]
    # Excel in a comma-decimal locale saves with semicolons.
    assert md.read_csv(text.replace(",", ";"))[0]["table"] == items[0]["table"]
    with pytest.raises(ValueError, match="isn't a data dictionary"):
        md.read_csv("a,b,c\r\n1,2,3")


def test_import_previews_only_real_changes_and_skips_what_it_cant_take(svc):
    text = md.to_csv(svc.meta_model_dictionary()["items"])
    text = _edit(text, "dim_customer", "email", "Customer's contact email.", "Confidential")
    text = _edit(text, "dim_customer", "(table)", "One row per customer.")
    text = _edit(text, "fact_sales", "fact_sales rows", "Ignored: measures are written for you")
    text += "dim_customer,old_col,Column,string,Gone,,,gold.dim_customer\r\n"
    text += "dim_customer,city,Column,string,Town,Secret,,gold.dim_customer\r\n"
    out = svc.meta_model_dictionary_preview(text)
    got = sorted((c["object"], c["field"], c["new"]) for c in out["changes"])
    assert got == [("dim_customer", "description", "One row per customer."),
                   ("dim_customer.email", "classification", "confidential"),
                   ("dim_customer.email", "description", "Customer's contact email.")]
    reasons = {s["object"]: s["reason"] for s in out["skipped"]}
    assert "isn't in gold.dim_customer any more" in reasons["dim_customer.old_col"]
    assert reasons["dim_customer.city"] == "Listed twice; the first row counts"


def test_a_blank_cell_never_erases(svc):
    items = svc.meta_model_dictionary()["items"]
    plan = md.plan_import([x | {"description": "Kept"} for x in items], md.read_csv(md.to_csv(items)))
    assert plan["changes"] == [] and plan["skipped"] == []


def test_import_saves_to_the_catalog_only_after_the_confirm(svc):
    text = _edit(md.to_csv(svc.meta_model_dictionary()["items"]), "dim_customer", "email", "Contact email.", "Confidential")
    ask = svc.meta_model_dictionary_import(text)
    assert ask == {"confirm": {"changes": 2, "skipped": 0}}
    assert all(not c["description"] for c in svc.mesh_about("gold.dim_customer")["columns"]), "nothing saved yet"
    done = svc.meta_model_dictionary_import(text, confirmed=True)
    assert done["saved"] == 1
    email = next(c for c in svc.mesh_about("gold.dim_customer")["columns"] if c["name"] == "email")
    assert (email["description"], email["classification"]) == ("Contact email.", "confidential")
    again = svc.meta_model_dictionary_preview(text)
    assert again["changes"] == [], "a second import finds nothing to change"


def test_export_writes_a_new_file_each_time_and_save_writes_the_project_file(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    a = svc.meta_model_dictionary_export()
    b = svc.meta_model_dictionary_export()
    assert a["path"] != b["path"] and a["path"].endswith("SM_Gold data dictionary.csv")
    with open(a["path"], "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf", "a UTF-8 mark, so Excel shows accents right"
    saved = svc.meta_model_dictionary_save()
    assert saved["path"] == "semantic/SM_Gold.dictionary.csv"
    assert (tmp_path / "proj" / "semantic" / "SM_Gold.dictionary.csv").read_text(encoding="utf-8-sig").startswith("Table,")


def test_update_model_writes_descriptions_into_the_tmdl(svc):
    svc.mesh_save_about("gold.fact_sales", {"description": "One row per order line."})
    svc.mesh_save_about("gold.fact_sales", {"description": "Order amount, before tax."}, "net_amount")
    project = svc._project()
    about = svc.model_descriptions(project, "gold")
    assert about["gold.fact_sales"] == {"": "One row per order line.", "net_amount": "Order amount, before tax."}
    tables, rels = semantic.plan(project, "gold")
    for t in tables:
        t.columns = [(c, "double" if c == "net_amount" else "long") for c in project.entities[t.entity].columns]
    parts = semantic.build(tables, rels, "srv", "sql1", about)
    tmdl = parts["definition/tables/fact_sales.tmdl"]
    assert tmdl.startswith("/// One row per order line.\ntable fact_sales\n")
    assert "\t/// Order amount, before tax.\n\tcolumn net_amount\n" in tmdl
    assert "\t/// Sum of net_amount. Order amount, before tax.\n\tmeasure 'fact_sales total net_amount'" in tmdl
    assert "\t/// Rows in fact_sales.\n\tmeasure 'fact_sales rows'" in tmdl
    # Update model finds the table by its lineage tag even with a description above it.
    assert semantic.table_names(parts, ["gold.fact_sales"]) == {"gold.fact_sales": "fact_sales"}


def test_methods_are_registered():
    from fabric_desktop import audit, server
    assert md.HEADER[-1] == "Entity"
    for m in ("meta_model_dictionary", "meta_model_dictionary_export", "meta_model_dictionary_save",
              "meta_model_dictionary_preview", "meta_model_dictionary_import"):
        assert m in server.METHODS
    assert {"meta_model_dictionary", "meta_model_dictionary_preview"} <= audit.READ_ONLY
    assert not {"meta_model_dictionary_export", "meta_model_dictionary_save", "meta_model_dictionary_import"} & audit.READ_ONLY
