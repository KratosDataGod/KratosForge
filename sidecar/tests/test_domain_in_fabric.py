"""Governance plan step 5: a new domain is created in Fabric first, then kept in the platform linked to it."""

import pytest
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from test_platform import svc  # noqa: F401 - the fixture


class _Admin:
    def __init__(self, status=None):
        self.status, self.created = status, []

    def create_admin_domain(self, name, description="", parent_id=""):
        if self.status:
            raise FabricError(f"POST /admin/domains failed with {self.status}", status=self.status)
        self.created.append((name, description, parent_id))
        return {"id": f"f-{name.lower()}", "displayName": name, "parentDomainId": parent_id or None}


@pytest.fixture
def admin(monkeypatch):
    fake = _Admin()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fake if write else None)
    return fake


def test_a_new_domain_and_its_subdomain_are_created_in_fabric_and_linked(svc, admin):  # noqa: F811
    got = {d["id"]: d for d in svc.platform_create_domain({"name": "Finance", "description": "Money"})}
    assert got["finance"]["fabric_domain"] == "f-finance"
    got = {d["id"]: d for d in svc.platform_create_domain({"name": "Payroll", "parent_id": "finance"})}
    assert got["payroll"]["parent_id"] == "finance" and got["payroll"]["fabric_domain"] == "f-payroll"
    assert admin.created == [("Finance", "Money", ""), ("Payroll", "", "f-finance")]


def test_nothing_reaches_fabric_when_the_platform_would_refuse_it(svc, admin):  # noqa: F811
    svc.platform_save_domain({"name": "HR"})
    with pytest.raises(ValueError, match="HR isn't in Fabric"):
        svc.platform_create_domain({"name": "Payroll", "parent_id": "hr"})
    with pytest.raises(ValueError, match="already has a domain called HR"):
        svc.platform_create_domain({"name": "hr"})
    with pytest.raises(ValueError, match="needs a name"):
        svc.platform_create_domain({"name": " "})
    assert admin.created == []
    # Saved in the platform only, as before.
    assert {d["id"] for d in svc.platform_create_domain({"name": "Legal"}, in_fabric=False)} == {"hr", "legal"}
    assert admin.created == []


@pytest.mark.parametrize(("status", "error", "match"), [(403, PermissionError, "Only a Fabric administrator"),
                                                        (409, ValueError, "Fabric already has a domain called Finance")])
def test_fabric_refusing_changes_nothing_and_says_why(svc, monkeypatch, status, error, match):  # noqa: F811
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: _Admin(status))
    with pytest.raises(error, match=match):
        svc.platform_create_domain({"name": "Finance"})
    assert svc.platform_domains() == []
