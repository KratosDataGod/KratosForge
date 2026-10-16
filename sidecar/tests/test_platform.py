"""The platform (keys, domains, products, catalog text) and the data mesh, on the SQLite platform."""

import json
import threading

import polars as pl
import pytest

from fabric_desktop.central import core, udf
from fabric_desktop.central.client import FabricPlatform, LocalPlatform, PlatformKeys
from fabric_desktop.meta import keys as K
from fabric_desktop.meta import mesh
from fabric_desktop.meta.engine import Engine
from fabric_desktop.meta.project import GateError, Project, validate
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.meta.templates import init_project
from fabric_desktop.meta_service import _toml
from fabric_desktop.service import Service

from test_meta import DAY1, write


@pytest.fixture
def platform(tmp_path):
    return LocalPlatform(tmp_path / "platform.sqlite", lambda: "chris@example.com")


# --------------------------------------------------------------------------- keys


def test_keys_start_after_unknown_and_never_change(platform):
    keys = PlatformKeys(platform, "dev")
    first, inserted = keys.issue("customer", ["b", "a", None, "a", " "])
    assert first == {"a": 1, "b": 2} and inserted == 2
    again, inserted = keys.issue("customer", ["c", "a"])
    assert again == {"a": 1, "c": 3} and inserted == 1
    assert keys.lookup("customer", "UNKNOWN")["sk"] == -1
    assert keys.stats("customer") == {"created": True, "keys": 3, "next": 4}
    assert [r[:2] for r in keys.export("customer")] == [[-1, "UNKNOWN"], [1, "a"], [2, "b"], [3, "c"]]


def test_keys_are_issued_in_batches(platform):
    keys = PlatformKeys(platform, "dev", batch=3)
    got, inserted = keys.issue("order", [str(i) for i in range(10)])
    assert inserted == 10 and sorted(got.values()) == list(range(1, 11))


def test_two_runs_at_once_never_get_two_keys_for_one_business_key(platform):
    keys = PlatformKeys(platform, "dev")
    results = []
    threads = [threading.Thread(target=lambda: results.append(keys.issue("customer", [str(i) for i in range(200)])[0]))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == results[0] for r in results) and len(set(results[0].values())) == 200


def test_import_keeps_numbers_and_refuses_to_renumber(platform):
    out = platform.call("import_keys", name="customer", rows=[[5, "x", "2026-01-01"], [9, "y", "2026-01-02"]])
    assert out == {"imported": 2, "already_there": 0}
    assert platform.call("import_keys", name="customer", rows=[[5, "x", ""]])["already_there"] == 1
    with pytest.raises(core.PlatformError, match="renumbering"):
        platform.call("import_keys", name="customer", rows=[[6, "x", ""]])
    with pytest.raises(core.PlatformError, match="already used"):
        platform.call("import_keys", name="customer", rows=[[9, "z", ""]])
    assert PlatformKeys(platform, "dev").issue("customer", ["new"])[0] == {"new": 10}


def test_key_names_and_long_business_keys_are_checked(platform):
    with pytest.raises(core.PlatformError):
        platform.call("issue_keys", name="Bad Name; drop", business_keys=["1"])
    with pytest.raises(core.PlatformError, match="longer than"):
        platform.call("issue_keys", name="customer", business_keys=["x" * 401])


def test_engine_takes_keys_from_the_platform(tmp_path, platform):
    p = init_project(tmp_path / "project", "3", "Chris", True)
    store = LocalStore(p.root / ".local" / "dev", "dev")
    keys = PlatformKeys(platform, "dev")
    results = Engine(p, store, DAY1, keys_store=keys).run_many()
    assert [r.status for r in results] == ["ok"] * 6, [(r.entity, r.message) for r in results]
    issued = dict((bk, sk) for sk, bk, _ in keys.export("customer"))
    silver = store.read("LH_Silver", "dbo", "orders")
    assert all(issued[str(c)] == sk for c, sk in silver.select("customer_id", "customer_sk").iter_rows())
    assert store.read(K.KEYS_LAKEHOUSE, "sk", "customer") is None  # nothing went to LH_Keys
    assert K.sync(p, keys)["platform"] is True


# --------------------------------------------------------------------------- catalog


def test_domains_go_one_level_deep_and_in_use_ones_stay(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("save_domain", domain={"name": "Sales EMEA", "parent_id": "sales"})
    with pytest.raises(core.PlatformError, match="one level"):
        platform.call("save_domain", domain={"name": "Too deep", "parent_id": "sales-emea"})
    platform.call("register_project", project={"name": "orders", "domain": "sales", "subdomain": "sales-emea"})
    with pytest.raises(core.PlatformError, match="still used"):
        platform.call("delete_domain", did="sales")
    assert [d["id"] for d in platform.call("list_domains")] == ["sales", "sales-emea"]
    assert platform.call("audit")[0]["by_user"] == "chris@example.com"


def test_links_respect_product_access(platform):
    for d in ("sales", "finance"):
        platform.call("save_domain", domain={"name": d})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    platform.call("register_project", project={"name": "ledger", "domain": "finance"})
    platform.call("register_project", project={"name": "pipeline", "domain": "sales"})
    port = [{"port": "customers", "entity": "gold.dim_customer", "workspace_id": "w", "lakehouse_id": "l", "table": "dim_customer"}]
    for name, access, status in (("c360", "domain", "published"), ("secret", "private", "published"), ("wip", "public", "draft")):
        platform.call("register_product", product={"project": "crm", "name": name, "domain": "sales", "access": access,
                                                   "status": status}, ports=port, env="prod")
    reads = {"dev": "prod", "test": "prod", "prod": "prod"}
    platform.call("register_link", consumer="pipeline", alias="cust", product_id="crm/c360", port="customers", reads=reads)
    with pytest.raises(core.PlatformError, match="sales domain only"):
        platform.call("register_link", consumer="ledger", alias="cust", product_id="crm/c360", port="customers", reads=reads)
    with pytest.raises(core.PlatformError, match="private"):
        platform.call("register_link", consumer="pipeline", alias="s", product_id="crm/secret", port="customers", reads=reads)
    with pytest.raises(core.PlatformError, match="draft"):
        platform.call("register_link", consumer="pipeline", alias="w", product_id="crm/wip", port="customers", reads=reads)
    c360 = next(p for p in platform.call("list_products") if p["id"] == "crm/c360")
    assert [c["consumer_project"] for c in c360["consumers"]] == ["pipeline"]
    assert platform.call("resolve_port", product_id="crm/c360", port="customers", env="prod")["table_name"] == "dim_customer"
    assert platform.call("resolve_port", product_id="crm/c360", port="customers", env="test") is None


def test_enrichment_changes_only_the_fields_given(platform):
    platform.call("save_enrichment", target="crm:gold.dim_customer", fields={"description": "One row per customer",
                                                                            "tags": "pii, gold, pii"})
    platform.call("save_enrichment", target="crm:gold.dim_customer", fields={"classification": "confidential"})
    got = platform.call("enrichment", project="crm")["crm:gold.dim_customer"]
    assert got["description"] == "One row per customer" and got["tags"] == ["gold", "pii"]
    assert got["classification"] == "confidential"
    with pytest.raises(core.PlatformError, match="classification"):
        platform.call("save_enrichment", target="crm:x", fields={"classification": "top secret"})


# --------------------------------------------------------------------------- Fabric client and functions


def test_fabric_platform_calls_the_function_and_drops_by(monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"output": {"keys": {"a": 1}, "inserted": 1}, "status": "Succeeded", "errors": []}

    def post(url, headers, json, timeout):  # noqa: A002 - requests' own name
        sent.update(url=url, auth=headers["Authorization"], body=json)
        return Response()

    class Session:
        pass

    session = Session()
    session.post = post
    fp = FabricPlatform(lambda: "tok", "ws", "fn", session=session)
    assert fp.call("issue_keys", name="customer", business_keys=["a"], by="me") == {"keys": {"a": 1}, "inserted": 1}
    assert sent["url"].endswith("/workspaces/ws/userDataFunctions/fn/functions/issue_keys/invoke")
    assert sent["auth"] == "Bearer tok" and "by" not in sent["body"]


def test_function_app_has_a_function_per_operation_and_compiles():
    source = udf.function_app()
    compile(source, "function_app.py", "exec")
    for op in core.OPERATIONS:
        assert f"def {op}(" in source
    parts = udf.definition("ws", "sql")
    assert "function_app.py" in json.dumps(parts)


# --------------------------------------------------------------------------- the mesh in a project


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


def test_upgrade_writes_project_toml_with_a_known_domain(svc):
    with pytest.raises(GateError):
        svc.mesh_set_project({"domain": "sales"})
    svc.platform_save_domain({"name": "Sales"})
    out = svc.mesh_set_project({"domain": "sales", "team": "CRM team"})
    assert out["project"]["domain"] == "sales" and out["project"]["upgraded"]
    assert out["projects"][0]["name"] == "crm"


def test_products_are_checked_on_save_and_documented_before_publish(svc):
    svc.platform_save_domain({"name": "Sales"})
    svc.mesh_set_project({"domain": "sales"})
    with pytest.raises(GateError, match="not a managed entity"):
        svc.mesh_save_product({"name": "c360", "ports": [{"name": "customers", "entity": "gold.nope"}]})
    assert not (svc._project().root / "metadata" / "products" / "c360.toml").exists()
    product = {"name": "c360", "access": "domain", "status": "published",
               "ports": [{"name": "customers", "entity": "gold.dim_customer"}]}
    svc.mesh_save_product(product)
    gaps = mesh.publish_problems(svc._project(), {})
    assert any("needs a description" in g for g in gaps) and any("needs an owner" in g for g in gaps)
    with pytest.raises(GateError):
        svc.mesh_publish("dev")

    svc.mesh_save_product(product | {"description": "Customers", "owner": "Chris"})
    about = svc.mesh_about("gold.dim_customer")
    assert about["products"] == [{"product": "c360", "port": "customers"}] and about["publish_problems"]
    svc.mesh_save_about("gold.dim_customer", {"description": "One row per customer"})
    for c in about["columns"]:
        svc.mesh_save_about("gold.dim_customer", {"description": f"The {c['name']}"}, c["name"])
    assert svc.mesh_about("gold.dim_customer")["publish_problems"] == []
    assert svc.mesh_publish("dev") == {"env": "dev", "published": ["c360"]}
    assert svc.mesh_overview()["published"][0]["id"] == "crm/c360"


def test_an_entity_reads_another_teams_table_through_a_link(svc, tmp_path):
    producer = LocalStore(tmp_path / "other", "dev")
    producer.write("LH_Gold", "dbo", "regions", pl.DataFrame({"region_id": [1, 2], "region": ["EMEA", "APAC"]}),
                   mode="overwrite")
    uri = producer.uri("LH_Gold", "dbo", "regions")
    svc.mesh_save_link({"alias": "regions", "product": "geo/regions", "port": "regions",
                        "pinned": {"prod": "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/dbo/regions"}})
    project = svc._project()
    write(project.root, "metadata/entities/silver.regions.toml", """
        name = "silver.regions"
        owner = "Chris"
        [source]
        link = "regions"
        [contract]
        keys = ["region_id"]
        [contract.columns]
        region_id = "int64"
        region = "string"
        [[check]]
        column = "region_id"
        not_null = true
        unique = true
    """)
    project.reload()
    assert project.entities["silver.regions"].summary()["source"] == "link regions"
    store = LocalStore(project.root / ".local" / "dev", "dev")
    # Without a platform port or a pin for dev, the run stops and says what to do.
    res = Engine(project, store, link_table=svc._link_table(project, "dev")).run("silver.regions")
    assert res.status != "ok" and "hasn't published" in res.message
    res = Engine(project, store, link_table=lambda alias: uri).run("silver.regions")
    assert res.status == "ok", res.message
    assert store.read("LH_Silver", "dbo", "regions").height == 2
    with pytest.raises(GateError, match="read it"):
        svc.mesh_delete_link("regions")


def test_bad_mesh_files_fail_the_save_gate(svc):
    root = svc._project().root
    write(root, "metadata/links/x.toml", 'alias = "x"\nproduct = "nope"\nport = ""\n[location.dev]\ntable = "C:/x"\n')
    write(root, "project.toml", '[project]\nname = "Bad Name"\nsubdomain = "emea"\n')
    errors = validate(Project(root))
    assert any("links/x: product" in e for e in errors) and any("location.dev" in e for e in errors)
    assert any("project.toml: name" in e for e in errors) and any("subdomain needs" in e for e in errors)


def test_designations_route_layers_and_are_saved_as_valid_toml(svc):
    svc.platform_save_domain({"name": "Sales"})
    svc.mesh_set_project({"domain": "sales"})
    svc.meta_set_designation("test", "ingest", "ws-ingest", "Sales Ingest - Test")
    svc.meta_set_layers([{"name": l["name"], "lakehouse": l["lakehouse"], "workspace": "ingest" if l["name"] == "bronze" else ""}
                         for l in svc.meta_project()["layers"]])
    project = svc._project()
    assert project.environments["test"]["workspaces"]["ingest"]["id"] == "ws-ingest"
    assert project.layer("bronze").workspace == "ingest"
    svc.meta_set_designation("test", "ingest")
    assert "workspaces" not in svc._project().environments["test"]


def test_toml_writer_handles_nested_tables():
    import tomllib
    value = {"workspace_id": "a", "workspaces": {"ingest": {"id": "x", "name": 'He said "hi"'}}, "n": 3, "on": True,
             "list": ["a", "b"]}
    assert tomllib.loads(f"v = {_toml(value)}")["v"] == value


def test_keys_move_to_the_platform_with_their_numbers(svc):
    project = svc._project()
    store = svc._keys_store(project, "dev")
    before = store.read(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, "customer").sort("customer_sk")
    out = svc.meta_keys_to_platform("dev")
    assert out["ok"] and all(t["match"] for t in out["tables"])
    assert svc._project().environments["dev"]["keys"] == "platform"
    moved = {bk: sk for sk, bk, _ in svc._platform_keys("dev").export("customer")}
    assert all(moved[bk] == sk for sk, bk in before.select("customer_sk", "customer_bk").iter_rows())
    # Runs now take keys from the platform, continuing after the highest moved key.
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    top = int(before["customer_sk"].max())
    new, _ = svc._platform_keys("dev").issue("customer", ["brand new customer"])
    assert new["brand new customer"] == top + 1
    # Going back would reuse that number, so it's refused.
    with pytest.raises(GateError, match="reuse"):
        svc.meta_keys_to_lakehouse("dev")


def test_teams_build_bronze_from_the_one_landing_zone(svc):
    landing = svc._landing_dir("dev") / "Files" / "erp" / "customers"
    landing.mkdir(parents=True)
    (landing / "day1.csv").write_text("customer_id,name\n1,Ada\n2,Grace\n", encoding="utf-8")
    root = svc._project().root
    write(root, "metadata/entities/bronze.erp_customers.toml", """
        name = "bronze.erp_customers"
        owner = "Chris"
        [source]
        landing = "erp/customers/*.csv"
        [contract]
        keys = ["customer_id"]
        [contract.columns]
        customer_id = "int64"
        name = "string"
    """)
    assert any(e["source"] == "landing erp/customers/*.csv" for e in svc.meta_project()["entities"])
    [res] = svc.meta_run(["bronze.erp_customers"])
    assert res["status"] == "ok", res
    store = svc._store(svc._project(), "dev")
    assert store.read("LH_Bronze", "dbo", "erp_customers").height == 2


def test_one_key_system_once_the_platform_is_in_fabric(svc):
    project = svc._project()
    assert not svc._keys_in_platform(project, "test")
    svc._save_settings(platform={"test": {"workspace_id": "ws", "functions_id": "fn"},
                                 "dev": {"workspace_id": "ws", "functions_id": "fn"}})
    # A new environment with no LH_Keys goes straight to the platform...
    assert svc._keys_in_platform(project, "test")
    assert not any("Keys workspace" in e for e in svc.meta_promote_check("dev", "test")["errors"])
    # ...but one that already issued keys in LH_Keys keeps them until they're moved with their numbers.
    assert not svc._keys_in_platform(project, "dev")
