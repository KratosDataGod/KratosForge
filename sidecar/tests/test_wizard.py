"""The new notebook wizard, the proof contract it saves, and receipt files (Prove stage 3)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_prove import LAKEHOUSE, FakeFabric, call, svc, wait  # noqa: F401 - svc is a fixture

from fabric_desktop import wizard
from fabric_desktop.server import handle


def error(svc, method, **params):  # noqa: F811
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return out["error"]["message"] if isinstance(out["error"], dict) else str(out["error"])


def test_every_template_writes_code_that_parses_and_uses_the_lakehouse_module():
    answers = {"clean": {"source": "dbo.orders", "keys": "order_id", "output": "silver.orders", "columns": "order_id, amount"},
               "join_aggregate": {"source": "dbo.orders", "join_table": "dbo.rates", "join_on": "region",
                                  "group_by": "region", "output": "silver.by_region"},
               "load_file": {"file": "Files/raw/rates.csv", "keys": "region", "output": "dbo.rates"}}
    assert {t["id"] for t in wizard.templates()["templates"]} == set(answers)
    for template, values in answers.items():
        built = wizard.build(template, values)
        code = [c["source"] for c in built["cells"] if c["type"] == "code"]
        for source in code:
            ast.parse(source)
        assert "from fabric_desktop import lakehouse" in code[0]
        assert "lakehouse.write_table(" in code[-1]
        assert built["proof"]["tables"][values["output"]]["keys"]


def test_answers_go_into_the_code_as_literals_only():
    nasty = 'id"); import shutil; shutil.rmtree("C:/") #'
    built = wizard.build("clean", {"source": "dbo.orders", "keys": [nasty], "output": "dbo.clean"})
    for cell in built["cells"]:
        if cell["type"] != "code":
            continue
        tree = ast.parse(cell["source"])
        imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert "shutil" not in imported
    assert any(nasty in c["source"] for c in built["cells"] if c["type"] == "code")


@pytest.mark.parametrize("template, values, text", [
    ("clean", {"source": "orders; drop", "keys": "id", "output": "dbo.x"}, "table name like dbo.orders"),
    ("clean", {"source": "dbo.orders", "output": "dbo.x"}, "identify a row"),
    ("join_aggregate", {"source": "dbo.a", "join_table": "dbo.b", "join_on": "x, y", "group_by": "g", "output": "dbo.c"},
     "one column to join on"),
    ("load_file", {"file": "Files/../secrets.csv", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("load_file", {"file": "C:/data/x.csv", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("clean", {"source": "dbo.orders", "keys": "id", "output": "dbo.x", "mode": "merge"}, "overwrite or append"),
    ("nope", {}, "No template"),
])
def test_bad_answers_are_refused_with_a_reason(template, values, text):
    with pytest.raises(ValueError, match=text):
        wizard.build(template, values)


def test_the_wizard_saves_the_notebook_with_its_contract_and_saves_keep_it(svc):  # noqa: F811
    nb = call(svc, "wizard_create", template="load_file", lakehouse_ref=LAKEHOUSE, name="Load rates",
              values={"file": "Files/raw/rates.csv", "keys": "region", "output": "dbo.Rates"})
    assert nb["name"] == "Load rates" and nb["lakehouse"] == LAKEHOUSE
    assert all(c.get("cell_id") for c in nb["cells"])
    assert call(svc, "prove_rules_get", name="Load rates") == {"tables": {"dbo.Rates": {"keys": ["region"], "ignore": []}}}
    # The notebook screen doesn't send the contract; a save from it keeps it.
    call(svc, "save_notebook", name="Load rates", cells=nb["cells"], lakehouse_ref=LAKEHOUSE)
    assert call(svc, "prove_rules_get", name="Load rates")["tables"]["dbo.Rates"]["keys"] == ["region"]
    assert "already exists" in error(svc, "wizard_create", template="load_file", lakehouse_ref=LAKEHOUSE, name="Load rates",
                                     values={"file": "Files/raw/rates.csv", "keys": "region", "output": "dbo.rates"})


def test_a_wizard_notebook_proves_the_same_with_its_contract(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    nb = call(svc, "wizard_create", template="load_file", lakehouse_ref=LAKEHOUSE,
              values={"file": "Files/raw/rates.csv", "keys": "region", "output": "dbo.Rates"})
    rules = call(svc, "prove_rules_get", name=nb["name"])
    started = call(svc, "prove_start", name=nb["name"], cells=nb["cells"], lakehouse_ref=LAKEHOUSE, rules=rules)
    out = wait(svc, started["id"])
    assert svc.fake.ran.returncode == 0, svc.fake.ran.stderr
    assert out["verdict"]["verdict"] == "same", out
    table = out["tables"][0]
    # The contract named the table as "dbo.Rates"; the proof matched it to the table it wrote.
    assert table["table"] == "dbo.rates" and table["method"] == "keyed" and table["keys"] == ["region"]
    assert out["rules"] == {"tables": {"dbo.rates": {"keys": ["region"], "ignore": []}}}


def test_proof_rules_are_checked_and_named_as_a_proof_names_tables(svc):  # noqa: F811
    call(svc, "save_notebook", name="N", cells=[{"type": "code", "source": ""}], lakehouse_ref=LAKEHOUSE)
    saved = call(svc, "prove_rules_set", name="N", rules={"default": {"rel": 1e-6}, "tables": {"Sales": {"keys": ["id"]}}})
    assert saved == {"default": {"rel": 1e-6}, "tables": {"dbo.sales": {"keys": ["id"]}}}
    assert call(svc, "prove_rules_get", name="N") == saved
    assert error(svc, "prove_rules_set", name="N", rules={"default": {"abs": -1}})
    assert "not tables2" in error(svc, "prove_rules_set", name="N", rules={"tables2": {}})
    call(svc, "prove_rules_set", name="N", rules={})
    assert call(svc, "prove_rules_get", name="N") == {}


def test_receipts_save_to_downloads_and_a_receipt_file_is_saved_whole(svc, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    from fabric_desktop import receipts
    rid = svc.receipts.put(receipts.new_receipt("proof", notebook={"name": "N"}, identity={"user": "a@b.c"}))
    out = call(svc, "receipt_save", id=rid, format="html", hide=["identity"])
    saved = Path(out["path"])
    assert saved.parent == tmp_path / "home" / "Downloads" and saved.name.endswith("-redacted.html")
    assert "a@b.c" not in saved.read_text(encoding="utf-8")
    whole = Path(call(svc, "receipt_save", id=rid, format="json")["path"])
    assert receipts.receipt_id(json.loads(whole.read_text(encoding="utf-8"))) == rid
    assert "saved whole" in error(svc, "receipt_save", id=rid, format="json", hide=["identity"])
