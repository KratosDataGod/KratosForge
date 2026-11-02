"""Trust badges on a data product (Preview): endorsement from Fabric's admin tenant scan, kept in the
catalog for everyone, and business terms linked to Microsoft Purview. No tenant calls: HTTP is faked."""

import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.central import core, udf
from fabric_desktop.central.client import FabricPlatform, LocalPlatform
from fabric_desktop.central.trust_service import endorsements_from_scan
from fabric_desktop.fabric_api import POWERBI_ADMIN, FabricClient, FabricError
from fabric_desktop.service import Service

WS = "aaaaaaaa-0000-0000-0000-000000000001"
LH = "bbbbbbbb-0000-0000-0000-000000000002"
LH_TEST = "cccccccc-0000-0000-0000-000000000003"
PID = "crm/customer_360"
TERM_URL = "https://purview.microsoft.com/datacatalog/governance/main/catalog/glossary/term/123?tid=abc"


class Who:
    user = "chris@example.com"


@pytest.fixture
def who():
    w = Who()
    w.user = "chris@example.com"
    return w


@pytest.fixture
def platform(tmp_path, who):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: who.user)  # chris is its first admin
    port = {"port": "customers", "table": "dim_customer", "workspace_name": "CRM Prod", "lakehouse_name": "LH_Gold"}
    product = {"project": "crm", "name": "customer_360", "owner": "ana@example.com", "access": "public",
               "status": "published", "description": "One row per customer"}
    p.call("register_product", product=product, ports=[port | {"workspace_id": WS, "lakehouse_id": LH_TEST}], env="test")
    p.call("register_product", product=product, ports=[port | {"workspace_id": WS, "lakehouse_id": LH.upper()}], env="prod")
    return p


class FakeScanner:
    def __init__(self, workspaces=None, status=None):
        self.workspaces, self.status, self.asked = workspaces or [], status, []

    def admin_scan_workspaces(self, ids):
        self.asked.append(list(ids))
        if self.status:
            raise FabricError(f"POST {POWERBI_ADMIN}/workspaces/getInfo failed with {self.status}: Unauthorized",
                              status=self.status)
        return self.workspaces


@pytest.fixture
def svc(tmp_path, platform):
    s = Service(root=tmp_path / "app")
    s._catalog = lambda: platform
    return s


def scan_of(details):
    return [{"id": WS, "name": "CRM Prod", "datasets": [{"id": "ds-1", "name": "Sales"}],
             "Lakehouse": [{"id": LH, "name": "LH_Gold", **({"endorsementDetails": details} if details else {})}]}]


# --------------------------------------------------------------------------- reading the scan


def test_scan_result_is_parsed_for_the_wanted_items_only():
    workspaces = [{"id": WS, "datasets": [
        {"id": "DS-1", "endorsementDetails": {"endorsement": "Certified", "certifiedBy": "Ana Lopez"}},
        {"id": "ds-2"},  # a Power BI item without endorsementDetails isn't endorsed
        {"id": "ds-3", "endorsementDetails": {"endorsement": "Promoted"}}],
        "dataflows": [{"objectId": "df-1", "endorsementDetails": {"endorsement": "MasterData"}}],
        "Lakehouse": [{"id": "lh-1"}, {"id": "lh-2", "endorsementDetails": {"endorsement": "Certified", "certifiedBy": "bo@contoso.com"}}],
        "name": "CRM"}]
    got = endorsements_from_scan(workspaces, {"ds-1", "ds-2", "df-1", "lh-1", "lh-2", "gone"})
    assert got == {"ds-1": {"endorsement": "Certified", "certified_by": "Ana Lopez"},
                   "ds-2": {"endorsement": "None", "certified_by": ""},
                   "df-1": {"endorsement": "Master data", "certified_by": ""},
                   "lh-2": {"endorsement": "Certified", "certified_by": "bo@contoso.com"}}
    # a Fabric item without endorsementDetails, and one the scan didn't report, stay unknown: never guessed
    assert "lh-1" not in got and "gone" not in got and "ds-3" not in got
    assert core.endorsement_label("Something new") == "Something new"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers, self.text = status, body, {}, json.dumps(body)

    def json(self):
        return self._body


class ScanSession:
    def __init__(self, polls=1):
        self.calls, self.polls = [], polls

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs.get("params"), kwargs.get("json"), headers["Authorization"]))
        if url.endswith("/workspaces/getInfo"):
            return Resp(202, {"id": f"scan{len(self.calls)}", "status": "NotStarted"})
        if "/scanStatus/" in url:
            self.polls -= 1
            return Resp(200, {"status": "Running" if self.polls >= 0 else "Succeeded"})
        if "/scanResult/" in url:
            return Resp(200, {"workspaces": [{"id": url.rsplit("/", 1)[1]}]})
        return Resp(404, {})


def test_scanner_asks_100_workspaces_a_call_reads_nothing_more_and_waits_for_the_scan():
    session, slept = ScanSession(), []
    client = FabricClient(lambda: "tok", session=session, sleep=slept.append)
    ids = [f"ws{i}" for i in range(150)]
    out = client.admin_scan_workspaces(ids)
    posts = [c for c in session.calls if c[0] == "POST"]
    assert [len(p[3]["workspaces"]) for p in posts] == [100, 50]
    assert all(p[1] == f"{POWERBI_ADMIN}/workspaces/getInfo" for p in posts)
    assert all(v == "false" for p in posts for v in p[2].values()) and "datasetExpressions" in posts[0][2]
    assert {c[0] for c in session.calls} == {"POST", "GET"}  # read-only: only getInfo is a POST
    assert len(out) == 2 and slept  # it waited for a running scan
    assert all(c[4] == "Bearer tok" for c in session.calls)


def test_scanner_says_when_a_scan_fails():
    class Failing(ScanSession):
        def request(self, method, url, headers=None, timeout=None, **kwargs):
            if "/scanStatus/" in url:
                return Resp(200, {"status": "Failed", "error": "quota"})
            return super().request(method, url, headers, timeout, **kwargs)
    with pytest.raises(FabricError, match="tenant scan failed: quota"):
        FabricClient(lambda: "tok", session=Failing(), sleep=lambda s: None).admin_scan_workspaces(["ws"])


# --------------------------------------------------------------------------- scanning and the stored badge


def test_unknown_when_nothing_has_been_scanned(svc):
    trust = svc.platform_product_trust(PID)
    assert [(i["item_id"], i["env"], i["endorsement"]) for i in trust["items"]] == [(LH, "prod", None)]
    assert trust["terms"] == [] and trust["admin"] is True


def test_scan_stores_the_badge_and_a_non_admin_sees_it(svc, who, platform):
    scanner = FakeScanner(scan_of({"endorsement": "Certified", "certifiedBy": "Ana Lopez"}))
    svc._scanner = lambda: scanner
    out = svc.platform_scan_endorsement(PID)
    assert scanner.asked == [[WS]]  # only the workspaces the products live in
    assert out["recorded"] == 1 and out["not_reported"] == []
    item = out["trust"]["items"][0]
    assert (item["endorsement"], item["certified_by"], item["scanned_by"]) == ("Certified", "Ana Lopez", "chris@example.com")
    who.user = "bo@example.com"  # not a platform admin, not the owner
    seen = svc.platform_product_trust(PID)
    assert seen["items"][0]["endorsement"] == "Certified" and seen["items"][0]["scanned_at"]
    assert seen["admin"] is False and seen["can_edit_terms"] is False
    entry = platform.call("audit", limit=5)[0]
    assert entry["action"] == "record endorsement scan"
    assert "Ana" not in (entry["detail"] or "")  # who certified isn't copied into the audit


def test_an_item_the_scan_did_not_report_stays_unknown(svc):
    svc._scanner = lambda: FakeScanner(scan_of(None))  # a lakehouse without endorsementDetails
    out = svc.platform_scan_endorsement(PID)
    assert out["recorded"] == 0 and out["not_reported"] == ["LH_Gold"]
    assert out["trust"]["items"][0]["endorsement"] is None


def test_a_later_scan_without_details_takes_an_old_certified_badge_away(svc, who, platform):
    svc._scanner = lambda: FakeScanner(scan_of({"endorsement": "Certified", "certifiedBy": "Ana Lopez"}))
    assert svc.platform_scan_endorsement(PID)["trust"]["items"][0]["endorsement"] == "Certified"
    svc._scanner = lambda: FakeScanner(scan_of(None))  # listed again, now with no endorsementDetails
    out = svc.platform_scan_endorsement(PID)
    assert out["not_reported"] == ["LH_Gold"]
    assert out["trust"]["items"][0]["endorsement"] is None
    who.user = "bo@example.com"
    assert svc.platform_product_trust(PID)["items"][0]["endorsement"] is None
    assert '"cleared": 1' in (platform.call("audit", limit=5)[0]["detail"] or "")


def test_non_admin_is_refused_a_scan_before_the_tenant_is_read(svc, who, platform):
    scanner = FakeScanner(scan_of({"endorsement": "Promoted"}))
    svc._scanner = lambda: scanner
    who.user = "bo@example.com"
    with pytest.raises(PermissionError, match="Only platform admins scan endorsement"):
        svc.platform_scan_endorsement(PID)
    assert scanner.asked == []
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("record_endorsements", items=[{"item_id": LH, "endorsement": "Certified"}])


def test_a_platform_admin_who_is_not_a_fabric_admin_is_told_so(svc):
    svc._scanner = lambda: FakeScanner(status=403)
    with pytest.raises(PermissionError, match="Only a Fabric administrator can read endorsement"):
        svc.platform_scan_endorsement(PID)
    assert svc.platform_product_trust(PID)["items"][0]["endorsement"] is None


# --------------------------------------------------------------------------- business terms


@pytest.mark.parametrize("url", [
    "http://purview.microsoft.com/term/1", "https://purview.microsoft.com.evil.example/term",
    "https://evil.example/?u=purview.microsoft.com", "https://user@purview.microsoft.com/term",
    "https://purview.microsoft.com:8443/term", "javascript:alert(1)", "", "https://purview.microsoft.com/a b",
    "https://evil.example\\.purview.microsoft.com/"])
def test_term_links_must_be_purview_https(svc, url):
    with pytest.raises(ValueError, match="Purview"):
        svc.platform_add_product_term(PID, "Customer", url)
    with pytest.raises(core.PlatformError, match="Purview"):
        core.purview_url(url)


def test_purview_hosts_are_accepted():
    for url in (TERM_URL, "https://web.purview.azure.com/resource/x/main/glossary/term?termGuid=1",
                "https://contoso.purview.microsoft.com/term/2"):
        assert core.purview_url(url) == url


def test_terms_are_added_and_removed_by_the_owner_or_an_admin_and_audited(svc, who, platform):
    trust = svc.platform_add_product_term(PID, "  Active   customer ", TERM_URL)
    assert trust["terms"] == [{"term": "Active customer", "url": TERM_URL, "added_by": "chris@example.com",
                               "added_at": trust["terms"][0]["added_at"]}]
    who.user = "bo@example.com"
    with pytest.raises(core.PlatformError, match="product's owner or a platform admin"):
        svc.platform_add_product_term(PID, "Churn", TERM_URL)
    with pytest.raises(core.PlatformError, match="product's owner or a platform admin"):
        svc.platform_remove_product_term(PID, "Active customer")
    who.user = "ana@example.com"  # the product's owner
    assert svc.platform_product_trust(PID)["can_edit_terms"] is True
    svc.platform_add_product_term(PID, "Churn", TERM_URL)
    assert [t["term"] for t in svc.platform_remove_product_term(PID, "active customer")["terms"]] == ["Churn"]
    with pytest.raises(core.PlatformError, match="isn't a business term"):
        svc.platform_remove_product_term(PID, "Active customer")
    actions = [(a["action"], a["by_user"]) for a in platform.call("audit", limit=10)]
    assert actions[:3] == [("remove product term", "ana@example.com"), ("add product term", "ana@example.com"),
                           ("add product term", "chris@example.com")]


def test_terms_need_a_published_product(svc):
    with pytest.raises(core.PlatformError, match="isn't a published data product"):
        svc.platform_add_product_term("crm/nope", "Churn", TERM_URL)


# --------------------------------------------------------------------------- an older platform


class NotFoundSession:
    def post(self, url, json=None, timeout=None, headers=None):
        return Resp(404, {})


def test_an_older_udf_platform_says_to_finish_or_repair(svc):
    svc._catalog = lambda: FabricPlatform(lambda: "tok", "ws", "fn", session=NotFoundSession())
    for call in (lambda: svc.platform_product_trust(PID),
                 lambda: svc.platform_add_product_term(PID, "Churn", TERM_URL),
                 lambda: svc.platform_remove_product_term(PID, "Churn")):
        with pytest.raises(LookupError, match="After an update, a platform admin runs Platform > Settings > Finish or repair once"):
            call()


def test_a_platform_made_before_gets_the_tables_on_first_use(platform):
    with platform._db() as db:
        for t in ("endorsement", "product_term"):
            db.run(f"DROP TABLE {db.t('catalog', t)}")
        db.commit()
        assert not db.exists("catalog", "endorsement")
    assert platform.call("product_trust", product_id=PID)["items"][0]["endorsement"] is None
    with platform._db() as db:
        assert db.exists("catalog", "endorsement") and db.exists("catalog", "product_term")


def test_rpc_names_udf_functions_and_audit():
    for m in ("platform_product_trust", "platform_scan_endorsement", "platform_add_product_term", "platform_remove_product_term"):
        assert m in server.METHODS
    assert not audit.audited("platform_product_trust")
    for m in ("platform_scan_endorsement", "platform_add_product_term", "platform_remove_product_term"):
        assert audit.audited(m)
    source = udf.function_app()
    for op in ("product_trust", "record_endorsements", "add_product_term", "remove_product_term"):
        assert f"def {op}(" in source and op in core.OPERATIONS
    compile(source, "function_app.py", "exec")


def test_a_failed_scan_reason_is_one_short_line_without_a_token():
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"

    class Failing(ScanSession):
        def request(self, method, url, headers=None, timeout=None, **kwargs):
            if "/scanStatus/" in url:
                return Resp(200, {"status": "Failed", "error": {"code": "x", "message": f"no\nBearer {token}" + "z" * 900}})
            return super().request(method, url, headers, timeout, **kwargs)
    with pytest.raises(FabricError) as e:
        FabricClient(lambda: "tok", session=Failing(), sleep=lambda s: None).admin_scan_workspaces(["ws"])
    text = str(e.value)
    assert "\n" not in text and token not in text and "[removed]" in text and len(text) < 400
