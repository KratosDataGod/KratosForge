"""Trust badges and product groups: the edges the badge tests don't reach. A fake catalog stands in
for the platform and a fake scanner for Fabric's admin tenant scan; nothing reaches a tenant."""

import pytest

from fabric_desktop.auth import POWERBI_TENANT_READ_SCOPES
from fabric_desktop.central import core
from fabric_desktop.central import trust_service as ts
from fabric_desktop.fabric_api import FabricClient, FabricError

WS = "aaaaaaaa-0000-0000-0000-000000000001"
LH = "bbbbbbbb-0000-0000-0000-000000000002"


class Catalog:
    """Answers each platform operation from a table; a value that is an exception is raised."""

    def __init__(self, **answers):
        self.answers = {"whoami": {"admin": True}, "list_products": []} | answers
        self.calls = []

    def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        answer = self.answers[op]
        if isinstance(answer, Exception):
            raise answer
        return answer(**kwargs) if callable(answer) else answer


class Svc(ts.TrustMixin):
    def __init__(self, catalog, scanner=None):
        self.catalog, self.scanner = catalog, scanner

    def _catalog(self):
        return self.catalog

    def _scanner(self):
        return self.scanner


def published(lakehouse=LH, workspace=WS, status="published"):
    return {"id": "crm/c360", "status": status,
            "ports": [{"lakehouse_id": lakehouse, "workspace_id": workspace, "lakehouse_name": "LH_Gold", "env": "prod"}]}


class Scanner:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.asked = result or [], error, []

    def admin_scan_workspaces(self, ids):
        self.asked.append(list(ids))
        if self.error:
            raise self.error
        return self.result


def test_scan_skips_workspaces_and_items_that_are_not_the_expected_shape():
    workspaces = [None, {"name": "CRM", "datasets": ["not a dict", {"id": "ds-1"}], "users": {"not": "a list"}}]
    assert ts.endorsements_from_scan(workspaces, {"ds-1"}) == {"ds-1": {"endorsement": "None", "certified_by": ""}}
    assert ts.endorsements_from_scan(None, {"ds-1"}) == {}


def test_a_blank_endorsement_on_a_fabric_item_says_nothing():
    workspaces = [{"Lakehouse": [{"id": LH, "endorsementDetails": {"endorsement": "  "}}]}]
    assert ts.endorsements_from_scan(workspaces, {LH}) == {}


def test_the_scanner_reads_with_the_tenant_read_scope_only():
    asked = []

    class Auth:
        def _token(self, scopes):
            asked.append(scopes)
            return "tok"

    svc = ts.TrustMixin()
    svc.auth = Auth()
    client = svc._scanner()
    assert isinstance(client, FabricClient)
    assert client._token() == "tok"
    assert asked[-1] == POWERBI_TENANT_READ_SCOPES


def test_a_non_admin_is_refused_before_anything_is_read():
    cat = Catalog(whoami={"admin": False})
    with pytest.raises(PermissionError, match="Only platform admins"):
        Svc(cat).platform_scan_endorsement()
    assert [op for op, _ in cat.calls] == ["whoami"]


def test_nothing_to_scan_when_no_published_product_has_a_fabric_port():
    scanner = Scanner()
    cat = Catalog(list_products=[published(status="draft"), published(workspace="")],
                  list_product_items=LookupError("older platform"))
    out = Svc(cat, scanner).platform_scan_endorsement()
    assert out["workspaces"] == 0 and out["items"] == 0 and out["recorded"] == 0
    assert "nothing to scan" in out["message"]
    assert scanner.asked == []  # the tenant isn't read at all


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_tenant_scan_says_only_a_fabric_admin_can(status, monkeypatch):
    logged = []
    monkeypatch.setattr(ts._log, "info", lambda *a, **k: logged.append(a))
    cat = Catalog(list_products=[published()], list_product_items=[])
    err = FabricError("POST getInfo failed with 403: token=secret-value", status=status)
    with pytest.raises(PermissionError) as raised:
        Svc(cat, Scanner(error=err)).platform_scan_endorsement()
    assert str(raised.value) == ts.NOT_ADMIN
    assert raised.value.__cause__ is err
    assert logged and "Tenant scan refused" in logged[0][0]


def test_any_other_scan_failure_is_raised_as_it_is():
    cat = Catalog(list_products=[published()], list_product_items=[])
    err = FabricError("POST getInfo failed with 500: busy", status=500)
    with pytest.raises(FabricError) as raised:
        Svc(cat, Scanner(error=err)).platform_scan_endorsement()
    assert raised.value is err
    assert "record_endorsements" not in [op for op, _ in cat.calls]


def test_a_scan_asked_for_one_product_returns_its_trust_too():
    scan = [{"Lakehouse": [{"id": LH, "endorsementDetails": {"endorsement": "Certified", "certifiedBy": "Ana"}}]}]
    cat = Catalog(list_products=[published()], list_product_items=[],
                  record_endorsements=lambda items, cleared: {"recorded": len(items)},
                  product_trust=lambda product_id: {"product_id": product_id, "badge": "Certified"})
    out = Svc(cat, Scanner(result=scan)).platform_scan_endorsement("crm/c360")
    assert out["recorded"] == 1 and out["not_reported"] == []
    assert out["trust"] == {"product_id": "crm/c360", "badge": "Certified"}


def test_product_groups_from_an_older_platform_read_as_none():
    older = core.PlatformError("No operation list_product_items on this platform")
    assert Svc(Catalog(list_product_items=older))._product_groups(Catalog(list_product_items=older)) is None
    assert Svc(None)._product_groups(Catalog(list_product_items=LookupError("404"))) is None


def test_any_other_platform_refusal_while_reading_groups_is_raised():
    refusal = core.PlatformError("The platform is busy")
    with pytest.raises(core.PlatformError, match="busy"):
        Svc(None)._product_groups(Catalog(list_product_items=refusal))


def test_product_items_add_and_remove_go_through_the_catalog():
    cat = Catalog(list_product_items=lambda product_id: [{"item_id": "m1"}],
                  add_product_item=lambda product_id, item: [item],
                  remove_product_item=lambda product_id, item_id: [])
    svc = Svc(cat)
    assert svc.platform_product_items("p") == {"product_id": "p", "items": [{"item_id": "m1"}]}
    added = svc.platform_add_product_item("p", "Report", "r1", WS, "Sales")
    assert added["items"][0] == {"item_type": "Report", "item_id": "r1", "workspace_id": WS, "item_name": "Sales",
                                 "workspace_name": ""}
    assert svc.platform_remove_product_item("p", "r1") == {"product_id": "p", "items": []}
    with pytest.raises(ValueError, match="semantic model or a report"):
        svc.platform_add_product_item("p", "Lakehouse", "x", WS, "LH")
    assert [op for op, _ in cat.calls] == ["list_product_items", "add_product_item", "remove_product_item"]
