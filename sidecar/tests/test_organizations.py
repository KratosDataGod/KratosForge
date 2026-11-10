"""A guest (B2B) account signing in to the company that invited it (organizations.py,
organizations_service.py, auth.Authenticator's tenant): the domain or tenant id is checked before
sign-in, sign-in goes to that tenant's own authority, a failed sign-in puts the choice back, IT can
fix the organization, and nothing but the typed domain or id goes to Microsoft."""

import json

import pytest
import requests

from fabric_desktop import app_settings, auth, dpapi, organizations
from fabric_desktop.organizations_service import OrganizationsMixin

HOST = "aaaaaaaa-1111-2222-3333-444444444444"
HOME = "bbbbbbbb-1111-2222-3333-444444444444"
OBJ = "cccccccc-1111-2222-3333-444444444444"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)
    organizations.reset_cache()
    yield
    organizations.reset_cache()


class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session:
    """Microsoft's public metadata for one tenant: northwind.com and HOST; anything else is unknown."""

    def __init__(self, cloud="microsoftonline.com", fail=None):
        self.urls, self.cloud, self.fail = [], cloud, fail

    def get(self, url, timeout=None, **kw):
        self.urls.append(url)
        if self.fail:
            raise self.fail
        name = url.split("/")[3]
        if name in ("northwind.com", HOST):
            return _Reply(200, {"issuer": f"https://login.microsoftonline.com/{HOST}/v2.0",
                                "cloud_instance_name": self.cloud})
        if name == "personal.example":
            return _Reply(200, {"issuer": f"https://login.microsoftonline.com/{organizations.CONSUMER_TENANT}/v2.0"})
        return _Reply(400, {"error": "invalid_tenant", "error_description": "AADSTS90002: Tenant not found."})


def _policy(tmp_path, data):
    path = app_settings.policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------- checking a domain or tenant id ----------

def test_a_domain_is_checked_with_microsofts_public_metadata_and_names_its_tenant():
    s = _Session()
    org = organizations.lookup("  Northwind.COM ", session=s)
    assert org == {"tenant_id": HOST, "domain": "northwind.com", "name": "northwind.com"}
    assert s.urls == ["https://login.microsoftonline.com/northwind.com/v2.0/.well-known/openid-configuration"]
    assert organizations.lookup("northwind.com", session=s) == org and len(s.urls) == 1  # once per run


def test_a_tenant_id_is_shown_by_its_id_when_no_domain_is_known():
    org = organizations.lookup(HOST.upper(), session=_Session())
    assert org == {"tenant_id": HOST, "domain": "", "name": HOST}


def test_an_email_or_pasted_address_is_read_as_its_domain():
    assert organizations.normalize("chris@Northwind.com") == "northwind.com"
    assert organizations.normalize("https://northwind.com/some/path") == "northwind.com"


@pytest.mark.parametrize("text", ["", "northwind", "north wind.com", "a/b.com", "evil.com/../../x", "x.com?y=1",
                                  "organizations", "common", "1.2.3.4", "*.contoso.com"])
def test_anything_but_a_domain_or_tenant_id_is_refused_before_any_call(text):
    s = _Session()
    with pytest.raises(ValueError):
        organizations.lookup(text, session=s)
    assert s.urls == []


def test_an_unknown_organization_says_so_plainly():
    with pytest.raises(ValueError, match="Microsoft has no organization at northwind.co"):
        organizations.lookup("northwind.co", session=_Session())


def test_personal_accounts_and_other_clouds_are_not_organizations_to_sign_in_to():
    with pytest.raises(ValueError, match="personal Microsoft accounts"):
        organizations.normalize(organizations.CONSUMER_TENANT)
    with pytest.raises(ValueError, match="personal Microsoft accounts"):
        organizations.lookup("personal.example", session=_Session())
    with pytest.raises(ValueError, match="separate Microsoft cloud"):
        organizations.lookup("northwind.com", session=_Session(cloud="microsoftonline.us"))


def test_no_connection_is_a_plain_message_not_not_found():
    with pytest.raises(RuntimeError, match="couldn't reach Microsoft"):
        organizations.lookup("northwind.com", session=_Session(fail=requests.ConnectionError("down")))
    assert organizations.tenant_id_of(HOST) == HOST  # an id needs no lookup


# ---------- the setting ----------

def test_sign_in_tenant_is_a_setting_people_change_and_it_is_kept_tidy():
    assert app_settings.load()["sign_in_tenant"] == {"value": "", "source": "default"}
    app_settings.save({"sign_in_tenant": " NorthWind.com "})
    assert app_settings.load()["sign_in_tenant"] == {"value": "northwind.com", "source": "user"}
    with pytest.raises(ValueError):
        app_settings.save({"sign_in_tenant": "https://evil/../x y"})
    app_settings.save({"sign_in_tenant": ""})
    assert app_settings.load()["sign_in_tenant"]["source"] == "default"


def test_policy_can_fix_the_organization_and_a_bad_value_is_ignored_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "POLICY_SETS_SIGN_IN", True)
    _policy(tmp_path, {"sign_in_tenant": "northwind.com"})
    assert app_settings.load()["sign_in_tenant"] == {"value": "northwind.com", "source": "policy"}
    with pytest.raises(ValueError, match="Your organization sets sign_in_tenant"):
        app_settings.save({"sign_in_tenant": ""})
    _policy(tmp_path, {"sign_in_tenant": "not a tenant"})
    assert app_settings.load()["sign_in_tenant"]["value"] == ""
    assert any("sign_in_tenant must be" in p for p in app_settings.policy_problems())


def test_this_build_never_takes_the_organization_from_a_policy_file_and_the_persons_choice_stands(tmp_path):
    _policy(tmp_path, {"sign_in_tenant": "northwind.com"})
    assert app_settings.load()["sign_in_tenant"] == {"value": "", "source": "default"}
    assert any("sign_in_tenant isn't applied from the policy file" in p for p in app_settings.policy_problems())
    app_settings.save({"sign_in_tenant": HOST})
    assert app_settings.load()["sign_in_tenant"] == {"value": HOST, "source": "user"}


# ---------- sign-in goes to that tenant ----------

def test_the_authenticator_signs_in_to_the_chosen_tenants_own_authority(tmp_path):
    own = auth.Authenticator(cache_path=tmp_path / "a.bin")
    assert own.app.authority.tenant == "organizations" and own.tenant == ""
    guest = auth.Authenticator(cache_path=tmp_path / "b.bin", tenant=HOST.upper())
    assert guest.tenant == HOST and guest.tenant_id == HOST
    assert guest.app.authority.tenant == HOST
    with pytest.raises(ValueError):
        auth.Authenticator(cache_path=tmp_path / "c.bin", tenant="x/../y")


def _signed_in(a, home=HOME):
    a.app.get_accounts = lambda: [{"username": "chris@contoso.com", "home_account_id": f"{OBJ}.{home}"}]
    return a


def test_a_guest_works_in_the_host_tenant_and_knows_its_home(tmp_path):
    a = _signed_in(auth.Authenticator(cache_path=tmp_path / "a.bin", tenant=HOST))
    assert a.home_tenant() == HOME and a.signed_in_tenant() == HOST and a.is_guest()
    own = _signed_in(auth.Authenticator(cache_path=tmp_path / "b.bin"))
    assert own.signed_in_tenant() == HOME and not own.is_guest()
    mine = _signed_in(auth.Authenticator(cache_path=tmp_path / "c.bin", tenant=HOME))
    assert mine.signed_in_tenant() == HOME and not mine.is_guest()  # their own tenant, named by id


def test_a_domain_that_couldnt_be_looked_up_learns_its_tenant_from_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(organizations, "tenant_id_of", lambda value: None)
    a = _signed_in(auth.Authenticator(cache_path=tmp_path / "a.bin", tenant="northwind.com"))
    assert a.signed_in_tenant() is None
    a._check_tenant({"id_token_claims": {"tid": HOST}})
    assert a.signed_in_tenant() == HOST and a.is_guest()


def test_allowed_tenants_still_applies_to_the_host_tenant(tmp_path):
    _policy(tmp_path, {"allowed_tenants": [HOME]})
    a = _signed_in(auth.Authenticator(cache_path=tmp_path / "a.bin", tenant=HOST))
    a.sign_out = lambda: None
    with pytest.raises(RuntimeError, match="only allows signing in with its own work accounts"):
        a._check_tenant({"id_token_claims": {"tid": HOST}})


# ---------- Switch organization ----------

class _Auth:
    def __init__(self, tenant):
        self.tenant = tenant

    def signed_in_user(self):
        return "chris@contoso.com"

    def home_tenant(self):
        return HOME

    def signed_in_tenant(self):
        return self.tenant or HOME

    def is_guest(self):
        return bool(self.tenant) and self.tenant != HOME


class _Svc(OrganizationsMixin):
    def __init__(self, root, sign_in_error=None):
        self.root, self.sign_in_error, self.signed_in_to = root, sign_in_error, []
        self._tables = {"cached": True}

    @property
    def auth(self):
        self._auth = _Auth(app_settings.value("sign_in_tenant"))
        return self._auth

    def sign_in(self):
        self.signed_in_to.append(app_settings.value("sign_in_tenant"))
        if self.sign_in_error:
            raise self.sign_in_error
        return {}


@pytest.fixture
def metadata(monkeypatch):
    s = _Session()
    real = organizations.lookup
    monkeypatch.setattr(organizations, "lookup", lambda text, session=None: real(text, session=s))
    return s


def test_switching_checks_first_signs_in_there_and_remembers_it(tmp_path, metadata):
    svc = _Svc(tmp_path)
    out = svc.organization_switch("northwind.com")
    assert svc.signed_in_to == [HOST] and svc._tables is None
    assert app_settings.load()["sign_in_tenant"] == {"value": HOST, "source": "user"}
    assert out["guest"] and out["current"]["name"] == "northwind.com" and out["home_domain"] == "contoso.com"
    assert [r["domain"] for r in out["recent"]] == ["northwind.com"]
    assert svc._guest_label() == "northwind.com"
    back = svc.organization_switch("")
    assert svc.signed_in_to == [HOST, ""] and not back["guest"] and back["current"] is None
    assert [r["tenant_id"] for r in back["recent"]] == [HOST]  # still offered next time


def test_a_sign_in_that_doesnt_finish_puts_the_organization_back(tmp_path, metadata):
    app_settings.save({"sign_in_tenant": HOME})
    svc = _Svc(tmp_path, sign_in_error=RuntimeError("The sign-in window was closed"))
    with pytest.raises(RuntimeError):
        svc.organization_switch(HOST)
    assert app_settings.value("sign_in_tenant") == HOME
    assert organizations.recent(tmp_path) == []


def test_an_unknown_organization_never_reaches_sign_in(tmp_path, metadata):
    svc = _Svc(tmp_path)
    with pytest.raises(ValueError):
        svc.organization_switch("northwind.co")
    assert svc.signed_in_to == [] and app_settings.value("sign_in_tenant") == ""


def test_it_fixed_organization_cant_be_switched_and_says_it_chose_it(tmp_path, metadata, monkeypatch):
    monkeypatch.setattr(app_settings, "POLICY_SETS_SIGN_IN", True)
    _policy(tmp_path, {"sign_in_tenant": "northwind.com"})
    svc = _Svc(tmp_path)
    status = svc.organization_status()
    assert status["fixed"] and status["current"]["name"] == "northwind.com"
    with pytest.raises(PermissionError, match="IT chose the organization"):
        svc.organization_switch("")
    assert svc.signed_in_to == []


def test_a_host_outside_allowed_tenants_is_refused_at_the_check(tmp_path, metadata):
    _policy(tmp_path, {"allowed_tenants": [HOME]})
    svc = _Svc(tmp_path)
    with pytest.raises(PermissionError, match="isn't one of them"):
        svc.organization_check("northwind.com")
    assert svc.signed_in_to == []


def test_the_recent_list_is_this_pcs_ids_and_domains_only_and_a_bad_file_is_ignored(tmp_path):
    organizations.remember(tmp_path, {"tenant_id": HOST, "domain": "northwind.com", "name": "x"})
    saved = json.loads((tmp_path / organizations.RECENT_FILE).read_text(encoding="utf-8"))
    assert set(saved["recent"][0]) == {"tenant_id", "domain", "used"}
    (tmp_path / organizations.RECENT_FILE).write_text(json.dumps({"recent": [{"tenant_id": "x"}, "y",
                                                                             {"tenant_id": HOST, "domain": "a b"}]}))
    assert organizations.recent(tmp_path) == [{"tenant_id": HOST, "domain": "", "name": HOST, "used": 0}]
    for n in range(organizations.RECENT_MAX + 2):
        organizations.remember(tmp_path, {"tenant_id": f"{n:08d}-1111-2222-3333-444444444444", "domain": ""})
    assert len(organizations.recent(tmp_path)) == organizations.RECENT_MAX


def test_status_reads_only(tmp_path, monkeypatch):
    def no_call(*a, **k):
        raise AssertionError("organization_status called Microsoft")
    monkeypatch.setattr(organizations, "lookup", no_call)
    app_settings.save({"sign_in_tenant": HOST})
    status = _Svc(tmp_path).organization_status()
    assert status["current"] == {"tenant_id": HOST, "domain": "", "name": HOST} and status["guest"]

