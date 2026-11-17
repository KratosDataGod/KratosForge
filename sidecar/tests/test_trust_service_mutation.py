"""Trust badges and product groups: what mutation testing found the badge tests didn't pin
(docs/quality/mutation-testing.md, batch 78). A fake catalog stands in for the platform and a fake
scanner for Fabric's admin tenant scan; nothing reaches a tenant."""

import pytest

from fabric_desktop.central import trust_service as ts

WS = "aaaaaaaa-0000-0000-0000-000000000001"
WS2 = "aaaaaaaa-0000-0000-0000-000000000002"
WS3 = "aaaaaaaa-0000-0000-0000-000000000003"
LH = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


class Catalog:
    def __init__(self, **answers):
        self.answers = {"whoami": {"admin": True}, "list_products": [],
                        "record_endorsements": self._record} | answers
        self.calls = []

    def _record(self, items, cleared):
        return {"recorded": len(items)}

    def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        answer = self.answers[op]
        if isinstance(answer, Exception):
            raise answer
        return answer(**kwargs) if callable(answer) else answer


class Svc(ts.TrustMixin):
    def __init__(self, catalog, scanner):
        self.catalog, self.scanner = catalog, scanner

    def _catalog(self):
        return self.catalog

    def _scanner(self):
        return self.scanner


class Scanner:
    def __init__(self, result=None):
        self.result, self.asked = result or [], []

    def admin_scan_workspaces(self, ids):
        self.asked.append(list(ids))
        return self.result


def product(pid, status="published", workspace=WS):
    return {"id": pid, "status": status,
            "ports": [{"lakehouse_id": LH, "workspace_id": workspace, "lakehouse_name": "LH_Gold", "env": "prod"}]}


def group(pid, item_id, workspace, name="Sales model"):
    return {"product_id": pid, "item_id": item_id, "workspace_id": workspace, "item_type": "SemanticModel",
            "item_name": name}


def test_a_published_product_s_group_is_scanned_with_its_own_workspace_and_name():
    scanner = Scanner()
    cat = Catalog(list_products=[product("p1")], list_product_items=[group("p1", "m1", WS2)])
    out = Svc(cat, scanner).platform_scan_endorsement()
    assert scanner.asked == [[WS, WS2]]
    assert out["items"] == 2 and out["not_reported"] == ["LH_Gold", "Sales model"]
    (_, rec), = [c for c in cat.calls if c[0] == "record_endorsements"]
    assert rec["cleared"] == sorted([LH, "m1"])


def test_a_draft_product_s_group_is_not_scanned():
    scanner = Scanner()
    cat = Catalog(list_products=[product("p1"), product("p2", status="draft", workspace=WS3)],
                  list_product_items=[group("p2", "m2", WS3)])
    out = Svc(cat, scanner).platform_scan_endorsement()
    assert scanner.asked == [[WS]]
    assert out["items"] == 1 and out["not_reported"] == ["LH_Gold"]


def test_a_group_entry_for_an_item_already_behind_a_port_keeps_the_port_s_reading():
    scanner = Scanner()
    cat = Catalog(list_products=[product("p1")], list_product_items=[group("p1", LH, WS3, name="Other")])
    out = Svc(cat, scanner).platform_scan_endorsement()
    assert scanner.asked == [[WS]]
    assert out["items"] == 1 and out["not_reported"] == ["LH_Gold"]


def test_a_group_without_a_name_is_reported_by_its_id_and_blank_fields_stay_blank():
    scan = [{"datasets": [{"id": "m1"}]}]
    cat = Catalog(list_products=[product("p1")],
                  list_product_items=[{"product_id": "p1", "item_id": "m1", "workspace_id": WS2, "item_name": None},
                                      {"product_id": "p1", "item_id": "m9", "workspace_id": WS2}])
    out = Svc(cat, Scanner(result=scan)).platform_scan_endorsement()
    assert out["not_reported"] == ["LH_Gold", "m9"]
    (_, rec), = [c for c in cat.calls if c[0] == "record_endorsements"]
    assert rec["items"] == [{"item_id": "m1", "workspace_id": WS2, "item_type": "", "item_name": "",
                             "endorsement": "None", "certified_by": ""}]
    assert out["recorded"] == 1
