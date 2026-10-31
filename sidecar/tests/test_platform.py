"""The platform (keys, domains, products, catalog text) and the data mesh, on the SQLite platform."""

import json
import threading
import types

import polars as pl
import pytest
import requests

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
    monkeypatch.setattr(core, "require_admin", lambda *a: None)
    conn = _SqlServer()
    rows = [[i, f"bk{i}", "2026-01-01"] for i in range(1, 2501)]
    out = core.import_keys(core.Db(conn, "tsql"), "customer", rows)
    inserts = [p for sql, p in conn.statements if "IDENTITY_INSERT" in sql]
    assert out == {"imported": 2500, "already_there": 0}
    assert sum(len(p) for p in inserts) == 2500 * 3 and len(inserts) == 5
    keys_sql = [sql for sql, _ in conn.statements if "[audit]" not in sql]  # the import's audit row comes last (#1194)
    assert "RESEED, 2500" in keys_sql[-1]


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
    with pytest.raises(core.PlatformError, match="Only platform admins can copy keys in"):
        _as(platform, "eve@example.com").call("import_keys", name="customer", rows=[[5, "x", "2026-01-01"]])
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
    platform.call("register_project", project={"name": "crm"})
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


GATEWAY_PAGE = b"<html>\n<head><title>502 Bad Gateway</title></head>\n<body>Bad Gateway</body>\n</html>"


def answering(status, content=b"", reason=""):
    """A FabricPlatform whose UDF_Platform answers every call with this status and body."""
    resp = requests.Response()
    resp.status_code, resp._content, resp.reason, resp.encoding = status, content, reason, "utf-8"
    resp._content_consumed = True  # read in full, as requests leaves a response it has read

    class Session:
        def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002 - requests' own name
            return resp

    return FabricPlatform(lambda: "tok", "ws", "fn", session=Session(), sleep=lambda _s: None)


@pytest.mark.parametrize("status,content,reason", [
    (502, GATEWAY_PAGE, "Bad Gateway"),
    (503, b'{"errors": [{"message": "Service Unavailable"}]}', "Service Unavailable"),
    (500, b"", "Internal Server Error"),
    (429, b"Too many requests", "Too Many Requests"),
])
def test_a_5xx_or_429_says_the_platform_couldnt_be_reached(status, content, reason):
    with pytest.raises(requests.HTTPError) as err:
        answering(status, content, reason).call("key_stats", name="customer")
    assert not isinstance(err.value, core.PlatformError), "a passing outage isn't the platform's own answer"
    assert isinstance(err.value, requests.RequestException), "what the can't-be-reached fallbacks catch"
    assert str(err.value) == (f"UDF_Platform couldn't be reached to key stats: Fabric answered {status} {reason}. "
                              "Try again in a few minutes.")
    assert err.value.response.status_code == status


def test_a_4xx_that_isnt_json_is_a_platform_error_with_the_start_of_the_text():
    with pytest.raises(core.PlatformError) as err:
        answering(400, b"Bad request:\n  the function input isn't valid " + b"x" * 500).call("issue_keys", name="c")
    text = str(err.value)
    assert text.startswith("The platform couldn't issue keys: UDF_Platform answered 400 with something that isn't JSON: "
                           "Bad request: the function input isn't valid xxx")
    assert len(text) < 450


@pytest.mark.parametrize("content,kind", [(b"[1, 2]", "JSON that isn't an object"), (b'"done"', "JSON that isn't an object"),
                                          (b"null", "JSON that isn't an object"),
                                          (GATEWAY_PAGE, "something that isn't JSON")])
def test_a_200_that_isnt_a_json_object_is_a_platform_error(content, kind):
    with pytest.raises(core.PlatformError, match=f"^The platform couldn't key stats: UDF_Platform answered 200 with {kind}: "):
        answering(200, content).call("key_stats", name="customer")


def test_the_401_403_and_404_messages_are_unchanged():
    for status in (401, 403):
        with pytest.raises(PermissionError) as err:
            answering(status, GATEWAY_PAGE).call("key_stats", name="customer")
        assert str(err.value) == ("The platform refused this call. Ask your platform team for at least Viewer "
                                  "on the Platform workspace and Execute on UDF_Platform.")
    with pytest.raises(LookupError) as err:
        answering(404, GATEWAY_PAGE).call("key_stats", name="customer")
    assert str(err.value) == "UDF_Platform wasn't found. Open Platform > Settings, then check the platform's workspace."


def test_a_4xx_or_failed_json_answer_still_names_its_errors():
    with pytest.raises(core.PlatformError, match=r"^The platform couldn't issue keys: name is required; too many$"):
        answering(400, b'{"errors": [{"message": "name is required"}, "too many"]}').call("issue_keys", name="")
    with pytest.raises(core.PlatformError, match=r"^The platform couldn't key stats: no table$"):
        answering(200, b'{"status": "Failed", "errors": [{"message": "no table"}]}').call("key_stats", name="c")
    assert answering(200, b'{"output": {"created": true}}').call("key_stats", name="c") == {"created": True}
    assert answering(200, b"").call("key_stats", name="c") == {}


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


def test_a_prod_publish_that_changes_a_product_tells_its_followers(svc, monkeypatch):
    svc.meta_set_environment("prod", target="local")
    who = {"user": "chris@example.com"}
    monkeypatch.setattr(svc, "_user", lambda: who["user"])
    svc.platform_save_domain({"name": "Sales"})
    svc.mesh_set_project({"domain": "sales"})
    product = {"name": "c360", "access": "public", "status": "published", "description": "Customers", "owner": "Chris",
               "ports": [{"name": "customers", "entity": "gold.dim_customer"}]}
    svc.mesh_save_product(product)
    svc.mesh_save_about("gold.dim_customer", {"description": "One row per customer"})
    for c in svc.mesh_about("gold.dim_customer")["columns"]:
        svc.mesh_save_about("gold.dim_customer", {"description": f"The {c['name']}"}, c["name"])
    sent = []
    monkeypatch.setattr(svc, "platform_notify", lambda events: sent.extend(events) or {"sent": len(events)})
    assert svc.mesh_publish("prod")["published"] == ["c360"]
    assert svc.platform_products()["can_follow"] and not svc.platform_products()["products"][0]["following"]
    assert svc.platform_follow("crm/c360") == {"following": ["crm/c360"]}
    assert svc.platform_products()["products"][0]["following"]
    who["user"] = "ana@example.com"
    svc.platform_follow("crm/c360")
    who["user"] = "chris@example.com"

    svc.mesh_save_product(product | {"version": "2"})
    svc.mesh_publish("dev")
    assert sent == [], "Dev changes aren't news to readers"
    svc.mesh_publish("prod")
    [event] = sent
    assert (event["kind"], event["detail"], event["to"]["people"]) == ("product.changed", "Version 1 to 2", ["ana@example.com"]), \
        "the promoter isn't told about their own change"
    assert svc.platform_follow("crm/c360", follow=False) == {"following": []}


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


def test_no_lh_keys_merge_once_keys_come_from_the_platform(svc, monkeypatch):
    assert svc.meta_entity("silver.customers")["keys_sql"].startswith("-- Surrogate keys")
    assert svc.meta_entity("silver.customers")["keys_from"] == ""
    svc.meta_keys_to_platform("dev")
    ent = svc.meta_entity("silver.customers")
    # A MERGE into LH_Keys would issue keys SQL_Platform never sees, so there is none to copy or start from.
    assert ent["keys_sql"] is None and ent["keys_from"] == "SQL_Platform"
    assert svc.meta_keys("dev")["lakehouse"] == "SQL_Platform"
    with pytest.raises(GateError, match="come from SQL_Platform"):
        svc.meta_key_notebook("silver.customers")
    monkeypatch.setattr(svc, "_keys_config", lambda: {"shared": True})
    assert svc.meta_entity("silver.customers")["keys_from"] == "SQL_Keys"
    assert svc.meta_keys("dev")["lakehouse"] == "SQL_Keys"
    # Test still issues keys in LH_Keys, so its MERGE stays.
    assert svc.meta_entity("silver.customers", env="test")["keys_from"] == ""


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
    # Only a sign-in name can match a caller, and removing a non-admin isn't recorded as a removal.
    before = [e for e in ana.call("audit", limit=500) if e["action"] in ("add admin", "remove admin")]
    for bad in ("ana", "Ana Lopez", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", ""):
        with pytest.raises(core.PlatformError, match="sign-in name"):
            ana.call("add_admin", user=bad)
    with pytest.raises(core.PlatformError, match="lee@example.com isn't a platform admin"):
        ana.call("remove_admin", user="Lee@Example.com")
    assert [a["user_name"] for a in ana.call("list_admins")] == ["ana@example.com"]
    assert [e for e in ana.call("audit", limit=500) if e["action"] in ("add admin", "remove admin")] == before


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


def test_data_requests_go_to_the_domain_or_product_owners(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "Dee@example.com"})
    platform.call("save_domain", domain={"name": "Finance"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    port = [{"port": "customers", "entity": "gold.dim_customer", "workspace_id": "w", "lakehouse_id": "l", "table": "dim_customer"}]
    platform.call("register_product", product={"project": "crm", "name": "c360", "domain": "sales", "access": "public",
                                               "status": "published", "owner": "Bo@example.com"}, ports=port, env="prod")
    ana, bo, dee = (_as(platform, f"{n}@example.com") for n in ("ana", "bo", "dee"))

    # A new product: the domain's owners decide; nothing is checked against a product.
    new = ana.call("request", kind="data", target="Sales", project="ledger", reason="Churn by region",
                   detail={"what": "new", "needed_by": "2026-11-30"})
    assert (new["target"], new["detail"]["domain_name"], new["detail"]["owners"]) == ("sales", "Sales", "Dee@example.com")
    with pytest.raises(core.PlatformError, match="already a request waiting"):
        ana.call("request", kind="data", target="sales", project="ledger", reason=" churn  by REGION ",
                 detail={"what": "new"})
    assert [r["can_decide"] for r in dee.call("list_requests", status="waiting")] == [True]
    assert [r["can_decide"] for r in bo.call("list_requests", status="waiting")] == [False]
    with pytest.raises(core.PlatformError, match="domain's owners or a platform admin"):
        bo.call("decide_request", id=new["id"], approve=True)
    done = dee.call("decide_request", id=new["id"], approve=True, note="In Prod by 30 November")
    assert (done["status"], done["note"]) == ("approved", "In Prod by 30 November")

    # A change to a product: its owner decides.
    chg = ana.call("request", kind="data", target="crm/c360", project="ledger", reason="Add a region column",
                   detail={"what": "change"})
    assert chg["detail"]["product_name"] == "c360"
    with pytest.raises(core.PlatformError, match="product's owner or a platform admin"):
        dee.call("decide_request", id=chg["id"], approve=True)
    assert bo.call("decide_request", id=chg["id"], approve=False, note="Region is in geo/regions")["status"] == "declined"

    for kwargs, why in (({"target": "sales", "reason": "x", "detail": {"what": "both"}}, "new data product or a change"),
                        ({"target": "sales", "reason": " ", "detail": {"what": "new"}}, "Say what you need"),
                        ({"target": "nowhere", "reason": "x", "detail": {"what": "new"}}, "Pick the domain"),
                        ({"target": "crm/none", "reason": "x", "detail": {"what": "change"}}, "isn't a published"),
                        ({"target": "sales", "reason": "x", "detail": {"what": "new", "needed_by": "30/11"}}, "Needed by")):
        with pytest.raises(core.PlatformError, match=why):
            ana.call("request", kind="data", **kwargs)


def test_data_request_notices_name_who_decides():
    from fabric_desktop import governance_notices as gn
    new = {"id": "r1", "kind": "data", "target": "sales", "detail": {"what": "new", "domain_id": "sales", "owners": "Dee@example.com"}}
    event = gn.request_waiting(new, [])
    assert (event["title"], event["domain"], event["to"]["people"]) == ("Data request to sales", "sales", ["dee@example.com"])
    chg = {"id": "r2", "kind": "data", "target": "crm/c360", "detail": {"what": "change", "domain_id": "sales"}}
    event = gn.request_waiting(chg, [{"id": "crm/c360", "owner": "bo@example.com", "domain_id": "sales"}])
    assert event["to"]["people"] == ["bo@example.com"]


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
    platform.call("set_project_member", project="ledger", user="ana@example.com", role="member")
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
    # The revoke row keeps what gave the access, since the grant row is gone (#1352).
    revoked = next(e for e in bo.call("audit", limit=50) if e["action"] == "revoke access")
    detail = json.loads(revoked["detail"])
    assert detail.pop("granted_at") and detail == {"project": "ledger", "request": acc["id"], "granted_by": "bo@example.com"}
    # Taking it away again finds nothing to take: it says so and the audit trail gains no second revoke (#1237).
    revokes = len([e for e in bo.call("audit", limit=500) if e["action"] == "revoke access"])
    with pytest.raises(core.PlatformError, match="ledger has no access to crm/c360 to take away"):
        bo.call("revoke_grant", product_id="crm/c360", project="ledger")
    assert len([e for e in bo.call("audit", limit=500) if e["action"] == "revoke access"]) == revokes == 1
    with pytest.raises(core.PlatformError, match="owner or a platform admin"):  # the role check comes first
        ana.call("revoke_grant", product_id="crm/c360", project="nowhere")

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
    def __init__(self, domains=None, workspaces=None, status=200, roles=None, unreadable=()):
        self.domains, self.workspaces, self.status = domains or [], workspaces or {}, status
        self.roles, self.unreadable, self.throttled = roles or {}, set(unreadable), set()

    def list_admin_domains(self):
        from fabric_desktop.fabric_api import FabricError
        if self.status != 200:
            raise FabricError(f"GET /admin/domains failed with {self.status}: Unauthorized", status=self.status)
        return self.domains

    def list_admin_domain_role_assignments(self, domain_id):
        from fabric_desktop.fabric_api import FabricError
        if domain_id in self.unreadable:
            raise FabricError(f"GET /admin/domains/{domain_id}/roleAssignments failed with 404: NotFound")
        if domain_id in self.throttled:
            raise FabricError("GET roleAssignments failed with 429: TooManyRequests", status=429)
        return self.roles.get(domain_id, [])

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


def _admin(name, kind="User", role="Admins"):
    return {"id": f"ra-{name}", "role": role,
            "principal": {"id": f"obj-{name}", "displayName": name, "type": kind,
                          "userDetails": {"userPrincipalName": f"{name.lower()}@example.com"}}}


def test_import_reads_each_domains_fabric_admins_as_owners_from_fabric(svc, monkeypatch):
    roles = {"f-sales": [_admin("Ana"), _admin("Sales leads", "Group"), _admin("Bo", role="Contributors")]}
    fake = _FakeFabric(FABRIC_DOMAINS, roles=roles, unreadable={"f-fin"})
    monkeypatch.setattr(Service, "_fabric_admin", lambda self: fake)
    out = svc.platform_import_fabric_domains()
    assert out["owners_unread"] == ["Finance & Risk"]
    got = {d["id"]: d for d in out["domains"]}
    sales = got["sales"]
    assert json.loads(sales["fabric_owners"]) == [{"name": "Ana", "type": "User"}, {"name": "Sales leads", "type": "Group"}]
    assert "@" not in sales["fabric_owners"]  # names only: no email is stored
    assert not sales["owners"] and not got["sales-emea"]["fabric_owners"]  # only Admins, and the platform's owners stay unset
    # a later import that can't read a domain's admins keeps what it had; a change to the admins is an update
    fake.unreadable = {"f-sales"}
    again = svc.platform_import_fabric_domains()
    assert again["result"]["unchanged"].count("sales") == 1
    assert json.loads({d["id"]: d for d in svc._catalog().call("list_domains")}["sales"]["fabric_owners"])[0]["name"] == "Ana"
    fake.unreadable, fake.roles = set(), {"f-sales": [_admin("Cy")]}
    assert "sales" in svc.platform_import_fabric_domains()["result"]["updated"]
    assert {d["id"]: d for d in svc.mesh_map()["domains"]}["sales"]["fabric_owners"] == '[{"name": "Cy", "type": "User"}]'
    fake.throttled = {"f-sales"}
    assert svc.platform_import_fabric_domains()["owners_unread"] == ["Sales (Fabric is busy: import again later)"]


def test_fabric_owners_column_is_added_to_a_platform_made_before(platform):
    with platform._db() as db:
        db.run(f"ALTER TABLE {db.t('catalog', 'domain')} DROP COLUMN fabric_owners")
        db.commit()
        assert "fabric_owners" not in core._columns(db, "domain")
    platform.call("import_fabric_domains", domains=[{**FABRIC_DOMAINS[0], "admins": [{"name": "Ana", "type": "User"}]}])
    with platform._db() as db:
        assert "fabric_owners" in core._columns(db, "domain")
    assert platform.call("list_domains")[0]["fabric_owners"] == '[{"name": "Ana", "type": "User"}]'


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


# --------------------------------------------------------------------------- one list of source systems (UX-009)


def test_a_platform_made_before_source_ids_gets_them_keeping_its_sources(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    LocalPlatform(path, lambda: "chris@example.com")
    with sqlite3.connect(path) as conn:  # the source table as schema 4 made it
        conn.execute('DROP TABLE "catalog__source"')
        conn.execute('CREATE TABLE "catalog__source" (name TEXT NOT NULL PRIMARY KEY, title TEXT, description TEXT, '
                     'owner TEXT, expected_hours TEXT, connections TEXT, updated_at TEXT, updated_by TEXT)')
        conn.executemany('INSERT INTO "catalog__source" (name, title, connections) VALUES (?, ?, ?)',
                         [("sap", "SAP ERP", "{}"), ("crm", "Dynamics", "{}")])
    platform = LocalPlatform(path, lambda: "chris@example.com")
    got = {s["name"]: s for s in platform.call("list_sources")}
    assert (got["crm"]["source_id"], got["crm"]["code"], got["crm"]["title"]) == (1, "CRM", "Dynamics")
    assert (got["sap"]["source_id"], got["sap"]["code"], got["sap"]["active"]) == (2, "SAP", True)
    # Saving again keeps the id; a new source takes the next one.
    platform.call("save_source", source={"name": "sap", "title": "SAP S/4", "system_type": "SAP"})
    platform.call("save_source", source={"code": "HR"})
    got = {s["name"]: s for s in platform.call("list_sources")}
    assert got["sap"]["source_id"] == 2 and got["sap"]["system_type"] == "SAP" and got["hr"]["source_id"] == 3
    with pytest.raises(core.PlatformError, match="folder name in any case"):
        platform.call("save_source", source={"name": "erp", "code": "SAP"})


def test_moving_sources_to_the_platform_keeps_their_ids_and_reports_clashes(platform):
    platform.call("save_source", source={"code": "ERP"})  # id 1
    moved = platform.call("adopt_sources", project="crm", systems=[
        {"id": 7, "code": "Shop", "name": "Online shop", "owner": "ana", "system_type": "Files", "active": True},
        {"id": 1, "code": "ERP", "name": "ERP"},
        {"id": 2, "code": "CRM", "name": "Dynamics"},
        {"id": 7, "code": "POS", "name": "Tills"}])
    assert moved["moved"] == ["Shop", "CRM"] and moved["already"] == ["ERP"]
    assert moved["conflicts"] == ["POS: id 7 is already shop in the platform"]
    got = {s["name"]: s for s in moved["sources"]}
    assert (got["shop"]["source_id"], got["shop"]["code"], got["shop"]["title"]) == (7, "Shop", "Online shop")
    again = platform.call("adopt_sources", systems=[{"id": 3, "code": "CRM"}])
    assert again["moved"] == [] and again["conflicts"] == ["CRM is id 2 in the platform but 3 in this project"]
    # Only platform admins move sources.
    db = platform._db().__enter__()
    try:
        with pytest.raises(core.PlatformError, match="Only platform admins"):
            core.adopt_sources(db, [{"id": 9, "code": "X"}], by="ana@example.com")
    finally:
        db.conn.close()


def test_a_project_reads_platform_sources_with_the_platform_ids(svc):
    k = svc.meta_keys(platform=True)
    assert [s["code"] for s in k["source_systems"]] == ["CRM", "SHOP"]
    assert all(s["platform"] is None for s in k["source_systems"]) and k["platform_sources"] == []

    # Move sources to the platform: the platform's list now has the project's ids.
    res = svc.meta_sources_move()
    assert res["moved"] == ["CRM", "SHOP"] and res["conflicts"] == []
    k = res["keys"]
    assert [(p["code"], p["source_id"], p["read"]) for p in k["platform_sources"]] == [("CRM", 1, True), ("SHOP", 2, True)]
    assert k["source_systems"][0]["platform"]["source_id"] == 1 and k["source_problems"] == []

    # A source a platform admin adds is read with its platform id, and the project keeps its connection.
    svc.platform_save_source({"code": "SAP", "title": "SAP ERP", "owner": "Platform team", "system_type": "SAP"})
    k = svc.meta_source_read("sap")
    sap = next(s for s in k["source_systems"] if s["code"] == "SAP")
    assert (sap["id"], sap["name"], sap["owner"]) == (3, "SAP ERP", "Platform team")
    with pytest.raises(ValueError, match="already reads SAP"):
        svc.meta_source_read("sap")
    sap["connections"]["dev"]["server_host"] = "sap-dev"
    k = svc.meta_save_source_system(sap | {"name": "Renamed here", "owner": "me"}, original_id=3)
    sap = next(s for s in k["source_systems"] if s["code"] == "SAP")
    assert (sap["name"], sap["owner"], sap["connections"]["dev"]["server_host"]) == ("SAP ERP", "Platform team", "sap-dev")

    # Ids that disagree are reported, never changed.
    svc.platform_save_source({"code": "HR"})  # id 4 in the platform
    svc.meta_save_source_system({"id": 9, "code": "HR", "name": "HR"})
    assert svc.meta_keys(platform=True)["source_problems"][0].startswith("HR is id 4 in the platform but 9 in this project")


def test_listing_sources_on_a_platform_not_yet_repaired_changes_nothing(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    platform = LocalPlatform(path, lambda: "chris@example.com")
    with sqlite3.connect(path) as conn:  # schema 4's source table, as a Fabric platform has it before Finish or repair
        conn.execute('DROP TABLE "catalog__source"')
        conn.execute('CREATE TABLE "catalog__source" (name TEXT NOT NULL PRIMARY KEY, title TEXT, description TEXT, '
                     'owner TEXT, expected_hours TEXT, connections TEXT, updated_at TEXT, updated_by TEXT)')
        conn.executemany('INSERT INTO "catalog__source" (name, title, connections) VALUES (?, ?, ?)',
                         [("sap", "SAP ERP", "{}"), ("crm", "Dynamics", "{}")])
    with platform._db() as db:
        got = {s["name"]: s for s in core.list_sources(db)}
    assert (got["crm"]["source_id"], got["crm"]["code"], got["sap"]["source_id"], got["sap"]["active"]) == (1, "CRM", 2, True)
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info("catalog__source")')}
    assert not cols & set(core._SOURCE_ADDED), "a read added columns"


def test_owner_coverage_lists_what_has_no_owner_no_backup_or_a_quiet_owner(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "Dee@example.com, ed@example.com"})
    platform.call("save_domain", domain={"name": "Finance"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    for name, owner, status in (("c360", "bo@example.com", "published"), ("draft", "", "draft"), ("orders", "Sales team", "published")):
        platform.call("register_product", product={"project": "crm", "name": name, "domain": "sales", "access": "public",
                                                   "status": status, "owner": owner}, ports=[], env="prod")
    platform.call("save_source", source={"name": "shopify", "owner": "lee@example.com; bo@example.com"})
    for who in ("dee", "ed", "lee"):
        _as(platform, f"{who}@example.com").call("note_seen")
    old = (core.dt.datetime.now(core.dt.timezone.utc) - core.dt.timedelta(days=61)).isoformat(timespec="seconds")
    with platform._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'person')} SET last_seen = ? WHERE user_name = ?", (old, "ed@example.com"))
        db.commit()
    bo = _as(platform, "bo@example.com")
    bo.call("note_seen")
    assert bo.call("note_seen")["last_seen"], "a second call the same day writes nothing new"

    c = platform.call("owner_coverage")
    gaps = {(g["kind"], g["id"]): g["problems"] for g in c["gaps"]}
    assert c["gaps"][0]["problems"] == ["No owner"], "nobody owning it comes first"
    assert gaps == {("domain", "finance"): ["No owner"],
                    ("domain", "sales"): ["ed@example.com hasn't used the platform for 61 days"],
                    ("product", "crm/c360"): ["No second owner to cover"],
                    ("product", "crm/orders"): ["No second owner to cover"]}
    assert next(g for g in c["gaps"] if g["id"] == "crm/orders")["unchecked"] == ["sales team"]
    assert (c["checked"], c["covered"], c["no_owner"]) == (5, 1, 1), "drafts aren't checked; shopify is covered"
    off = platform.call("owner_coverage", presence=False)
    assert off["presence"] is False and ("domain", "sales") not in {(g["kind"], g["id"]) for g in off["gaps"]}
    assert not any("used the platform" in p for g in off["gaps"] for p in g["problems"]), "no quiet check without presence"


def test_owner_coverage_reads_without_creating_catalog_person(platform):
    with platform._db() as db:
        db.run(f"DROP TABLE {db.t('catalog', 'person')}")
        db.commit()
    platform.call("save_domain", domain={"name": "Sales", "owners": "dee@example.com"})
    c = platform.call("owner_coverage")
    assert c["gaps"][0]["problems"] == ["No second owner to cover", "dee@example.com hasn't used the platform yet"]
    with platform._db() as db:
        assert not db.exists("catalog", "person"), "a read created the table"


def test_presence_is_noted_in_the_background_only_when_policy_turns_it_on(tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    from fabric_desktop.central import service as central_service
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(s, "_user", lambda: "dee@example.com")
    started = []
    monkeypatch.setattr(central_service.threading, "Thread",
                        lambda target, args, **kw: types.SimpleNamespace(start=lambda: started.append(target(*args))))
    assert app_settings.DEFAULTS["platform_presence"] is False and app_settings.RESTRICTED["platform_presence"] is False
    monkeypatch.setattr(app_settings, "value", lambda key: False if key == "platform_presence" else app_settings.DEFAULTS[key])
    assert s.platform_note_seen() == {"noted": False, "reason": "off"} and not started
    assert s.platform_owner_coverage()["presence"] is False
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "platform_presence" else app_settings.DEFAULTS[key])
    assert s.platform_note_seen() == {"noted": True} and len(started) == 1
    assert s.platform_note_seen() == {"noted": False, "reason": "already"}, "once a day per run"
    with s._catalog()._db() as db:
        assert [r["user_name"] for r in db.rows(f"SELECT * FROM {db.t('catalog', 'person')}")] == ["dee@example.com"]
    assert s.platform_owner_coverage()["presence"] is True


def test_access_reviews_flag_old_grants_for_their_owners_and_take_nothing_away(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    platform.call("register_product", product={"project": "crm", "name": "c360", "domain": "sales", "access": "private",
                                               "status": "published", "owner": "Bo@example.com"}, ports=[], env="prod")
    bo, ana = _as(platform, "bo@example.com"), _as(platform, "ana@example.com")
    for project in ("ledger", "billing"):
        req = ana.call("request", kind="access", target="crm/c360", project=project, reason="Invoices")
        bo.call("decide_request", id=req["id"], approve=True)
    fresh = bo.call("access_reviews")
    assert (fresh["due"], [g["project"] for g in fresh["grants"]]) == (0, ["billing", "ledger"])
    assert ana.call("access_reviews")["grants"] == [], "only owners and admins review"
    assert len(platform.call("access_reviews")["grants"]) == 2, "a platform admin sees every product's"

    # Granted 100 days ago: due, and still there.
    old = (core.dt.datetime.now(core.dt.timezone.utc) - core.dt.timedelta(days=100)).isoformat(timespec="seconds")
    with platform._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'grant')} SET granted_at = ?", (old,))
        db.commit()
    due = bo.call("access_reviews")
    assert due["due"] == 2 and all(g["due"] and g["days"] >= 100 for g in due["grants"])
    assert sorted(g["project"] for g in bo.call("list_grants", product_id="crm/c360")) == ["billing", "ledger"]

    with pytest.raises(core.PlatformError, match="owner or a platform admin"):
        ana.call("confirm_access", product_id="crm/c360", projects=["ledger"])
    with pytest.raises(core.PlatformError, match="nope has no access"):
        bo.call("confirm_access", product_id="crm/c360", projects=["nope"])
    after = bo.call("confirm_access", product_id="crm/c360", projects=["ledger"])
    ledger = next(g for g in after["grants"] if g["project"] == "ledger")
    assert (after["due"], ledger["due"], ledger["reviewed_by"]) == (1, False, "bo@example.com")
    assert after["grants"][0]["project"] == "billing", "the longest unconfirmed comes first"

    bo.call("revoke_grant", product_id="crm/c360", project="ledger")
    assert [g["project"] for g in bo.call("access_reviews")["grants"]] == ["billing"]


def test_following_a_product_and_hearing_what_a_publish_changed(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    platform.call("register_project", project={"name": "ledger", "domain": "sales"})
    port = {"port": "customers", "entity": "gold.dim_customer", "workspace_id": "w", "lakehouse_id": "l", "table": "dim_customer",
            "columns": {"id": "Int64", "name": "Utf8", "city": "Utf8"}}
    product = {"project": "crm", "name": "c360", "domain": "sales", "access": "public", "status": "published", "owner": "bo@example.com"}
    first = platform.call("register_product", product=product, ports=[port], env="prod")
    assert first["changes"] == [] and "followers" not in first
    ana, cy = _as(platform, "Ana@example.com"), _as(platform, "cy@example.com")
    assert ana.call("follow_product", product_id="crm/c360") == ["crm/c360"]
    assert ana.call("follow_product", product_id="crm/c360") == ["crm/c360"], "following twice is one follow"
    with pytest.raises(core.PlatformError, match="isn't a published"):
        ana.call("follow_product", product_id="crm/none")
    platform.call("set_project_member", project="ledger", user="cy@example.com", role="member")
    cy.call("register_link", consumer="ledger", alias="cust", product_id="crm/c360", port="customers",
            reads={"dev": "prod", "test": "prod", "prod": "prod"})
    assert platform.call("product_followers", product_id="crm/c360") == ["ana@example.com", "cy@example.com"]

    again = platform.call("register_product", product=product, ports=[port], env="prod")
    assert again["changes"] == [], "an unchanged publish says nothing"
    changed = dict(port, columns={"id": "Int64", "name": "Utf8", "region": "Utf8", "city": "Int64"})
    out = platform.call("register_product", product=dict(product, version="2", status="deprecated", deprecated_on="2027-01-31"),
                        ports=[changed], env="prod")
    assert out["changes"] == ["Deprecated, stops 2027-01-31", "Version 1 to 2", "Port customers: region added; city retyped"]
    assert out["followers"] == ["ana@example.com", "cy@example.com"]
    gone = dict(port, columns={"id": "Int64"})
    assert platform.call("register_product", product=dict(product, version="2", status="deprecated", deprecated_on="2027-01-31"),
                         ports=[gone], env="prod")["changes"] == ["Port customers: city, name, region removed"]

    assert ana.call("unfollow_product", product_id="crm/c360") == []
    assert platform.call("product_followers", product_id="crm/c360") == ["cy@example.com"]
    with pytest.raises(core.PlatformError, match="owner or a platform admin"):
        cy.call("product_followers", product_id="crm/c360")
    assert _as(platform, "bo@example.com").call("product_followers", product_id="crm/c360") == ["cy@example.com"]


def test_reads_of_follows_and_reviews_create_no_tables(platform):
    platform.call("register_project", project={"name": "crm"})
    platform.call("register_product", product={"project": "crm", "name": "c360", "access": "public", "status": "published",
                                               "owner": "bo@example.com"}, ports=[], env="dev")
    with platform._db() as db:  # a platform made before following and access reviews
        for table in ("follow", "grant_review"):
            db.run(f"DROP TABLE {db.t('catalog', table)}")
        db.commit()
    assert platform.call("list_follows") == [] and platform.call("product_followers", product_id="crm/c360") == []
    assert platform.call("access_reviews")["grants"] == []
    before = len(platform.call("audit", limit=500))
    assert platform.call("confirm_access", product_id="crm/c360", projects=[])["grants"] == []
    assert len(platform.call("audit", limit=500)) == before, "an empty confirmation writes no audit row"
    with platform._db() as db:
        assert not db.exists("catalog", "follow") and not db.exists("catalog", "grant_review")


def test_a_product_change_notice_names_its_followers_and_what_changed():
    from fabric_desktop import governance_notices as gn
    from fabric_desktop.workloads import platform_notify as pn
    product = {"id": "crm/c360", "name": "c360", "domain_id": "sales"}
    assert gn.product_changed(product, ["Version 1 to 2"], [], "prod") is None, "nobody to tell"
    assert gn.product_changed(product, [], ["ana@example.com"], "prod") is None, "nothing changed"
    event = pn.validate(gn.product_changed(product, ["Version 1 to 2"], ["Ana@example.com", "not a upn"], "prod"))
    assert (event["kind"], event["title"], event["detail"], event["to"]["people"]) == (
        "product.changed", "c360", "Version 1 to 2", ["ana@example.com"])
    assert event["open"] == {"view": "onelake", "tab": "products", "target": "crm/c360"}
    title, lines = pn.message(event)
    assert title == "Data product changed: c360"
    assert "Version 1 to 2" in lines and lines[-1] == "Open it in KratosForge: Data mesh > Products."


def _owned_product(platform):
    platform.call("save_domain", domain={"name": "Sales"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    product = {"project": "crm", "name": "c360", "domain": "sales", "access": "public", "status": "published",
               "owner": "bo@example.com"}
    platform.call("set_project_member", project="crm", user="bo@example.com", role="member")
    _as(platform, "bo@example.com").call("register_product", product=product, ports=[], env="prod")
    return product


def test_only_a_products_owners_or_an_admin_publish_it_again(platform):
    product = _owned_product(platform)
    eve = _as(platform, "eve@example.com")
    with pytest.raises(core.PlatformError, match="Only crm's members or a platform admin"):
        eve.call("register_product", product=product, ports=[], env="prod")
    platform.call("set_project_member", project="crm", user="eve@example.com", role="member")  # a teammate, not its owner
    with pytest.raises(core.PlatformError, match="Only crm/c360's owners or a platform admin"):
        eve.call("register_product", product=dict(product, owner="eve@example.com", status="deprecated",
                                                  deprecated_on="2020-01-01"), ports=[], env="prod")
    with pytest.raises(core.PlatformError, match="owners or a platform admin"):
        eve.call("register_product", product=product, ports=[], env="dev")  # every environment
    row = [p for p in platform.call("list_products") if p["id"] == "crm/c360"][0]
    assert (row["owner"], row["status"], row["updated_by"]) == ("bo@example.com", "published", "bo@example.com")
    # An owner, in any case, and a platform admin both may.
    _as(platform, "Bo@Example.com").call("register_product", product=dict(product, version="2"), ports=[], env="prod")
    platform.call("register_product", product=dict(product, version="3"), ports=[], env="prod")


def test_the_last_publisher_of_a_product_owned_by_a_team_publishes_it_again(platform):
    product = dict(_owned_product(platform), owner="Customer team")  # a team name: no one to match
    _as(platform, "bo@example.com").call("register_product", product=product, ports=[], env="prod")
    _as(platform, "bo@example.com").call("register_product", product=dict(product, version="2"), ports=[], env="prod")
    platform.call("set_project_member", project="crm", user="eve@example.com", role="member")
    with pytest.raises(core.PlatformError, match="owners or a platform admin"):
        _as(platform, "eve@example.com").call("register_product", product=product, ports=[], env="prod")


def test_a_first_publish_naming_only_other_people_as_owners_is_refused(platform):
    platform.call("register_project", project={"name": "crm"})
    platform.call("set_project_member", project="crm", user="eve@example.com", role="member")
    product = {"project": "crm", "name": "orders", "access": "public", "status": "published", "owner": "bo@example.com"}
    eve = _as(platform, "eve@example.com")
    with pytest.raises(core.PlatformError, match="add yourself to its owners"):
        eve.call("register_product", product=product, ports=[], env="prod")
    assert not [p for p in platform.call("list_products") if p["id"] == "crm/orders"]
    eve.call("register_product", product=dict(product, owner="bo@example.com; eve@example.com"), ports=[], env="prod")
    eve.call("register_product", product=dict(product, name="returns", owner="Orders team"), ports=[], env="prod")


# --------------------------------------------------------------------------- project members


def test_a_projects_members_change_it_and_others_are_refused(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "olu@example.com"})
    bo, eve = _as(platform, "bo@example.com"), _as(platform, "eve@example.com")
    bo.call("register_project", project={"name": "crm", "team": "Customer analytics"})  # the first to record it owns it
    assert bo.call("list_project_members", project="crm")["members"] == [{"user": "bo@example.com", "role": "owner"}]
    for op, kwargs in (("register_project", {"project": {"name": "crm", "team": "Not yours"}}),
                       ("register_product", {"product": {"project": "crm", "name": "c360", "owner": "eve@example.com"},
                                             "ports": [], "env": "prod"}),
                       ("register_link", {"consumer": "crm", "alias": "x", "product_id": "crm/c360", "port": "p", "reads": {}}),
                       ("set_project_member", {"project": "crm", "user": "eve@example.com", "role": "owner"})):
        with pytest.raises(core.PlatformError, match="Only crm's members or a platform admin"):
            eve.call(op, **kwargs)
    assert [p["team"] for p in platform.call("list_projects")] == ["Customer analytics"]
    # An owner adds a member, who may then change the project but not its members.
    bo.call("set_project_member", project="crm", user="Eve@Example.com", role="member")
    eve.call("register_project", project={"name": "crm", "team": "Customers"})
    with pytest.raises(core.PlatformError, match="Only crm's owners or a platform admin"):
        eve.call("set_project_member", project="crm", user="cy@example.com", role="member")
    with pytest.raises(core.PlatformError, match="crm needs an owner"):
        bo.call("set_project_member", project="crm", user="bo@example.com", role="")
    with pytest.raises(core.PlatformError, match="isn't an email"):
        bo.call("set_project_member", project="crm", user="Customer team", role="member")
    # A platform admin changes any project's members.
    platform.call("set_project_member", project="crm", user="eve@example.com", role="")
    assert [m["user"] for m in bo.call("list_project_members", project="crm")["members"]] == ["bo@example.com"]


def test_a_project_from_before_members_starts_with_the_people_who_worked_on_it(platform):
    platform.call("register_project", project={"name": "crm"})
    # Recorded and published before members: the catalog names who did it.
    platform.call("register_product", product={"project": "crm", "name": "c360", "access": "public", "status": "published",
                                               "owner": "bo@example.com; Customer team"}, ports=[], env="prod")
    with platform._db() as db:  # as if from before members
        db.run(f"DELETE FROM {db.t('catalog', 'project_member')}")
        db.commit()
    listed = _as(platform, "ana@example.com").call("list_project_members", project="crm")
    assert listed["earlier"] and [m["user"] for m in listed["members"]] == ["bo@example.com", "chris@example.com"]
    assert not listed["can_change"]
    with pytest.raises(core.PlatformError, match=r"Ask one of its owners \(bo@example.com, chris@example.com\)"):
        _as(platform, "ana@example.com").call("register_project", project={"name": "crm"})
    _as(platform, "bo@example.com").call("register_project", project={"name": "crm", "team": "Customers"})


def test_joining_a_domain_waits_for_its_owners(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "olu@example.com"})
    platform.call("save_domain", domain={"name": "Finance"})
    bo, olu = _as(platform, "bo@example.com"), _as(platform, "olu@example.com")
    out = bo.call("register_project", project={"name": "crm", "domain": "sales"})
    assert out["domain_id"] is None and out["domain_request"]["status"] == "waiting"
    assert out["domain_request"]["detail"]["owners"] == "olu@example.com"
    again = bo.call("register_project", project={"name": "crm", "domain": "sales", "team": "Customers"})  # a promote
    assert again["domain_request"]["id"] == out["domain_request"]["id"], "the same ask isn't sent twice"
    [req] = olu.call("list_requests", status="waiting")
    assert req["kind"] == "join_domain" and req["can_decide"]
    assert not _as(platform, "eve@example.com").call("list_requests", status="waiting")[0]["can_decide"]
    olu.call("decide_request", id=req["id"], approve=True)
    [crm] = platform.call("list_projects")
    assert crm["domain_id"] == "sales"
    # Moving to a domain with no owners waits for a platform admin; asking again for another withdraws the first ask.
    first = bo.call("register_project", project={"name": "crm", "domain": "finance"})["domain_request"]
    assert [r["can_decide"] for r in olu.call("list_requests", status="waiting")] == [False]
    second = bo.call("register_project", project={"name": "crm", "domain": "sales"})
    assert "domain_request" not in second, "back in its own domain: nothing to ask"
    assert [r["status"] for r in platform.call("list_requests") if r["id"] == first["id"]] == ["waiting"]
    platform.call("decide_request", id=first["id"], approve=False)
    # A domain owner, or an admin, joins their own project directly.
    olu.call("register_project", project={"name": "orders", "domain": "sales"})
    assert {p["name"]: p["domain_id"] for p in platform.call("list_projects")}["orders"] == "sales"


def test_project_toml_keeps_the_catalogs_domain_while_a_join_waits(svc):
    svc.platform_save_domain({"name": "Sales"})
    svc.platform_save_domain({"name": "Finance", "owners": "olu@example.com"})
    svc.mesh_set_project({"domain": "sales"})
    catalog = svc._catalog()
    catalog.call("set_project_member", project="crm", user="bo@example.com", role="owner")
    catalog._user = lambda: "bo@example.com"
    out = svc.mesh_set_project({"domain": "finance"})
    assert out["domain_request"]["status"] == "waiting" and out["project"]["domain"] == "sales"
    assert 'domain = "sales"' in (svc._project().root / mesh.PROJECT_FILE).read_text(encoding="utf-8")
    listed = svc.mesh_members()
    assert listed["domain_request"]["mine"] and listed["domain_request"]["detail"]["domain"] == "finance"
    catalog._user = lambda: "olu@example.com"
    catalog.call("decide_request", id=listed["domain_request"]["id"], approve=True)
    assert svc.mesh_members()["domain_request"] is None
    assert [p["domain_id"] for p in svc.mesh_overview()["projects"]] == ["finance"]


def _forget_members(platform):
    with platform._db() as db:
        db.run(f"DELETE FROM {db.t('catalog', 'project_member')}")
        db.commit()


def test_a_project_the_catalog_names_nobody_for_waits_for_an_admin_to_name_its_owner(platform):
    # Recorded signed out on this PC, so the catalog names nobody; then members came.
    _as(platform, "").call("register_project", project={"name": "crm"})
    _forget_members(platform)
    ana = _as(platform, "ana@example.com")
    assert not ana.call("list_project_members", project="crm")["can_change"]
    with pytest.raises(core.PlatformError, match="crm has no owner yet, so only a platform admin"):
        ana.call("register_project", project={"name": "crm", "team": "Mine now"})
    with pytest.raises(core.PlatformError, match="no owner yet"):
        ana.call("set_project_member", project="crm", user="ana@example.com", role="owner")
    assert ana.call("list_project_members", project="crm")["members"] == []
    platform.call("set_project_member", project="crm", user="bo@example.com", role="owner")
    _as(platform, "bo@example.com").call("register_project", project={"name": "crm", "team": "Customers"})


def test_a_name_known_only_from_its_links_isnt_claimed_by_the_first_caller(platform):
    platform.call("register_project", project={"name": "sales"})
    platform.call("register_product", product={"project": "sales", "name": "orders", "access": "public",
                                               "status": "published"}, ports=[], env="prod")
    # A link records no project row: "finance" is in the catalog only as the link's consumer.
    with platform._db() as db:
        db.run(f"INSERT INTO {db.t('catalog', 'link')} (id, consumer_project, alias, product_id, port, reads, updated_at, "
               "updated_by) VALUES ('finance:orders', 'finance', 'orders', 'sales/orders', 'orders', '{}', '', '')")
        db.commit()
    with pytest.raises(core.PlatformError, match="finance has no owner yet"):
        _as(platform, "eve@example.com").call("register_project", project={"name": "finance"})
    with pytest.raises(core.PlatformError, match="finance has no owner yet"):
        _as(platform, "eve@example.com").call("register_link", consumer="finance", alias="orders2",
                                             product_id="sales/orders", port="orders", reads={})
    # A name the catalog has never seen is the caller's, as their sign-in (an email, never an object id).
    _as(platform, "eve@example.com").call("register_project", project={"name": "payroll"})
    assert _as(platform, "eve@example.com").call("list_project_members", project="payroll")["members"] == [
        {"user": "eve@example.com", "role": "owner"}]
    with pytest.raises(core.PlatformError, match="no owner yet"):
        _as(platform, "0b9c6f1e-0000-4000-8000-000000000000").call("register_project", project={"name": "hr"})


def test_a_project_recorded_by_an_object_id_with_a_team_as_owner_isnt_claimed(platform):
    platform.call("register_project", project={"name": "crm"})
    platform.call("register_product", product={"project": "crm", "name": "c360", "access": "public",
                                               "status": "published", "owner": "Customer team"}, ports=[], env="prod")
    with platform._db() as db:  # as if an object id recorded it all, before members
        db.run(f"UPDATE {db.t('catalog', 'project')} SET updated_by = '0b9c6f1e-0000-4000-8000-000000000000'")
        db.run(f"UPDATE {db.t('catalog', 'product')} SET updated_by = '0b9c6f1e-0000-4000-8000-000000000000'")
        db.commit()
    _forget_members(platform)
    mallory = _as(platform, "mallory@example.com")
    listed = mallory.call("list_project_members", project="crm")
    assert listed["members"] == [] and not listed["can_change"]
    with pytest.raises(core.PlatformError, match="crm has no owner yet"):
        mallory.call("register_project", project={"name": "crm", "team": "Mallory"})
    assert mallory.call("list_project_members", project="crm")["members"] == []


def test_catalog_text_is_saved_by_the_projects_members_and_certified_by_its_domains_owners(platform):
    platform.call("save_domain", domain={"name": "Sales", "owners": "olu@example.com"})
    platform.call("register_project", project={"name": "crm", "domain": "sales"})
    platform.call("set_project_member", project="crm", user="bo@example.com", role="owner")
    bo, eve, olu = (_as(platform, u) for u in ("bo@example.com", "eve@example.com", "olu@example.com"))
    with pytest.raises(core.PlatformError, match="isn't in the platform's catalog yet"):
        bo.call("save_enrichment", target="nowhere:gold.x", fields={"description": "mine"})
    for fields in ({"description": "x"}, {"classification": "public"}, {"steward": "eve@example.com"}, {"tags": "a"}):
        with pytest.raises(core.PlatformError, match="Only crm's members or a platform admin can change its catalog text"):
            eve.call("save_enrichment", target="crm:gold.dim_customer#email", fields=fields)
    bo.call("save_enrichment", target="crm:gold.dim_customer", fields={"description": "One row per customer",
                                                                      "classification": "confidential"})
    with pytest.raises(core.PlatformError, match="Only the owners of crm's domain or a platform admin"):
        bo.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": "yes"})
    # The form sends every field: an unchanged certified passes.
    bo.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": "", "description": "Customers"})
    with pytest.raises(core.PlatformError, match="Only crm's members"):
        olu.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": "yes", "description": "Mine"})
    audit = lambda: [a for a in platform.call("audit") if a["action"] == "enrich"]  # noqa: E731
    seen = audit()
    eve.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": ""})  # unchanged: writes nothing
    assert audit() == seen
    assert platform.call("enrichment", project="crm")["crm:gold.dim_customer"]["updated_by"] == "bo@example.com"
    olu.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": "yes"})  # not a member: certifying only
    assert platform.call("enrichment", project="crm")["crm:gold.dim_customer"]["certified"] == "yes"
    with pytest.raises(core.PlatformError, match="Only the owners of crm's domain"):
        bo.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": ""})
    bo.call("save_enrichment", target="crm:gold.dim_customer", fields={"certified": "yes", "tags": "gold"})
    # Without a domain, only an admin certifies.
    platform.call("register_project", project={"name": "hr"})
    platform.call("set_project_member", project="hr", user="bo@example.com", role="owner")
    with pytest.raises(core.PlatformError, match="isn't in a domain yet, so a platform admin decides"):
        bo.call("save_enrichment", target="hr:gold.staff", fields={"certified": "yes"})
    platform.call("save_enrichment", target="hr:gold.staff", fields={"certified": "yes"})


def _sc(lh, name, target, ws="w1"):
    return {"lakehouse_id": lh, "workspace_id": ws, "workspace_name": "Sales", "lakehouse_name": lh, "path": "Tables",
            "name": name, "target": target}


def test_only_a_lakehouses_project_members_or_an_admin_hide_a_shortcut_problem(platform):
    platform.call("register_project", project={"name": "crm"})
    platform.call("set_project_member", project="crm", user="bo@example.com", role="owner")
    platform.call("register_product", product={"project": "crm", "name": "c360", "access": "public", "status": "published"},
                  ports=[{"port": "customers", "entity": "gold.c", "lakehouse_id": "lh1", "workspace_id": "w1"}], env="prod")
    bo, eve = _as(platform, "bo@example.com"), _as(platform, "eve@example.com")
    lakehouses = [{"lakehouse_id": "lh1", "workspace_id": "w1"}, {"lakehouse_id": "lh2", "workspace_id": "w2"}]
    a, b = {"type": "OneLake", "path": "Tables/a"}, {"type": "OneLake", "path": "Tables/b"}
    eve.call("record_shortcuts", lakehouses=lakehouses, shortcuts=[_sc("lh1", "x", a), _sc("lh1", "y", a), _sc("lh2", "z", a, "w2")])
    sid = {s["name"]: s["id"] for s in platform.call("list_seen_shortcuts")}
    platform.call("accept_shortcut_risk", shortcut_id=sid["x"], kind="cross_env", reason="on purpose")
    platform.call("record_shortcut_health", results=[{"id": sid["x"], "health": "broken", "detail": "404"}])
    # Eve isn't a member of crm: her scan reports a repoint and a removal but changes neither.
    out = eve.call("record_shortcuts", lakehouses=lakehouses, shortcuts=[_sc("lh1", "x", b), _sc("lh2", "z", a, "w2")])
    assert sorted((s["change"], s["shortcut_id"]) for s in out["skipped"]) == [("removed", sid["y"]), ("repointed", sid["x"])]
    [reported] = [c for c in out["changes"] if c["kind"] == "repointed"]
    assert reported["by_user"] == "eve@example.com" and reported["note"].startswith("reported")
    seen = {s["name"]: s for s in platform.call("list_seen_shortcuts")}
    assert set(seen) == {"x", "y", "z"} and seen["x"]["health"] == "broken"
    assert [r["shortcut_id"] for r in platform.call("list_shortcut_risks")] == [sid["x"]]
    # Nor can she mark it healthy; anyone may report one broken.
    got = eve.call("record_shortcut_health", results=[{"id": sid["x"], "health": "healthy"},
                                                      {"id": sid["y"], "health": "broken", "detail": "gone"}])
    assert [c["kind"] for c in got] == ["skipped", "broke"] and got[1]["by_user"] == "eve@example.com"
    assert {s["name"]: s["health"] for s in platform.call("list_seen_shortcuts")} == {"x": "broken", "y": "broken", "z": "not_checked"}
    # A lakehouse no project has a product in: an admin's only. In crm's, bo's scan and check apply.
    eve.call("record_shortcuts", lakehouses=lakehouses[1:], shortcuts=[])
    assert "z" in {s["name"] for s in platform.call("list_seen_shortcuts")}
    out = bo.call("record_shortcuts", lakehouses=lakehouses[:1], shortcuts=[_sc("lh1", "x", b)])
    assert not out["skipped"] and {c["kind"] for c in out["changes"]} == {"repointed", "removed"}
    assert platform.call("list_shortcut_risks") == [], "an applied repoint ends the accepted risk"
    assert [c["kind"] for c in bo.call("record_shortcut_health", results=[{"id": sid["x"], "health": "healthy"}])] == []
    assert any(a["action"] == "shortcut scan" and a["by_user"] == "bo@example.com" for a in platform.call("audit"))


def test_a_change_logged_from_the_app_follows_the_same_rule(platform):
    platform.call("register_project", project={"name": "crm"})
    platform.call("set_project_member", project="crm", user="bo@example.com", role="owner")
    platform.call("register_product", product={"project": "crm", "name": "c360", "access": "public", "status": "published"},
                  ports=[{"port": "customers", "entity": "gold.c", "lakehouse_id": "lh1", "workspace_id": "w1"}], env="prod")
    bo, eve = _as(platform, "bo@example.com"), _as(platform, "eve@example.com")
    a, b = {"type": "OneLake", "path": "Tables/a"}, {"type": "OneLake", "path": "Tables/b"}
    eve.call("log_shortcut_change", kind="added", shortcut=_sc("lh1", "x", a), after={"target": a})  # anyone adds
    [x] = platform.call("list_seen_shortcuts")
    platform.call("accept_shortcut_risk", shortcut_id=x["id"], kind="cross_env", reason="on purpose")
    platform.call("record_shortcut_health", results=[{"id": x["id"], "health": "broken", "detail": "404"}])
    with pytest.raises(core.PlatformError, match="only a platform admin, or a member of a project"):
        eve.call("log_shortcut_change", kind="removed", shortcut=_sc("lh1", "x", a), before={"target": a})
    out = eve.call("log_shortcut_change", kind="repointed", shortcut=_sc("lh1", "x", b), before={"target": a}, after={"target": b})
    assert out["note"].startswith("reported") and out["by_user"] == "eve@example.com"
    [x] = platform.call("list_seen_shortcuts")
    assert x["health"] == "broken" and x["target"]["path"] == "Tables/a" and len(platform.call("list_shortcut_risks")) == 1
    # A non-member's same-health report doesn't freshen the check either; a broken one always lands.
    when = x["checked_at"]
    assert [c["kind"] for c in eve.call("record_shortcut_health", results=[{"id": x["id"], "health": "healthy"}])] == ["skipped"]
    eve.call("record_shortcut_health", results=[{"id": x["id"], "health": "broken", "detail": "still 404"}])
    assert platform.call("list_seen_shortcuts")[0]["health_detail"] == "still 404"
    bo.call("log_shortcut_change", kind="repointed", shortcut=_sc("lh1", "x", b), before={"target": a}, after={"target": b})
    [x] = platform.call("list_seen_shortcuts")
    assert x["health"] == "not_checked" and platform.call("list_shortcut_risks") == [] and x["checked_at"] != when
    bo.call("log_shortcut_change", kind="removed", shortcut=_sc("lh1", "x", b), before={"target": b})
    assert platform.call("list_seen_shortcuts") == []
