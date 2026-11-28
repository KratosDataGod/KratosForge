"""The Direct Lake check's service (model_directlake_service.py): the edges the main tests don't reach. Where a
table is in OneLake, finding a lakehouse by its SQL endpoint when the list leaves the properties out, a
capacity or a store that can't be read, and a table or a key that fails part way. Tables are local Delta
folders and Fabric is a stand-in; nothing reaches a tenant."""

from __future__ import annotations

from types import SimpleNamespace


from fabric_desktop import model_directlake_service as mds
from fabric_desktop import onelake
from tests.test_model_directlake import svc  # noqa: F401 - the service with a stand-in Fabric and local tables


class Bare(mds.ModelDirectLakeMixin):
    def __init__(self, fabric=None, store=None, health=None):
        self.fabric, self.store, self.health = fabric, store, health
        self.auth = SimpleNamespace(storage_token=lambda: "storage-tok")

    def _store(self, project, env):
        if isinstance(self.store, Exception):
            raise self.store
        return self.store

    def capacity_health(self):
        if isinstance(self.health, Exception):
            raise self.health
        return self.health


def test_a_direct_lake_table_is_read_from_its_lakehouse_with_the_storage_token(monkeypatch):
    monkeypatch.setattr(onelake, "storage_options", lambda token: {"bearer_token": token})
    uri, options = Bare()._directlake_uri("ws1", "lh1", "gold", "fact_sales")
    assert uri == f"abfss://ws1@{onelake.DFS_HOST}/lh1/Tables/gold/fact_sales"
    assert options == {"bearer_token": "storage-tok"}
    flat, _ = Bare()._directlake_uri("ws1", "lh1", "", "sales")  # a lakehouse without schemas
    assert flat == f"abfss://ws1@{onelake.DFS_HOST}/lh1/Tables/sales"


def test_a_onelake_expression_names_its_lakehouse_without_asking_fabric():
    svc = Bare(fabric=None)  # any call to Fabric would fail
    found = {"kind": "onelake", "workspace": "ws9", "item": "lh9"}
    assert svc._directlake_lakehouse(None, "test", {"workspace_id": "ws-test"}, found, {}) == ("ws9", "lh9")


class Lakehouses:
    """Lists lakehouses without their properties, as the list call can; get_lakehouse has them."""

    def __init__(self):
        self.got = []

    def list_lakehouses(self, ws):
        return [{"id": f"{ws}-a"}, {"id": f"{ws}-b"}]

    def get_lakehouse(self, ws, lh):
        self.got.append(lh)
        return {"properties": {"sqlEndpointProperties": {"id": "SQL-B" if lh == "ws-test-b" else "other"}}}


def test_a_lakehouse_listed_without_properties_is_read_for_its_sql_endpoint(monkeypatch):
    logged = []
    monkeypatch.setattr(mds._log, "info", lambda *a: logged.append(a))
    fabric = Lakehouses()
    svc = Bare(fabric=fabric, store=OSError("no store yet"))
    cache: dict = {}
    found = {"kind": "sql", "endpoint": "sql-b"}
    assert svc._directlake_lakehouse(None, "test", {"workspace_id": "ws-test"}, found, cache) == ("ws-test", "ws-test-b")
    assert fabric.got == ["ws-test-a", "ws-test-b"]
    assert cache == {"sql-b": ("ws-test", "ws-test-b")}
    assert logged[0][0] == "No lakehouse workspaces for the Direct Lake check: %s" and "no store yet" in logged[0][1]
    # asked again, the answer comes from the cache
    assert svc._directlake_lakehouse(None, "test", {"workspace_id": "ws-test"}, found, cache) == ("ws-test", "ws-test-b")
    assert fabric.got == ["ws-test-a", "ws-test-b"]


def test_a_capacity_that_cant_be_read_leaves_the_sku_for_the_person_to_pick(monkeypatch):
    logged = []
    monkeypatch.setattr(mds._log, "info", lambda *a: logged.append(a))
    out = Bare(health=RuntimeError("capacity API refused")).meta_model_directlake_setup("prod")
    assert out["env"] == "prod" and out["sku"] == "" and out["capacity_sku"] == ""
    assert out["skus"]
    assert logged and "capacity API refused" in logged[0][1]


def test_an_environment_with_no_capacity_entry_has_no_sku():
    out = Bare(health={"environments": [{"env": "test", "capacity": {"sku": "F8"}}]}).meta_model_directlake_setup("prod")
    assert out["sku"] == "" and out["capacity_sku"] == ""


def test_one_table_that_cant_be_read_is_listed_and_the_others_still_checked(svc, monkeypatch):  # noqa: F811
    real = svc._directlake_uri

    def uri(ws, lh, schema, table):
        if table == "dim_customer":
            raise PermissionError("403 on dim_customer")
        return real(ws, lh, schema, table)

    monkeypatch.setattr(svc, "_directlake_uri", uri)
    out = svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    assert out["unread"] == [{"table": "DimCustomer", "reason": "PermissionError: 403 on dim_customer"}]
    assert [t["table"] for t in out["tables"]] == ["FactSales"]
    # DimCustomer's key can't be read, so neither its uniqueness nor the relationship to it is checked
    assert not [f for f in out["findings"] if f["check"] in ("Key not unique", "Rows with no match")]


def test_a_key_that_fails_to_read_or_match_is_listed_and_the_check_still_stands(svc, monkeypatch):  # noqa: F811
    def broken(*args):
        raise OSError("connection reset")

    monkeypatch.setattr(mds, "duplicate_values", broken)
    monkeypatch.setattr(mds, "unmatched_rows", broken)
    out = svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    assert {"table": "DimCustomer", "reason": "Key customer_sk not read (OSError: connection reset)"} in out["unread"]
    assert {"table": "FactSales", "reason": "Key customer_sk not matched (OSError: connection reset)"} in out["unread"]
    assert out["keys_checked"] is True
    assert ("Column missing", "discount_pct") in {(f["check"], f["column"]) for f in out["findings"]}


def test_a_key_column_the_delta_table_doesnt_have_is_not_read(svc, monkeypatch):  # noqa: F811
    read = []
    monkeypatch.setattr(mds, "duplicate_values", lambda *a: read.append(a) or 0)
    model = svc.fake.bim["model"]
    dim = next(t for t in model["tables"] if t["name"] == "DimCustomer")
    dim["columns"][0]["sourceColumn"] = "customer_key"  # not in dim_customer
    out = svc.meta_model_directlake_check("test", "SM_Gold", keys=True)
    assert read == []
    assert not [f for f in out["findings"] if f["check"] == "Rows with no match"]

