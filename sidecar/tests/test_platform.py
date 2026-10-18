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


class _SqlServer:
    """A stand-in for a SQL Server connection that holds a statement to SQL Server's limits."""

    def __init__(self):
        self.statements = []

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        assert len(params) <= 2100, f"{len(params)} parameters"
        assert sql.count("(?, ?, ?)") <= 1000, "more than 1,000 rows in one VALUES list"
        self.statements.append((sql, params))
        self.description, self._rows = [("bk",), ("sk",)], []

    def fetchall(self):
        return self._rows

    def commit(self):
        pass


def test_a_large_key_import_stays_under_sql_server_limits(monkeypatch):
    monkeypatch.setattr(core, "ensure_key_table", lambda *a: None)
    conn = _SqlServer()
    rows = [[i, f"bk{i}", "2026-01-01"] for i in range(1, 2501)]
    out = core.import_keys(core.Db(conn, "tsql"), "customer", rows)
    inserts = [p for sql, p in conn.statements if "IDENTITY_INSERT" in sql]
    assert out == {"imported": 2500, "already_there": 0}
    assert sum(len(p) for p in inserts) == 2500 * 3 and len(inserts) == 5
    assert "RESEED, 2500" in conn.statements[-1][0]


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


# --------------------------------------------------------------------------- platform admins, requests, landing


def _as(platform, user):
    """The same platform database, called as someone else (as UDF_Platform would record them)."""
    return LocalPlatform(platform.path, lambda: user)


def test_only_platform_admins_create_domains_key_tables_and_sources(platform):
    # Chris opened the platform first, so he's its admin; Ana is a domain engineer.
    assert platform.call("whoami")["admin"] and [a["user_name"] for a in platform.call("list_admins")] == ["chris@example.com"]
    ana = _as(platform, "Ana@Example.com")
    me = ana.call("whoami")
    assert me == {"user": "ana@example.com", "admin": False, "admins": 1, "can_claim": False, "domains_owned": []}
    with pytest.raises(core.PlatformError, match="already has admins"):
        ana.call("claim_admin")
    for op, kwargs in (("save_domain", {"domain": {"name": "Sales"}}), ("ensure_key_table", {"name": "supplier"}),
                       ("save_source", {"source": {"name": "sap"}}), ("add_admin", {"user": "ana@example.com"})):
        with pytest.raises(core.PlatformError, match="Only platform admins"):
            ana.call(op, **kwargs)
    # Issuing keys from a key table that isn't there is refused too, with what to do.
    with pytest.raises(core.PlatformError, match="Request a key table"):
        PlatformKeys(ana, "dev").issue("supplier", ["S1"])
    platform.call("ensure_key_table", name="supplier", business_key="supplier_id", description="A supplier")
    assert PlatformKeys(ana, "dev").issue("supplier", ["S1"])[0] == {"S1": 1}
    [kt] = ana.call("list_key_tables")
    assert kt["name"] == "supplier" and kt["business_key"] == "supplier_id" and kt["keys"] == 1
    assert kt["created_by"] == "chris@example.com"
    # Admins add admins, and a platform always keeps one.
    platform.call("add_admin", user="ana@example.com")
    assert ana.call("whoami")["admin"]
    ana.call("remove_admin", user="chris@example.com")
    with pytest.raises(core.PlatformError, match="at least one admin"):
        ana.call("remove_admin", user="ana@example.com")


def test_a_new_platform_has_no_admin_until_someone_claims_it(tmp_path):
    fresh = LocalPlatform(tmp_path / "p.sqlite", lambda: "")  # signed out on their own PC: it's theirs
    assert fresh.call("whoami")["admin"]
    with fresh._db() as db:  # in Fabric every call has a caller, and there's no admin yet
        fabric_like = core.Db(db.conn, "sqlite")
        assert core.whoami(fabric_like, "lee@example.com")["can_claim"]
        with pytest.raises(core.PlatformError, match="no admins yet"):
            core.save_domain(fabric_like, {"name": "Sales"}, by="lee@example.com")
        assert core.claim_admin(fabric_like, "lee@example.com")["admin"]
        core.save_domain(fabric_like, {"name": "Sales"}, by="lee@example.com")


def test_domain_owners_keep_their_own_glossary(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "ana@example.com; bo@example.com"})
    platform.call("save_domain", domain={"name": "Finance"})
    ana = _as(platform, "ana@example.com")
    assert ana.call("whoami")["domains_owned"] == ["sales"]
    ana.call("save_term", term={"term": "Active customer", "definition": "Bought in 12 months", "domain_id": "sales"})
    for term in ({"term": "Revenue", "domain_id": "finance"}, {"term": "Shared"},
                 {"term": "Active customer", "domain_id": "finance"}):
        with pytest.raises(core.PlatformError, match="glossary"):
            ana.call("save_term", term=term)


def test_requests_for_key_tables_sources_and_access(platform):
    for d in ("sales", "finance"):
        platform.call("save_domain", domain={"name": d})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    platform.call("register_project", project={"name": "ledger", "domain": "finance"})
    port = [{"port": "customers", "entity": "gold.dim_customer", "workspace_id": "w", "lakehouse_id": "l", "table": "dim_customer"}]
    platform.call("register_product", product={"project": "crm", "name": "c360", "domain": "sales", "access": "domain",
                                               "status": "published", "owner": "Bo@example.com"}, ports=port, env="prod")
    ana, bo = _as(platform, "ana@example.com"), _as(platform, "bo@example.com")
    reads = {"dev": "prod", "test": "prod", "prod": "prod"}

    # A key table: Ana asks, only an admin decides, approving makes it.
    req = ana.call("request", kind="key_table", target="Supplier", project="ledger", reason="Supplier dimension",
                   detail={"business_key": "supplier_id"})
    assert req["target"] == "supplier" and req["status"] == "waiting"
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        ana.call("request", kind="key_table", target="supplier")
    [mine] = ana.call("list_requests")
    assert mine["mine"] and not mine["can_decide"]
    with pytest.raises(core.PlatformError, match="Only a platform admin"):
        ana.call("decide_request", id=req["id"], approve=True)
    assert platform.call("list_requests")[0]["can_decide"]
    done = platform.call("decide_request", id=req["id"], approve=True, note="Made it")
    assert done["status"] == "approved" and done["decided_by"] == "chris@example.com"
    assert [k["name"] for k in platform.call("list_key_tables")] == ["supplier"]
    with pytest.raises(core.PlatformError, match="already approved"):
        platform.call("decide_request", id=req["id"], approve=False)
    with pytest.raises(core.PlatformError, match="already there"):
        ana.call("request", kind="key_table", target="supplier")

    # A source for the landing zone: approving adds it.
    src = ana.call("request", kind="source", target="shopify", detail={"owner": "lee@example.com", "expected_hours": "24"})
    platform.call("decide_request", id=src["id"], approve=True)
    assert [(s["name"], s["expected_hours"]) for s in ana.call("list_sources")] == [("shopify", "24")]

    # Access to another domain's product: its owner decides, then the link is allowed.
    with pytest.raises(core.PlatformError, match="Request access"):
        ana.call("register_link", consumer="ledger", alias="cust", product_id="crm/c360", port="customers", reads=reads)
    acc = ana.call("request", kind="access", target="crm/c360", project="ledger", reason="Invoices by customer")
    assert [r["can_decide"] for r in bo.call("list_requests", status="waiting")] == [True]
    bo.call("decide_request", id=acc["id"], approve=True)
    ana.call("register_link", consumer="ledger", alias="cust", product_id="crm/c360", port="customers", reads=reads)
    assert next(p for p in ana.call("list_products") if p["id"] == "crm/c360")["granted"] == ["ledger"]
    with pytest.raises(core.PlatformError, match="already has access"):
        ana.call("request", kind="access", target="crm/c360", project="ledger")
    with pytest.raises(core.PlatformError, match="owner or a platform admin"):
        ana.call("revoke_grant", product_id="crm/c360", project="ledger")
    assert bo.call("revoke_grant", product_id="crm/c360", project="ledger") == []

    # Declined and withdrawn requests stay in the list with who and when.
    nope = ana.call("request", kind="key_table", target="vendor")
    with pytest.raises(core.PlatformError, match="person who sent"):
        bo.call("withdraw_request", id=nope["id"])
    assert ana.call("withdraw_request", id=nope["id"])["status"] == "withdrawn"
    assert {r["status"] for r in platform.call("list_requests")} == {"approved", "withdrawn"}


def test_sources_keep_secrets_out_and_in_use_ones_stay(platform):
    with pytest.raises(core.PlatformError, match="Key Vault"):
        platform.call("save_source", source={"name": "sap", "connections": {"dev": {"password": "hunter2"}}})
    platform.call("save_source", source={"name": "sap", "title": "SAP ERP", "expected_hours": "24",
                                         "connections": {"dev": {"secret_name": "sap-conn"}}})
    platform.call("register_project", project={"name": "crm", "landing": {"sap": ["bronze.sap_customer"]}})
    [sap] = platform.call("list_sources")
    assert sap["connections"] == {"dev": {"secret_name": "sap-conn"}}
    assert sap["readers"] == [{"project": "crm", "entities": ["bronze.sap_customer"]}]
    with pytest.raises(core.PlatformError, match="still read by crm"):
        platform.call("delete_source", name="sap")
    platform.call("register_project", project={"name": "crm", "landing": {}})
    assert platform.call("delete_source", name="sap") == []


def test_platform_page_lists_the_landing_zone_and_who_reads_it(svc):
    landing = svc._landing_dir("dev") / "Files"
    (landing / "erp" / "customers").mkdir(parents=True)
    (landing / "erp" / "customers" / "day1.csv").write_text("customer_id,name\n1,Ada\n", encoding="utf-8")
    (landing / "crm" / "leads").mkdir(parents=True)
    old = landing / "crm" / "leads" / "old.csv"
    old.write_text("id\n1\n", encoding="utf-8")
    import os
    os.utime(old, (1_600_000_000, 1_600_000_000))
    svc.platform_save_source({"name": "erp", "title": "ERP", "expected_hours": "24"})
    svc.platform_save_source({"name": "crm", "expected_hours": "24"})
    svc.platform_save_source({"name": "hr", "expected_hours": "24"})
    out = svc.platform_landing("dev")
    by = {s["name"]: s for s in out["sources"]}
    assert by["erp"]["entities"][0]["name"] == "customers" and by["erp"]["entities"][0]["files"] == 1
    assert not by["erp"]["late"] and by["crm"]["late"] and by["hr"]["late"]
    assert [f["path"] for f in svc.platform_landing_files("dev", "erp", "customers")] == ["erp/customers/day1.csv"]

    # A project that reads the landing zone is listed as a reader once it joins the mesh.
    svc.platform_save_domain({"name": "Sales"})
    text = svc.meta_new_entity("landing", "bronze.erp_customers", source="erp/customers/*.csv")["text"]
    assert 'landing = "erp/customers/*.csv"' in text and "customer_id" in text
    svc.mesh_set_project({"domain": "sales"})
    assert svc.platform_landing("dev")["sources"][1]["readers"] == [{"project": "crm", "entities": ["bronze.erp_customers"]}]

    overview = svc.platform_overview("dev")
    assert overview["me"]["admin"] and overview["counts"]["domains"] == 1
    assert [s["name"] for s in overview["landing"]["sources"]] == ["crm", "erp", "hr"]


def test_project_key_tables_come_from_the_platform_once_keys_are_there(svc):
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["dev"]["keys"] = "platform"
    svc._write_environments(project, envs)
    with pytest.raises(GateError, match="Request it"):
        svc.meta_new_key_table("vendor")
    out = svc.platform_create_key_table("vendor", "vendor_id", "A vendor")
    assert [e["ok"] for e in out["environments"]] == [True, True, True]
    keys = svc.meta_new_key_table("vendor")
    assert "vendor" in [k["name"] for k in keys["central_key_tables"]]
    assert "vendor" in [k["name"] for k in keys["key_tables"]]


def test_data_products_show_what_the_open_project_may_link(svc):
    for d in ("Sales", "Finance"):
        svc.platform_save_domain({"name": d})
    svc.mesh_set_project({"domain": "finance"})
    cat = svc._catalog()
    cat.call("register_project", project={"name": "shop", "domain": "sales"})
    port = [{"port": "customers", "entity": "gold.dim_customer"}]
    for name, access in (("c360", "domain"), ("stores", "public"), ("wip", "public")):
        cat.call("register_product", product={"project": "shop", "name": name, "domain": "sales", "access": access,
                                              "status": "draft" if name == "wip" else "published"}, ports=port, env="prod")
    out = svc.platform_products()
    by = {p["name"]: p for p in out["products"]}
    assert "wip" not in by
    assert by["stores"]["can_link"] and not by["c360"]["can_link"] and by["c360"]["why"] == "request"
    svc.platform_request("access", "shop/c360", "Invoices by customer")
    assert {p["name"]: p for p in svc.platform_products()["products"]}["c360"]["why"] == "requested"


# --------------------------------------------------------------------------- domains from Fabric

FABRIC_DOMAINS = [
    {"id": "f-sales", "displayName": "Sales", "description": "Sales in Fabric"},
    {"id": "f-emea", "displayName": "Sales EMEA", "parentDomainId": "f-sales"},
    {"id": "f-fin", "displayName": "Finance & Risk"},
]


def test_fabric_domains_import_link_by_name_and_keep_owners(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "ana@example.com", "description": "Ours"})
    out = platform.call("import_fabric_domains", domains=FABRIC_DOMAINS)
    assert out["linked"] == ["sales"] and sorted(out["added"]) == ["finance-risk", "sales-emea"]
    got = {d["id"]: d for d in platform.call("list_domains")}
    assert got["sales"]["fabric_domain"] == "f-sales" and got["sales"]["owners"] == "ana@example.com"
    assert got["sales"]["description"] == "Ours" and got["sales-emea"]["parent_id"] == "sales"
    # renamed and moved in Fabric: the same catalog id follows it, so projects keep their domain
    platform.call("register_project", project={"name": "crm", "domain": "sales", "subdomain": "sales-emea"})
    moved = [FABRIC_DOMAINS[0], {"id": "f-emea", "displayName": "Europe", "parentDomainId": "f-sales"}]
    out = platform.call("import_fabric_domains", domains=moved)
    assert out["updated"] == ["sales-emea"] and out["unchanged"] == ["sales"] and out["gone"] == ["finance-risk"]
    got = {d["id"]: d for d in platform.call("list_domains")}
    assert got["sales-emea"]["name"] == "Europe" and "finance-risk" in got  # gone from Fabric, never deleted here


def test_fabric_domains_are_locked_and_admin_only(platform):
    platform.call("import_fabric_domains", domains=FABRIC_DOMAINS)
    with pytest.raises(core.PlatformError, match="comes from Fabric"):
        platform.call("save_domain", domain={"id": "sales", "name": "Revenue"})
    saved = platform.call("save_domain", domain={"id": "sales", "name": "Sales", "owners": "lee@example.com"})
    assert saved["fabric_domain"] == "f-sales" and saved["owners"] == "lee@example.com"
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        _as(platform, "ana@example.com").call("import_fabric_domains", domains=FABRIC_DOMAINS)
    deep = [{"id": "a", "displayName": "A"}, {"id": "b", "displayName": "B", "parentDomainId": "a"},
            {"id": "c", "displayName": "C", "parentDomainId": "b"}]
    assert platform.call("import_fabric_domains", domains=deep)["skipped"] == ["C"]


class _FakeFabric:
    def __init__(self, domains=None, workspaces=None, status=200):
        self.domains, self.workspaces, self.status = domains or [], workspaces or {}, status

    def list_admin_domains(self):
        from fabric_desktop.fabric_api import FabricError
        if self.status != 200:
            raise FabricError(f"GET /admin/domains failed with {self.status}: Unauthorized")
        return self.domains

    def get_workspace(self, wid):
        return {"id": wid, "displayName": f"WS {wid}", **({"domainId": self.workspaces[wid]} if wid in self.workspaces else {})}


def test_import_needs_a_fabric_admin_and_workspaces_suggest_the_domain(svc, monkeypatch):
    monkeypatch.setattr(Service, "_fabric_admin", lambda self: _FakeFabric(status=403))
    with pytest.raises(PermissionError, match="Fabric administrator"):
        svc.platform_import_fabric_domains()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self: _FakeFabric(FABRIC_DOMAINS))
    out = svc.platform_import_fabric_domains()
    assert out["fabric"] == 3 and len(out["domains"]) == 3
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["test"] = {"workspace_id": "ws-test"}
    envs["prod"] = {"workspace_id": "ws-prod", "workspaces": {"ingest": {"id": "ws-ingest", "name": "Ingest"}}}
    svc._write_environments(project, envs)
    monkeypatch.setattr(Service, "fabric", property(lambda self: _FakeFabric(
        workspaces={"ws-test": "f-unknown", "ws-prod": "f-emea"})))
    got = svc.mesh_workspace_domains()
    assert got["suggested"] == {"domain": "sales", "subdomain": "sales-emea"}
    assert got["unnamed"] == ["f-unknown"] and got["imported"]
    assert {(r["env"], r["designation"], r["domain"]) for r in got["workspaces"]} == {
        ("test", "", None), ("prod", "", "sales-emea"), ("prod", "ingest", None)}


def test_mesh_map_lists_domains_projects_products_and_links(svc):
    for d in ({"name": "Sales"}, {"name": "Finance"}):
        svc.platform_save_domain(d)
    svc.mesh_set_project({"domain": "sales"})
    cat = svc._catalog()
    cat.call("register_project", project={"name": "ledger", "domain": "finance"})
    cat.call("register_product", product={"project": "crm", "name": "c360", "domain": "sales", "access": "public",
                                          "status": "published"}, ports=[], env="prod")
    cat.call("register_product", product={"project": "crm", "name": "wip", "status": "draft"}, ports=[], env="prod")
    cat.call("register_link", consumer="ledger", alias="customers", product_id="crm/c360", port="customer",
             reads={"prod": "prod"})
    m = svc.mesh_map()
    assert m["me"] == "crm" and [p["id"] for p in m["products"]] == ["crm/c360"]
    assert m["links"] == [{"from": "ledger", "product": "crm/c360", "to": "crm"}]
    assert {p["name"]: p["domain_id"] for p in m["projects"]} == {"crm": "sales", "ledger": "finance"}
