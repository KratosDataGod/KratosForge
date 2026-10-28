"""Product groups (Preview): a data product's semantic models and reports, picked from Fabric by its owner
and kept in the catalog by item id only (catalog.product_item), each with its last scanned endorsement.
Nothing is written to Fabric. No tenant calls: HTTP is faked."""

import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.central import core, udf
from fabric_desktop.central.client import FabricPlatform, LocalPlatform
from fabric_desktop.service import Service

WS = "aaaaaaaa-0000-0000-0000-000000000001"
LH = "bbbbbbbb-0000-0000-0000-000000000002"
BI_WS = "eeeeeeee-0000-0000-0000-000000000005"
SM = "dddddddd-0000-0000-0000-000000000004"
RPT = "ffffffff-0000-0000-0000-000000000006"
PID = "crm/customer_360"


class Who:
    user = "chris@example.com"


@pytest.fixture
def who():
    return Who()


@pytest.fixture
def platform(tmp_path, who):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: who.user)  # chris is its first admin
    port = {"port": "customers", "table": "dim_customer", "workspace_name": "CRM Prod", "lakehouse_name": "LH_Gold",
            "workspace_id": WS, "lakehouse_id": LH}
    product = {"project": "crm", "name": "customer_360", "owner": "ana@example.com", "access": "public",
               "status": "published", "description": "One row per customer"}
    p.call("register_product", product=product, ports=[port], env="prod")
    return p


@pytest.fixture
def svc(tmp_path, platform):
    s = Service(root=tmp_path / "app")
    s._catalog = lambda: platform
    return s


def model(**kw):
    return {"item_type": "SemanticModel", "item_id": SM.upper(), "workspace_id": BI_WS, "item_name": "SM_Customer360",
            "workspace_name": "Sales Prod"} | kw


def test_the_owner_adds_a_model_and_a_report_by_id_and_each_is_audited(svc, who, platform):
    who.user = "ana@example.com"  # the product's owner
    out = svc.platform_add_product_item(PID, **model())
    assert out["items"] == [{"product_id": PID, "item_id": SM, "item_type": "SemanticModel", "workspace_id": BI_WS,
                             "workspace_name": "Sales Prod", "item_name": "SM_Customer360", "added_by": "ana@example.com",
                             "added_at": out["items"][0]["added_at"], "endorsement": None, "certified_by": "",
                             "scanned_at": ""}]
    out = svc.platform_add_product_item(PID, "Report", RPT, BI_WS, "Customer health", "Sales Prod")
    assert [(i["item_type"], i["item_name"]) for i in out["items"]] == [("SemanticModel", "SM_Customer360"),
                                                                         ("Report", "Customer health")]
    # the same item again only renames it
    out = svc.platform_add_product_item(PID, **model(item_name="SM Customer 360"))
    assert [i["item_name"] for i in out["items"]] == ["SM Customer 360", "Customer health"]
    entries = platform.call("audit", limit=5)
    assert [(e["action"], e["target"], e["by_user"]) for e in entries[:3]] == [
        ("add product item", PID, "ana@example.com")] * 3
    assert json.loads(entries[0]["detail"]) == {"item_type": "SemanticModel", "item_id": SM, "workspace_id": BI_WS,
                                                "item_name": "SM Customer 360"}
    assert svc.platform_product_items(PID)["items"] == out["items"]


def test_only_the_owner_or_a_platform_admin_changes_them(svc, who, platform):
    svc.platform_add_product_item(PID, **model())  # chris is a platform admin
    who.user = "bo@example.com"
    with pytest.raises(core.PlatformError, match="product's owner or a platform admin"):
        svc.platform_add_product_item(PID, "Report", RPT, BI_WS, "Churn watch")
    with pytest.raises(core.PlatformError, match="product's owner or a platform admin"):
        svc.platform_remove_product_item(PID, SM)
    # everyone reads them
    assert [i["item_id"] for i in svc.platform_product_items(PID)["items"]] == [SM]


def test_removing_takes_only_the_catalog_row_away_and_is_audited(svc, who, platform):
    svc.platform_add_product_item(PID, **model())
    assert svc.platform_remove_product_item(PID, SM.upper())["items"] == []
    entry = platform.call("audit", limit=1)[0]
    assert (entry["action"], entry["target"]) == ("remove product item", PID)
    assert json.loads(entry["detail"]) == {"item_type": "SemanticModel", "item_id": SM, "item_name": "SM_Customer360"}
    with pytest.raises(core.PlatformError, match="isn't a semantic model or report"):
        svc.platform_remove_product_item(PID, SM)


@pytest.mark.parametrize("bad, why", [
    ({"item_type": "Lakehouse"}, "Pick a semantic model or a report"),
    ({"item_id": "not-a-guid"}, "The item needs its Fabric id"),
    ({"workspace_id": ""}, "workspace needs its Fabric id"),
    ({"item_name": "  "}, "display name"),
])
def test_an_item_needs_a_type_ids_and_a_name(svc, bad, why):
    with pytest.raises(ValueError, match=why):
        svc.platform_add_product_item(PID, **model(**bad))
    assert svc.platform_product_items(PID)["items"] == []


def test_the_catalog_refuses_other_types_and_unknown_products(platform):
    with pytest.raises(core.PlatformError, match="SemanticModel or Report"):
        platform.call("add_product_item", product_id=PID, item=model(item_type="Dashboard"))
    with pytest.raises(core.PlatformError, match="isn't a published data product"):
        platform.call("add_product_item", product_id="crm/nope", item=model())


class FakeScanner:
    def __init__(self, workspaces):
        self.workspaces, self.asked = workspaces, []

    def admin_scan_workspaces(self, ids):
        self.asked.append(list(ids))
        return self.workspaces


def test_the_scan_reads_each_model_and_reports_own_badge(svc, who):
    svc.platform_add_product_item(PID, **model())
    svc.platform_add_product_item(PID, "Report", RPT, BI_WS, "Customer health", "Sales Prod")
    scanner = FakeScanner([{"id": WS, "Lakehouse": [{"id": LH}]},
                           {"id": BI_WS, "datasets": [{"id": SM, "endorsementDetails": {"endorsement": "Certified"}}],
                            "reports": [{"id": RPT}]}])
    svc._scanner = lambda: scanner
    out = svc.platform_scan_endorsement(PID)
    assert scanner.asked == [sorted([WS, BI_WS])]
    assert out["items"] == 3 and out["recorded"] == 2 and out["not_reported"] == ["LH_Gold"]
    who.user = "bo@example.com"
    seen = {i["item_name"]: i["endorsement"] for i in svc.platform_product_items(PID)["items"]}
    assert seen == {"SM_Customer360": "Certified", "Customer health": "None"}


def test_products_list_carries_each_products_models_and_reports(svc):
    svc.platform_add_product_item(PID, **model())
    out = svc.platform_products()
    assert out["can_group"] is True
    (p,) = out["products"]
    assert [(i["item_type"], i["item_id"]) for i in p["items"]] == [("SemanticModel", SM)]


def test_a_platform_made_before_gets_the_table_on_first_use(platform):
    with platform._db() as db:
        db.run(f"DROP TABLE {db.t('catalog', 'product_item')}")
        db.commit()
        assert not db.exists("catalog", "product_item")
    assert platform.call("list_product_items", product_id=PID) == []
    with platform._db() as db:
        assert db.exists("catalog", "product_item")
        assert core.ensure_product_items(db) == []


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers, self.text = status, body, {}, json.dumps(body)

    def json(self):
        return self._body


class NotFoundSession:
    def post(self, url, json=None, timeout=None, headers=None):
        return Resp(404, {})


def test_an_older_udf_platform_says_to_finish_or_repair(svc):
    svc._catalog = lambda: FabricPlatform(lambda: "tok", "ws", "fn", session=NotFoundSession())
    for call in (lambda: svc.platform_product_items(PID),
                 lambda: svc.platform_add_product_item(PID, **model()),
                 lambda: svc.platform_remove_product_item(PID, SM)):
        with pytest.raises(LookupError, match="After an update, a platform admin runs Platform > Settings > Finish or repair once"):
            call()
    assert svc._product_groups(svc._catalog()) is None  # the products list then shows no groups


def test_rpc_names_udf_functions_and_audit():
    for m in ("platform_product_items", "platform_add_product_item", "platform_remove_product_item"):
        assert m in server.METHODS
    assert not audit.audited("platform_product_items")
    assert audit.audited("platform_add_product_item") and audit.audited("platform_remove_product_item")
    source = udf.function_app()
    for op in ("list_product_items", "add_product_item", "remove_product_item"):
        assert f"def {op}(" in source and op in core.OPERATIONS
    compile(source, "function_app.py", "exec")
