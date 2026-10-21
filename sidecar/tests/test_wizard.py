"""The new notebook wizard, the proof contract it saves, and receipt files (Prove stage 3)."""

from __future__ import annotations

import ast
import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from test_prove import LAKEHOUSE, FakeFabric, call, svc, wait  # noqa: F401 - svc is a fixture

from fabric_desktop import wizard
from fabric_desktop.server import handle


def error(svc, method, **params):  # noqa: F811
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" in out, out
    return out["error"]["message"] if isinstance(out["error"], dict) else str(out["error"])


ANSWERS = {
    "clean": {"source": "dbo.orders", "keys": "order_id", "output": "silver.orders", "columns": "order_id, amount"},
    "join_aggregate": {"source": "dbo.orders", "join_table": "dbo.rates", "join_on": "region",
                       "group_by": "region", "output": "silver.by_region"},
    "load_file": {"file": "Files/raw/rates.csv", "keys": "region", "output": "dbo.rates"},
    "load_folder": {"folder": "Files/raw/daily", "file_type": "csv", "keys": "order_id", "output": "bronze.orders"},
    "incremental": {"source": "dbo.orders", "watermark": "order_id", "keys": "order_id", "output": "silver.orders"},
    "standardize": {"source": "dbo.raw", "dates": "order_date", "numbers": "amount", "keys": "order_id",
                    "output": "silver.orders"},
    "quality": {"source": "dbo.orders", "keys": "order_id", "required": "region", "allowed": "status: open|closed",
                "ranges": "amount: 0..100", "output": "silver.orders", "rejects": "silver.orders_rejects"},
    "date_dimension": {"start": "2024-01-01", "end": "2025-12-31", "fiscal_start": "7", "output": "gold.dim_date"},
    "reconcile": {"source": "dbo.orders", "other": "silver.orders", "keys": "order_id", "sums": "amount"},
    "run_in_order": {"notebooks": "Load\nClean\nReport", "retries": "1"},
}


def test_every_template_writes_code_that_parses_and_uses_the_lakehouse_module():
    listed = wizard.templates()
    assert {t["id"] for t in listed["templates"]} == set(ANSWERS)
    assert {t["group"] for t in listed["templates"]} == set(listed["groups"])
    for t in listed["templates"]:
        assert set(t["fields"]) <= set(listed["fields"]), t["id"]
    for template, values in ANSWERS.items():
        built = wizard.build(template, values)
        code = [c["source"] for c in built["cells"] if c["type"] == "code"]
        for source in code:
            ast.parse(source)
        if template == "run_in_order":
            assert "runMultiple" in code[-1] and built["proof"]["tables"] == {}
            continue
        assert "from fabric_desktop import lakehouse" in code[0]
        if template == "reconcile":
            assert built["proof"]["tables"] == {} and "write_table" not in "".join(code)
            continue
        assert "lakehouse.write_table(" in code[-1]
        assert built["proof"]["tables"][values["output"]]["keys"]


class FakeLake:
    """The lakehouse module's reads and writes, in memory, to run the wizard's cells."""

    def __init__(self, tables=None, files=None):
        self.tables = {k: v for k, v in (tables or {}).items()}
        self.file_data = files or {}
        self.writes = []

    def read_table(self, table):
        return self.tables[table]

    def table_exists(self, table):
        return table in self.tables

    def open_table(self, table):
        df = self.tables[table]
        return type("T", (), {"to_pyarrow_table": lambda _self, columns=None: (df.select(columns) if columns else df).to_arrow()})()

    def files(self, directory="Files"):
        return [p for p in self.file_data if p.rsplit("/", 1)[0] == directory]

    def read_csv(self, path, **_kw):
        return self.file_data[path]

    read_parquet = read_json = read_csv

    def write_table(self, data, table, mode="overwrite"):
        self.writes.append((table, mode, data.height))
        self.tables[table] = pl.concat([self.tables[table], data], how="diagonal_relaxed") \
            if mode == "append" and table in self.tables else data


def run_cells(built, monkeypatch, lake, **names):
    import fabric_desktop
    monkeypatch.setattr(fabric_desktop, "lakehouse", lake, raising=False)
    scope = dict(names)
    for cell in built["cells"]:
        if cell["type"] == "code":
            exec(compile(cell["source"], "<cell>", "exec"), scope)  # noqa: S102 - the wizard's own fixed cells
    return scope


ORDERS = pl.DataFrame({"order_id": [1, 2, 3, 4], "region": ["n", "s", None, "n"],
                       "status": ["open", "closed", "open", "lost"], "amount": [10.0, 50.0, 5.0, 500.0]})


def test_load_a_folder_reads_each_file_and_notes_where_rows_came_from(monkeypatch):
    lake = FakeLake(files={"Files/raw/daily/a.csv": ORDERS.head(2), "Files/raw/daily/b.csv": ORDERS.tail(2),
                           "Files/raw/daily/skip.json": ORDERS, "Files/raw/other/c.csv": ORDERS})
    built = wizard.build("load_folder", ANSWERS["load_folder"])
    run_cells(built, monkeypatch, lake)
    out = lake.tables["bronze.orders"]
    assert out.height == 4 and set(out["source_file"]) == {"Files/raw/daily/a.csv", "Files/raw/daily/b.csv"}
    assert built["proof"]["tables"]["bronze.orders"]["ignore"] == ["loaded_at"]
    with pytest.raises(AssertionError, match="No .csv files"):
        run_cells(built, monkeypatch, FakeLake())


def test_incremental_load_appends_only_rows_past_the_watermark(monkeypatch):
    lake = FakeLake({"dbo.orders": ORDERS.head(2)})
    built = wizard.build("incremental", ANSWERS["incremental"])
    run_cells(built, monkeypatch, lake)
    lake.tables["dbo.orders"] = ORDERS
    run_cells(built, monkeypatch, lake)
    run_cells(built, monkeypatch, lake)  # nothing new: nothing written
    assert lake.writes == [("silver.orders", "append", 2), ("silver.orders", "append", 2)]
    assert lake.tables["silver.orders"]["order_id"].to_list() == [1, 2, 3, 4]


def test_standardize_renames_trims_and_stops_on_values_it_cant_read(monkeypatch):
    raw = pl.DataFrame({"OrderID": [1, 2], "Order Date": ["2024-01-02", " 2024-02-03 "], "amount": ["1.5", "2"]})
    lake = FakeLake({"dbo.raw": raw})
    built = wizard.build("standardize", {**ANSWERS["standardize"], "keys": "order_id"})
    run_cells(built, monkeypatch, lake)
    out = lake.tables["silver.orders"]
    assert out.columns == ["order_id", "order_date", "amount"]
    assert out.schema["order_date"] == pl.Date and out.schema["amount"] == pl.Float64
    lake.tables["dbo.raw"] = raw.with_columns(pl.lit("not a date").alias("Order Date"))
    with pytest.raises(AssertionError, match="2 values in order_date could not be read"):
        run_cells(built, monkeypatch, lake)


def test_quality_checks_split_good_rows_from_rejects_with_reasons(monkeypatch):
    lake = FakeLake({"dbo.orders": ORDERS})
    built = wizard.build("quality", ANSWERS["quality"])
    run_cells(built, monkeypatch, lake)
    assert lake.tables["silver.orders"]["order_id"].to_list() == [1, 2]
    reasons = dict(zip(*lake.tables["silver.orders_rejects"].select("order_id", "reason").to_dict(as_series=False).values()))
    assert reasons == {3: "region is empty", 4: "status is not one of ['open', 'closed']; amount is outside 0.0..100.0"}
    assert set(built["proof"]["tables"]) == {"silver.orders", "silver.orders_rejects"}


def test_quality_sends_text_in_a_range_column_to_rejects(monkeypatch):
    orders = ORDERS.with_columns(pl.col("amount").cast(pl.Utf8)).with_columns(
        pl.when(pl.col("order_id") == 1).then(pl.lit("n/a")).otherwise(pl.col("amount")).alias("amount"))
    lake = FakeLake({"dbo.orders": orders})
    run_cells(wizard.build("quality", ANSWERS["quality"]), monkeypatch, lake)
    reasons = dict(lake.tables["silver.orders_rejects"].select("order_id", "reason").iter_rows())
    assert reasons[1] == "amount is not a number"
    assert 1 not in lake.tables["silver.orders"]["order_id"].to_list()


def test_date_dimension_has_a_row_per_day_and_a_fiscal_year(monkeypatch):
    lake = FakeLake()
    run_cells(wizard.build("date_dimension", ANSWERS["date_dimension"]), monkeypatch, lake)
    out = lake.tables["gold.dim_date"]
    assert out.height == 731 and out["date_key"][0] == 20240101
    june, july = out.filter(pl.col("date_key") == 20240630), out.filter(pl.col("date_key") == 20240701)
    assert (june["fiscal_year"][0], june["fiscal_month"][0]) == (2024, 12)
    assert (july["fiscal_year"][0], july["fiscal_month"][0]) == (2025, 1)
    weekend = sum((date(2024, 1, 1) + timedelta(days=i)).weekday() >= 5 for i in range(731))
    assert out.filter(pl.col("is_weekend")).height == weekend


def test_reconcile_passes_on_equal_tables_and_names_each_difference(monkeypatch):
    built = wizard.build("reconcile", ANSWERS["reconcile"])
    run_cells(built, monkeypatch, FakeLake({"dbo.orders": ORDERS, "silver.orders": ORDERS}))
    other = ORDERS.head(3).with_columns(pl.col("amount") + 1)
    with pytest.raises(AssertionError, match="differ in 3 ways"):
        run_cells(built, monkeypatch, FakeLake({"dbo.orders": ORDERS, "silver.orders": other}))


def test_run_in_order_chains_each_notebook_after_the_one_before(monkeypatch):
    seen = []
    utils = type("U", (), {"notebook": type("N", (), {"runMultiple": staticmethod(lambda dag: seen.append(dag) or {})})()})()
    run_cells(wizard.build("run_in_order", ANSWERS["run_in_order"]), monkeypatch, FakeLake(), notebookutils=utils)
    acts = seen[0]["activities"]
    assert [(a["name"], a["dependencies"], a["retry"]) for a in acts] == [("Load", [], 1), ("Clean", ["Load"], 1),
                                                                         ("Report", ["Clean"], 1)]


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


def test_a_folder_name_with_quotes_stays_a_literal():
    folder = 'Files/a"b\'; import shutil #'
    built = wizard.build("load_folder", {"folder": folder, "keys": "id", "output": "dbo.x"})
    for cell in built["cells"]:
        if cell["type"] == "code":
            tree = ast.parse(cell["source"])
            assert not any(isinstance(n, ast.Import) and n.names[0].name == "shutil" for n in ast.walk(tree))
    assert any(f"No .csv files in {folder}" in s.value for c in built["cells"] if c["type"] == "code"
               for s in ast.walk(ast.parse(c["source"])) if isinstance(s, ast.Constant) and isinstance(s.value, str))


@pytest.mark.parametrize("template, values, text", [
    ("clean", {"source": "orders; drop", "keys": "id", "output": "dbo.x"}, "table name like dbo.orders"),
    ("clean", {"source": "dbo.orders", "output": "dbo.x"}, "identify a row"),
    ("join_aggregate", {"source": "dbo.a", "join_table": "dbo.b", "join_on": "x, y", "group_by": "g", "output": "dbo.c"},
     "one column to join on"),
    ("load_file", {"file": "Files/../secrets.csv", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("load_file", {"file": "C:/data/x.csv", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("clean", {"source": "dbo.orders", "keys": "id", "output": "dbo.x", "mode": "merge"}, "overwrite or append"),
    ("nope", {}, "No template"),
    ("load_folder", {"folder": "Files/../x", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("load_folder", {"folder": "Tables/x", "keys": "id", "output": "dbo.x"}, "under Files/"),
    ("incremental", {"source": "dbo.a", "watermark": "a, b", "keys": "id", "output": "dbo.x"}, "one column"),
    ("quality", {"source": "dbo.a", "keys": "id", "output": "dbo.x", "rejects": "dbo.X"}, "different table"),
    ("quality", {"source": "dbo.a", "keys": "id", "output": "dbo.x", "rejects": "dbo.r", "ranges": "amount: 1-2"},
     "must look like"),
    ("quality", {"source": "dbo.a", "keys": "id", "output": "dbo.x", "rejects": "dbo.r", "ranges": "amount: ..inf"},
     "must look like"),
    ("quality", {"source": "dbo.a", "keys": "id", "output": "dbo.x", "rejects": "dbo.r", "allowed": "status"},
     "column: rule"),
    ("date_dimension", {"start": "2025-01-02", "end": "2025-01-01", "output": "dbo.d"}, "on or after"),
    ("date_dimension", {"start": "soon", "end": "2025-01-01", "output": "dbo.d"}, "date like"),
    ("date_dimension", {"start": "2025-01-01", "end": "2025-01-01", "fiscal_start": "13", "output": "dbo.d"}, "fiscal"),
    ("run_in_order", {"notebooks": ""}, "one per line"),
    ("run_in_order", {"notebooks": "A\na"}, "each notebook once"),
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


def test_saving_a_receipt_again_never_overwrites_the_first_file(svc, tmp_path, monkeypatch):  # noqa: F811
    # HD-038 (finding EN-11): a second save replaced the first file.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    from fabric_desktop import receipts
    rid = svc.receipts.put(receipts.new_receipt("proof", notebook={"name": "N"}))
    first = Path(call(svc, "receipt_save", id=rid, format="html")["path"])
    first.write_text("edited by hand", encoding="utf-8")
    second = Path(call(svc, "receipt_save", id=rid, format="html")["path"])
    assert second != first and second.parent == first.parent
    assert first.read_text(encoding="utf-8") == "edited by hand"
    assert second.name == first.stem + " (2).html"


def test_a_new_template_runs_and_proves_the_same_in_both_engines(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    nb = call(svc, "wizard_create", template="date_dimension", lakehouse_ref=LAKEHOUSE,
              values={"start": "2024-01-01", "end": "2024-03-31", "fiscal_start": "4", "output": "dbo.dim_date"})
    rules = call(svc, "prove_rules_get", name=nb["name"])
    out = wait(svc, call(svc, "prove_start", name=nb["name"], cells=nb["cells"], lakehouse_ref=LAKEHOUSE, rules=rules)["id"])
    assert svc.fake.ran.returncode == 0, svc.fake.ran.stderr
    assert out["verdict"]["verdict"] == "same", out
    assert out["tables"][0]["table"] == "dbo.dim_date" and out["tables"][0]["keys"] == ["date"]


def test_incremental_runs_in_fabric_too_where_the_target_doesnt_exist_yet(svc):  # noqa: F811
    # Fabric's lakehouse module had no table_exists, so this template failed there on its first cell that used it.
    svc.fake = FakeFabric(svc)
    nb = call(svc, "wizard_create", template="incremental", lakehouse_ref=LAKEHOUSE,
              values={"source": "dbo.orders", "watermark": "order_id", "keys": "order_id", "output": "dbo.orders_copy"})
    rules = call(svc, "prove_rules_get", name=nb["name"])
    out = wait(svc, call(svc, "prove_start", name=nb["name"], cells=nb["cells"], lakehouse_ref=LAKEHOUSE, rules=rules)["id"])
    assert svc.fake.ran.returncode == 0, svc.fake.ran.stderr
    assert out["verdict"]["verdict"] == "same", out
    assert out["tables"][0]["pc"]["rows"] == 3
