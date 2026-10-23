"""Centralized surrogate keys: one key table per business key in LH_Keys, MERGEd first on every run."""

import json
import textwrap

import polars as pl
import pytest

from fabric_desktop.meta import keys as K
from fabric_desktop.meta.engine import Engine
from fabric_desktop.meta.project import Project, validate
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.meta.templates import init_project
from fabric_desktop.notebook import Kernel

from test_meta import DAY1, DAY2, append_entity, edit_entity, landing, write


@pytest.fixture
def proj(tmp_path):
    p = init_project(tmp_path / "project", "3", "Chris", True)
    store = LocalStore(p.root / ".local" / "dev", "dev")
    results = Engine(p, store, DAY1).run_many()
    assert [r.status for r in results] == ["ok"] * 6, [(r.entity, r.message) for r in results]
    return p, store


def table(store, lakehouse, schema, name):
    return store.read(lakehouse, schema, name)


def test_each_business_key_has_its_own_three_column_table(proj):
    p, store = proj
    customers = table(store, K.KEYS_LAKEHOUSE, "sk", "customer")
    assert customers.columns == ["customer_sk", "customer_bk", "create_date"]
    assert customers.schema["customer_sk"] == pl.Int32
    assert customers["customer_bk"].is_unique().all() and customers["customer_sk"].is_unique().all()
    assert customers.filter(pl.col("customer_sk") == -1)["customer_bk"].to_list() == ["UNKNOWN"]
    assert table(store, K.KEYS_LAKEHOUSE, "sk", "order").columns == ["order_sk", "order_bk", "create_date"]


def test_every_row_gets_its_key_and_any_source_maps_to_the_same_key(proj):
    p, store = proj
    keys = dict(table(store, K.KEYS_LAKEHOUSE, "sk", "customer").select("customer_bk", "customer_sk").iter_rows())
    silver_c = table(store, "LH_Silver", "dbo", "customers")
    silver_o = table(store, "LH_Silver", "dbo", "orders")
    assert silver_c.schema["customer_sk"] == pl.Int32
    for df in (silver_c, silver_o):
        assert all(keys[str(c)] == sk for c, sk in df.select("customer_id", "customer_sk").iter_rows())
    # customer 99 exists only on an order: it got its key there, so it never lands on -1
    assert 99 in silver_o["customer_id"].to_list() and "99" in keys and keys["99"] > 0
    run = store.meta("run_log").filter(pl.col("entity") == "silver.orders")["steps"][-1]
    assert [s["name"] for s in json.loads(run)][:3] == ["read", "contract", "surrogate keys"]

    # A second source with the same customer number gets the same customer_sk.
    landing(p.root, "landing/erp/c.csv", "cust_no,full_name\n1,Ada L\n7,New Person\n")
    write(p.root, "metadata/entities/bronze.erp_customers.toml", """
        name = "bronze.erp_customers"
        owner = "Chris"
        [source]
        files = "landing/erp/*.csv"
        [contract]
        keys = ["cust_no"]
        [contract.columns]
        cust_no = "int64"
        full_name = "string"
    """)
    write(p.root, "metadata/entities/silver.erp_customers.toml", """
        name = "silver.erp_customers"
        owner = "Chris"
        [source]
        entity = "bronze.erp_customers"
        [contract]
        keys = ["cust_no"]
        [contract.columns]
        cust_no = "int64"
        full_name = "string"
        [[check]]
        column = "cust_no"
        not_null = true
        unique = true
        [[keys]]
        table = "customer"
        business_key = "cust_no"
    """)
    p.reload()
    assert validate(p, DAY2) == []
    assert all(r.status == "ok" for r in Engine(p, store, DAY2).run_many(["bronze.erp_customers",
                                                                         "silver.erp_customers"]))
    erp = table(store, "LH_Silver", "dbo", "erp_customers")
    assert dict(erp.select("cust_no", "customer_sk").iter_rows())[1] == keys["1"]
    assert dict(erp.select("cust_no", "customer_sk").iter_rows())[7] == max(keys.values()) + 1


def test_rerunning_issues_no_new_keys(proj):
    p, store = proj
    before = table(store, K.KEYS_LAKEHOUSE, "sk", "customer").sort("customer_sk")
    Engine(p, store, DAY2).run_many(backfill="full")
    after = table(store, K.KEYS_LAKEHOUSE, "sk", "customer").sort("customer_sk")
    assert after.equals(before)


def test_composite_and_missing_business_keys(tmp_path):
    p = init_project(tmp_path / "project", "3", "Chris", False)
    store = LocalStore(p.root / ".local" / "dev", "dev")
    K.write_key_table(p.root, "store")
    landing(p.root, "s/a.csv", "region,code,name\nwest, 10 ,A\nwest,11,B\n,12,C\neast,10,D\n")
    write(p.root, "metadata/entities/bronze.stores.toml", """
        name = "bronze.stores"
        owner = "Chris"
        [source]
        files = "s/*.csv"
        [contract.columns]
        region = "string"
        code = "string"
        name = "string"
    """)
    write(p.root, "metadata/entities/silver.stores.toml", """
        name = "silver.stores"
        owner = "Chris"
        [source]
        entity = "bronze.stores"
        [contract]
        keys = ["name"]
        [contract.columns]
        region = "string"
        code = "string"
        name = "string"
        [[check]]
        column = "name"
        not_null = true
        unique = true
        [[keys]]
        table = "store"
        business_key = ["region", "code"]
    """)
    p.reload()
    assert validate(p, DAY1) == []
    assert all(r.status == "ok" for r in Engine(p, store, DAY1).run_many())
    out = dict(table(store, "LH_Silver", "dbo", "stores").select("name", "store_sk").iter_rows())
    assert out["C"] == -1  # no region, so no business key
    bks = table(store, K.KEYS_LAKEHOUSE, "sk", "store")["store_bk"].to_list()
    assert sorted(bks) == ["UNKNOWN", "east||10", "west||10", "west||11"]  # trimmed, joined with ||
    assert len({out["A"], out["B"], out["D"]}) == 3


def test_merge_keys_never_gives_a_business_key_two_keys(tmp_path):
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    first = K.merge_keys(store, kt, pl.Series(["B", "A", None, "A"]))
    second = K.merge_keys(store, kt, pl.Series(["C", "A"]))
    assert (first["inserted"], first["first"], first["last"]) == (2, 1, 2)
    assert (second["inserted"], second["existing"], second["first"]) == (1, 1, 3)
    df = K.read_keys(store, kt)
    assert dict(df.select("customer_bk", "customer_sk").iter_rows()) == {"UNKNOWN": -1, "A": 1, "B": 2, "C": 3}


def test_runs_at_once_never_give_two_business_keys_one_key(tmp_path):
    """Two entities sharing a key table, run at the same time: each key goes to one business key."""
    import threading
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    K.merge_keys(store, kt, pl.Series(["A"]))
    errors, start = [], threading.Barrier(6)

    def run(i):
        start.wait()
        try:
            K.merge_keys(store, kt, pl.Series([f"r{i}-{n}" for n in range(5)]))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    df = K.read_keys(store, kt)
    assert not df["customer_sk"].is_duplicated().any() and not df["customer_bk"].is_duplicated().any()
    assert not errors and df.height == 1 + 1 + 6 * 5  # Unknown, A, and every run's keys


WINDOWS_RACE = OSError(
    "Generic LocalFileSystem error: Unable to open file C:\\keys\\LH_Keys\\Tables\\keys\\customer\\_delta_log"
    "\\00000000000000000006.json#1: Access is denied. (os error 5)")


@pytest.mark.parametrize("step", ["read_at", "merge_insert", "read"])
def test_the_windows_delta_log_race_is_retried(tmp_path, monkeypatch, step):
    """Runs at once on Windows: a writer is briefly refused the log file another is renaming."""
    from fabric_desktop import retrying
    from fabric_desktop.meta.store import Store
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    K.merge_keys(store, kt, pl.Series(["A"]))
    real, calls = getattr(Store, step), []

    def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise WINDOWS_RACE
        return real(self, *a, **kw)

    monkeypatch.setattr(Store, step, flaky)
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    assert K.merge_keys(store, kt, pl.Series(["B"]))["inserted"] == 1
    assert len(calls) >= 2 and retrying.is_retryable(WINDOWS_RACE)
    keys = dict(K.read_keys(store, kt).select("customer_bk", "customer_sk").iter_rows())
    assert keys == {"UNKNOWN": -1, "A": 1, "B": 2}


def test_other_os_errors_are_not_retried(tmp_path, monkeypatch):
    from fabric_desktop import retrying
    from fabric_desktop.meta.store import Store
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    calls = []

    def denied(self, *a, **kw):
        calls.append(1)
        raise OSError("Access is denied. (os error 5)")  # not a Delta log file

    monkeypatch.setattr(Store, "create_if_absent", denied)
    with pytest.raises(OSError):
        K.ensure_key_table(store, kt)
    assert len(calls) == 1 and not retrying.is_retryable(OSError("Access is denied. (os error 5)"))


def test_a_key_computed_from_an_old_version_is_never_committed(tmp_path, monkeypatch):
    """The gap that let duplicates through: another run commits between reading the table and the MERGE."""
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    K.merge_keys(store, kt, pl.Series(["A"]))
    real, calls = store.read_at, []

    def read_then_someone_else_commits(*a):
        out = real(*a)
        calls.append(1)
        if len(calls) == 2:  # the first read is the key table check; the second is the one keys come from
            K.merge_keys(store, kt, pl.Series(["B"]))  # takes key 2 after we read
        return out

    monkeypatch.setattr(store, "read_at", read_then_someone_else_commits)
    out = K.merge_keys(store, kt, pl.Series(["C"]))
    assert out["attempts"] == 2
    keys = dict(K.read_keys(store, kt).select("customer_bk", "customer_sk").iter_rows())
    assert keys == {"UNKNOWN": -1, "A": 1, "B": 2, "C": 3}


def test_creating_a_key_table_never_wipes_one_already_filled(tmp_path):
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    assert K.ensure_key_table(store, kt)
    K.merge_keys(store, kt, pl.Series(["A", "B"]))
    assert not K.ensure_key_table(store, kt)  # a second run, or promote, leaves the keys alone
    assert K.read_keys(store, kt).height == 3


def test_a_key_table_with_a_duplicate_stops_the_run(tmp_path):
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    K.merge_keys(store, kt, pl.Series(["A"]))
    dup = pl.DataFrame({"customer_sk": [1], "customer_bk": ["Z"], "create_date": [None]}, schema=kt.schema())
    store.write(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, kt.table, dup, mode="append")
    with pytest.raises(RuntimeError, match="twice"):
        K.merge_keys(store, kt, pl.Series(["A"]))  # nothing new to add: still checked


def test_type1_dimension_uses_the_central_key(proj):
    p, store = proj
    write(p.root, "metadata/entities/gold.dim_customer1.toml", """
        name = "gold.dim_customer1"
        owner = "Chris"
        [source]
        entity = "silver.customers"
        [load]
        mode = "scd1"
        [scd]
        surrogate_key = "customer_sk"
        [contract]
        keys = ["customer_id"]
        [contract.columns]
        customer_sk = "int64"
        customer_id = "int64"
        name = "string"
    """)
    p.reload()
    assert validate(p, DAY2) == []
    assert Engine(p, store, DAY2).run("gold.dim_customer1").status == "ok"
    keys = dict(table(store, K.KEYS_LAKEHOUSE, "sk", "customer").select("customer_bk", "customer_sk").iter_rows())
    dim = table(store, "LH_Gold", "dbo", "dim_customer1")
    assert all(keys[str(c)] == sk for c, sk in dim.filter(pl.col("customer_sk") > 0)
               .select("customer_id", "customer_sk").iter_rows())
    assert dim.filter(pl.col("customer_sk") == -1).height == 1


def test_the_save_gate_checks_keys(proj):
    p, store = proj
    append_entity(p.root, "silver.customers", '[[keys]]\ntable = "vendor"\nbusiness_key = "nope"\n')
    p.reload()
    errors = validate(p, DAY2)
    assert "silver.customers: key table vendor is not defined in Data mesh > Design > Surrogate keys" in errors
    assert "silver.customers: business key nope is not in the source" in errors
    edit_entity(p.root, "silver.customers", 'table = "vendor"\nbusiness_key = "nope"', 'table = "customer"\nbusiness_key = "name"')
    assert "silver.customers: key table customer is listed twice" in validate(p.reload() or p, DAY2)

    # A layer can require keys on every table.
    from fabric_desktop.meta.project import Layer
    p2 = init_project(p.root.parent / "two", "3", "Chris", True)
    layers = [Layer(**{k: v for k, v in l.to_dict().items() if k != "lakehouse"}) for l in p2.layers]
    layers[2].require_keys = True
    p2.write_layers(layers)
    p2.reload()
    assert p2.layer("gold").require_keys
    errors = validate(p2, DAY2)
    assert "gold.dim_customer: layer gold requires surrogate keys; add a [[keys]] block" in errors

    # Source systems: unique ids and codes, and no secrets in the file.
    K.write_source_systems(p.root, [K.SourceSystem(1, "CRM"), K.SourceSystem(1, "crm")])
    path = K.keys_dir(p.root) / K.SOURCE_SYSTEMS_FILE
    path.write_text(path.read_text() + '[source_system.connection.dev]\npassword = "hunter2"\n')
    errors = validate(p.reload() or p, DAY2)
    assert "source system crm: id 1 is used twice" in errors
    assert "source system crm: code is used twice" in errors
    assert any("password looks like a secret" in e for e in errors)


def test_template_notebook_starts_with_the_merge(proj):
    p, _ = proj
    sql = K.merge_sql(p, p.entities["silver.orders"], "Keys - Test")
    assert sql.index("MERGE INTO `Keys - Test`.LH_Keys.sk.`order`") < sql.index("MERGE INTO `Keys - Test`.LH_Keys.sk.`customer`")
    assert "FROM LH_Bronze.dbo.orders" in sql
    assert "INSERT (customer_sk, customer_bk, create_date)" in sql and "WHEN NOT MATCHED THEN" in sql
    assert "COALESCE(k2.customer_sk, -1) AS customer_sk" in sql
    cells = K.template_cells(p, p.entities["silver.orders"])
    assert cells[0]["source"].startswith("%%sql\n-- Surrogate keys for silver.orders")
    assert "MERGE INTO LH_Keys.sk.`order` AS k" in cells[0]["source"]
    import sqlglot
    assert [type(x).__name__ for x in sqlglot.parse(sql, read="spark")] == ["Merge", "Merge", "Create"]
    out = Kernel().run(cells[0]["source"])
    assert out["status"] == "ok" and "runs in Fabric" in out["outputs"][0]["text"]


def test_service_keys_round_trip(tmp_path):
    from fabric_desktop.server import handle
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    call = lambda method, **params: json.loads(json.dumps(  # noqa: E731
        handle(svc, json.dumps({"id": 1, "method": method, "params": params})), default=str))

    call("meta_init", path=str(tmp_path / "proj"), preset="3")
    keys = call("meta_keys")["result"]
    assert [t["name"] for t in keys["key_tables"]] == ["customer", "order"]
    assert keys["key_tables"][0]["used_by"] == ["silver.customers", "silver.orders"]
    assert keys["key_tables"][0]["created"] is False and keys["local"] is True
    assert [s["code"] for s in keys["source_systems"]] == ["CRM", "SHOP"]

    assert all(r["status"] == "ok" for r in call("meta_run")["result"])
    assert (tmp_path / "proj" / ".local" / "keys-dev" / "LH_Keys" / "Tables" / "sk" / "customer").exists()
    keys = call("meta_keys")["result"]
    assert keys["key_tables"][0]["keys"] == 4 and keys["key_tables"][0]["next"] == 5
    assert call("meta_key_lookup", table="customer", value=" 2 ")["result"]["sk"] == 2
    assert call("meta_key_lookup", table="customer", value="nobody")["result"] is None

    assert "product" in [t["name"] for t in call("meta_new_key_table", name="Product")["result"]["key_tables"]]
    assert "already exists" in call("meta_new_key_table", name="product")["error"]
    assert "customer: silver.customers, silver.orders use it" in call("meta_delete_key_table", name="customer")["error"]
    assert "product" not in [t["name"] for t in call("meta_delete_key_table", name="product")["result"]["key_tables"]]

    erp = {"id": 3, "code": "ERP", "name": "Finance ERP", "system_type": "SQL Server", "active": True,
           "connections": {"dev": {"server_host": "sql-dev.kratos.local", "port": "1433", "database_name": "erp",
                                   "auth_method": "Entra ID", "key_vault_url": "https://kv.vault.azure.net",
                                   "secret_name": "erp-dev"},
                           "prod": {"server_host": "sql.kratos.local", "port": "1433"}}}
    saved = call("meta_save_source_system", system=erp)["result"]["source_systems"]
    assert saved[2]["connections"]["dev"]["server_host"] == "sql-dev.kratos.local"
    assert "already used" in call("meta_save_source_system", system=erp | {"code": "erp2"})["error"]
    assert "never changes" in call("meta_save_source_system", system=erp | {"id": 9}, original_id=3)["error"]
    edited = call("meta_save_source_system", system=erp | {"name": "Finance ERP (D365)"}, original_id=3)["result"]
    assert [s["name"] for s in edited["source_systems"]][2] == "Finance ERP (D365)"
    sync = call("meta_keys_sync")["result"]
    assert sync["config"] == {"source_system": 3, "source_connection": 4}
    conn = LocalStore(tmp_path / "proj" / ".local" / "keys-dev", "dev").read(K.KEYS_LAKEHOUSE, "config",
                                                                              "source_connection")
    assert conn.filter(pl.col("environment") == "prod")["port"].to_list() == [1433]
    assert len(call("meta_delete_source_system", id=3)["result"]["source_systems"]) == 2

    nb = call("meta_key_notebook", name="silver.customers")["result"]
    assert nb["name"] == "nb_bronze_to_silver_customers"
    cells = call("open_notebook", name=nb["name"])["result"]["cells"]
    assert cells[0]["source"].startswith("%%sql") and "MERGE INTO LH_Keys.sk.`customer`" in cells[0]["source"]
    ent = call("meta_entity", name="silver.customers")["result"]
    assert ent["keys_sql"].startswith("-- Surrogate keys for silver.customers")
    assert ent["surrogate_keys"] == [{"table": "customer", "column": "customer_sk", "business_key": ["customer_id"]}]

    assert "Pick a Keys workspace for Test" in call("meta_keys", env="test")["result"]["problem"]
    call("meta_set_environment", env="test", workspace_id="ws-test", workspace_name="Sales Test")
    keys = call("meta_set_keys_workspace", env="test", workspace_id="ws-keys", workspace_name="Keys - Test")["result"]
    assert keys["workspace_name"] == "Keys - Test"
    call("meta_set_environment", env="test", workspace_id="ws-test2", workspace_name="Sales Test 2")
    envs = Project(tmp_path / "proj").environments["test"]
    assert envs["keys_workspace_id"] == "ws-keys" and envs["workspace_id"] == "ws-test2"


def _both_engines(rows: dict[str, list]) -> tuple[list, list]:
    """The composite business key of each row, from the local engine and from the template's Spark SQL
    (translated to DuckDB with sqlglot, so its NULL and string rules are the ones that run)."""
    import duckdb
    import sqlglot
    df = pl.DataFrame(rows, schema={c: pl.String for c in rows})
    local = df.select(K.business_key(list(rows)).alias("bk"))["bk"].to_list()
    sql = sqlglot.transpile(f"SELECT {K._spark_bk(list(rows), 'b')} AS bk FROM t b", read="spark", write="duckdb")[0]
    con = duckdb.connect()
    con.register("t", df.to_arrow())
    return local, [r[0] for r in con.execute(sql).fetchall()]


def test_a_blank_or_missing_part_gives_no_business_key_in_spark_too():
    local, spark = _both_engines({"country": ["US", "US", "US", " "], "customer_no": [" 7 ", "  ", None, "7"]})
    assert local == spark == ["US||7", None, None, None]
    sql = K._spark_bk(["country", "customer_no"])
    assert "CONCAT_WS" not in sql and sql.startswith("CONCAT(")


def test_the_template_merge_skips_rows_without_a_business_key(tmp_path):
    p = init_project(tmp_path / "project", "3", "Chris", True)
    e = p.entities["silver.customers"]
    kt = p.key_tables["customer"]
    e.spec["keys"] = [{"table": "customer", "business_key": ["country", "customer_id"]}]
    sql = K.merge_sql(p, e)
    # The new keys come from rows whose business key isn't NULL, not rows whose raw columns aren't.
    assert f"WHERE n.{kt.bk} IS NOT NULL" in sql and "country IS NOT NULL" not in sql
    assert "CONCAT_WS" not in sql
    assert sql.count(K._spark_bk(["country", "customer_id"])) == 1
    assert sql.count(K._spark_bk(["country", "customer_id"], "b")) == 1  # src_keyed joins on the same key
