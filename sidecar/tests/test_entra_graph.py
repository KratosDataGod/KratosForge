"""Entra ID groups per domain, the engine side: the directory reads (Microsoft Graph, Group.Read.All as the
signed-in person) and what the dialog falls back to when the platform or the directory can't answer.

No network: the Graph session and the platform catalog are stand-ins."""

import re

import pytest

from fabric_desktop import http_session
from fabric_desktop.auth import GRAPH_GROUP_SCOPES
from fabric_desktop.central import core, entra
from fabric_desktop.central.entra_service import GRAPH, NO_GRAPH, EntraGroupsMixin

GID = "7c1e2f3a-1111-2222-3333-44445555a90d"
OTHER = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
OWNER = "ana@contoso.com"
PARENT_OWNER = "raj@contoso.com"
DOMAINS = [{"id": "sales", "name": "Sales", "owners": OWNER, "parent_id": "corp"},
           {"id": "sales-emea", "name": "Sales EMEA", "parent_id": "sales"},
           {"id": "corp", "name": "Corp", "owners": PARENT_OWNER}]


class Reply:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text
        self.ok = status < 400
        self.headers = {}

    def json(self):
        return self._body


class Graph:
    """http_session.session(): records each GET and answers from ``replies`` (a function of path, params)."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def get(self, url, params=None, headers=None, timeout=None):
        assert url.startswith(GRAPH) and timeout
        self.calls.append({"path": url[len(GRAPH):], "params": params, "headers": headers})
        return self.replies(url[len(GRAPH):], params or {})


class Auth:
    def __init__(self, fail=False):
        self.fail, self.scopes = fail, []

    def _token(self, scopes):
        self.scopes.append(scopes)
        if self.fail:
            raise RuntimeError("AADSTS65001: The user or administrator has not consented")
        return "graph-token"


class Catalog:
    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        answer = self.answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        return answer(**kw) if callable(answer) else answer


class Svc(EntraGroupsMixin):
    def __init__(self, catalog, auth=None, me=None):
        self.catalog, self.auth = catalog, auth or Auth()
        self.me = me or {"admin": True, "user": "chris@example.com"}

    def _catalog(self):
        return self.catalog

    def platform_domains(self):
        return DOMAINS

    def platform_whoami(self):
        return self.me

    def _user(self):
        return self.me.get("user") or ""


def _catalog(links=None, products=None, naming=None):
    links = links or {}
    return Catalog(entra_group_naming=naming if naming is not None else {},
                   list_entra_groups=lambda domain: list(links.get(domain, [])),
                   list_products=products if products is not None else [])


@pytest.fixture
def graph(monkeypatch):
    """Install a Graph stand-in; call it with the reply function."""
    def install(replies):
        g = Graph(replies)
        monkeypatch.setattr(http_session, "session", lambda: g)
        return g
    return install


# --------------------------------------------------------------------------- one Graph read

def test_a_read_sends_the_group_scope_token_and_returns_the_json(graph):
    g = graph(lambda path, params: Reply(200, {"id": GID, "displayName": "Sales Readers"}))
    svc = Svc(_catalog())
    assert svc._graph(f"/groups/{GID}", {"$select": "id"}) == {"id": GID, "displayName": "Sales Readers"}
    assert svc.auth.scopes == [GRAPH_GROUP_SCOPES]
    assert g.calls[0]["headers"] == {"Authorization": "Bearer graph-token"}
    svc._graph("/groups", {"$search": '"displayName:x"'}, eventual=True)
    assert g.calls[1]["headers"]["ConsistencyLevel"] == "eventual"


def test_no_consent_and_refused_reads_say_to_paste_the_object_id(graph):
    g = graph(lambda path, params: Reply(200, {}))
    with pytest.raises(PermissionError) as no_token:
        Svc(_catalog(), auth=Auth(fail=True))._graph("/groups")
    assert str(no_token.value) == NO_GRAPH and not g.calls  # nothing is sent without a token
    for status in (401, 403):
        graph(lambda path, params, s=status: Reply(s, text="Authorization_RequestDenied"))
        with pytest.raises(PermissionError, match="object ID"):
            Svc(_catalog())._graph("/groups")


def test_a_missing_group_is_none_and_other_errors_quote_graph_on_one_line(graph):
    graph(lambda path, params: Reply(404, text="Request_ResourceNotFound"))
    assert Svc(_catalog())._graph(f"/groups/{GID}") is None
    graph(lambda path, params: Reply(400, text="Invalid filter\nclause " + "x" * 500))
    with pytest.raises(RuntimeError) as err:
        Svc(_catalog())._graph("/groups")
    assert str(err.value).startswith("Microsoft Graph answered 400: Invalid filter")
    assert "\n" not in str(err.value) and len(str(err.value)) < 260


# --------------------------------------------------------------------------- linking reads the group's name

def test_linking_without_a_name_reads_it_from_the_directory(graph):
    g = graph(lambda path, params: Reply(200, {"id": GID, "displayName": "Sales Analytics Readers"}))
    cat = _catalog(links={"sales": [{"domain_id": "sales", "role": "readers", "env": "prod", "group_id": GID,
                                     "group_name": "Sales Analytics Readers"}]})
    cat.answers["link_entra_group"] = lambda **kw: [kw]
    out = Svc(cat).platform_entra_link("sales", "readers", "prod", f"  {GID} ")
    assert g.calls[0]["path"] == f"/groups/{GID}" and g.calls[0]["params"] == {"$select": "id,displayName"}
    sent = [kw for name, kw in cat.calls if name == "link_entra_group"]
    assert sent == [{"domain": "sales", "role": "readers", "env": "prod", "group_id": GID,
                     "group_name": "Sales Analytics Readers"}]
    assert out["linked"][0]["group_id"] == GID


def test_linking_keeps_the_id_when_the_directory_cant_be_read_and_refuses_an_unknown_one(graph):
    cat = _catalog()
    cat.answers["link_entra_group"] = []
    graph(lambda path, params: Reply(403))
    Svc(cat).platform_entra_link("sales", "stewards", "", GID)
    assert [kw["group_name"] for name, kw in cat.calls if name == "link_entra_group"] == [""]
    graph(lambda path, params: Reply(404))
    with pytest.raises(LookupError, match=GID):
        Svc(cat).platform_entra_link("sales", "stewards", "", GID)


def test_a_named_link_never_reads_the_directory(graph):
    g = graph(lambda path, params: Reply(500))
    cat = _catalog()
    cat.answers["link_entra_group"] = []
    Svc(cat).platform_entra_link("sales", "readers", "prod", GID, "Readers", envs=["dev", "prod"])
    assert not g.calls
    assert [kw["env"] for name, kw in cat.calls if name == "link_entra_group"] == ["dev", "prod"]


# --------------------------------------------------------------------------- finding a group

@pytest.mark.parametrize("group, kind, usable", [
    ({"securityEnabled": True, "mailEnabled": False, "groupTypes": []}, "Security group", True),
    ({"securityEnabled": True, "mailEnabled": True, "groupTypes": []}, "Mail-enabled security group", True),
    ({"securityEnabled": False, "mailEnabled": True, "groupTypes": ["Unified"]}, "Microsoft 365 group", True),
    ({"securityEnabled": True, "mailEnabled": True, "groupTypes": ["Unified"]}, "Microsoft 365 group", True),
    ({"securityEnabled": False, "mailEnabled": True, "groupTypes": None}, "Distribution list", False),
])
def test_each_kind_of_group_says_whether_it_can_hold_a_fabric_role(group, kind, usable):
    out = EntraGroupsMixin._entra_group({"id": GID, "displayName": "G"} | group)
    assert (out["kind"], out["usable"]) == (kind, usable)
    assert out["description"] == "" and out["name"] == "G"


def test_find_by_words_searches_names_without_quotes_or_backslashes(graph):
    g = graph(lambda path, params: Reply(200, {"value": [
        {"id": GID, "displayName": "Sales Readers", "securityEnabled": True, "groupTypes": []},
        {"id": OTHER, "displayName": "Sales list", "mailEnabled": True, "groupTypes": []}]}))
    found = Svc(_catalog()).platform_entra_find(' sales "re\\aders ')
    assert [(f["name"], f["usable"]) for f in found] == [("Sales Readers", True), ("Sales list", False)]
    call = g.calls[0]
    assert call["path"] == "/groups" and call["headers"]["ConsistencyLevel"] == "eventual"
    assert call["params"]["$search"] == '"displayName:sales  re aders"'
    assert call["params"]["$top"] == "25"


def test_find_by_object_id_reads_that_group_and_short_queries_send_nothing(graph):
    g = graph(lambda path, params: Reply(200, {"id": GID, "displayName": "Sales Readers", "securityEnabled": True}))
    svc = Svc(_catalog())
    assert [f["id"] for f in svc.platform_entra_find(GID)] == [GID]
    assert g.calls[0]["path"] == f"/groups/{GID}"
    graph(lambda path, params: Reply(404))
    assert svc.platform_entra_find(GID) == []
    g = graph(lambda path, params: Reply(200, {}))
    assert svc.platform_entra_find("s") == [] and svc.platform_entra_find("  ") == [] and svc.platform_entra_find(None) == []
    assert not g.calls
    assert svc.platform_entra_find("sales") == []  # an answer without "value"


# --------------------------------------------------------------------------- which groups exist already

def test_check_finds_suggested_groups_by_name_and_linked_groups_by_id(graph):
    links = {"sales": [{"domain_id": "sales", "role": "stewards", "env": "", "group_id": GID, "group_name": "S"},
                       {"domain_id": "sales", "role": "readers", "env": "prod", "group_id": OTHER, "group_name": "R"}]}

    def replies(path, params):
        if path == "/groups":
            return Reply(200, {"value": [{"id": "new-admins", "displayName": admins.lower()}]})
        return Reply(200, {"id": GID}) if path.endswith(GID) else Reply(404)
    cat = _catalog(links=links)
    rows = Svc(cat)._entra_plan("sales", None, False)[1]
    admins = next(r["suggested"] for r in rows if r["role"] == "domain_admins")
    readers = next(r["suggested"] for r in rows if r["role"] == "readers")
    g = graph(replies)
    status = Svc(cat).platform_entra_check("sales")["status"]
    assert status["sales|domain_admins|"] == {"exists": True, "id": "new-admins"}  # names match whatever the case
    assert status["sales|domain_contributors|"] == {"exists": False, "id": None}
    assert status["sales|stewards|"] == {"exists": True, "id": GID}
    assert status["sales|readers|prod"] == {"exists": False, "id": OTHER}  # linked, then deleted
    filters = [c["params"]["$filter"] for c in g.calls if c["path"] == "/groups"]
    assert len(filters) == 1 and f"'{admins}'" in filters[0] and readers not in filters[0]  # linked roles aren't looked up by name


def test_check_asks_at_most_fifteen_names_at_a_time_and_doubles_single_quotes(graph):
    g = graph(lambda path, params: Reply(200, {"value": []}))
    naming = {"pattern": "{PREFIX}-{DOMAIN}-{ROLE}-{ENV}", "prefix": "SG"}
    cat = _catalog(naming=naming)
    rows = Svc(cat)._entra_plan("sales", None, True)[1]
    assert len(rows) > 15  # the domain and its subdomain
    Svc(cat).platform_entra_check("sales", subdomains=True)
    filters = [c["params"]["$filter"] for c in g.calls]
    assert len(filters) == 2 and filters[0].count("'SG-") == 15
    assert all(f.startswith("displayName in (") and f.endswith(")") for f in filters)

    graph(lambda path, params: Reply(200, {"value": []}))
    svc = Svc(cat)
    svc._entra_plan = lambda *a: ({}, [{"domain_id": "sales", "role": "readers", "env": "prod",
                                        "suggested": "O'Brien Readers", "linked": None}])
    svc.platform_entra_check("sales")
    assert "'O''Brien Readers'" in http_session.session().calls[0]["params"]["$filter"]


# --------------------------------------------------------------------------- the platform answering less

def test_naming_falls_back_to_the_default_when_the_platform_cant_say(graph):
    default = core.checked_entra_naming({})
    for answer in (LookupError("no entra_group_naming"), core.PlatformError("old UDF_Platform"), None, "nonsense"):
        assert Svc(Catalog(entra_group_naming=answer))._entra_naming() == default


def test_links_are_empty_when_the_platform_has_none(graph):
    for answer in (LookupError("no list_entra_groups"), core.PlatformError("older catalog")):
        assert Svc(Catalog(list_entra_groups=answer))._entra_links("sales") == []


def test_an_unknown_domain_is_refused():
    with pytest.raises(ValueError, match="isn't a domain"):
        Svc(_catalog())._entra_plan("hr", None, False)


def test_subdomains_bring_their_own_links():
    links = {"sales-emea": [{"domain_id": "sales-emea", "role": "stewards", "env": "", "group_id": GID,
                             "group_name": "EMEA Stewards"}]}
    cat = _catalog(links=links)
    rows = Svc(cat)._entra_plan("sales", None, True)[1]
    assert [r["name"] for r in rows if r["domain_id"] == "sales-emea" and r["role"] == "stewards"] == ["EMEA Stewards"]
    assert [kw["domain"] for name, kw in cat.calls if name == "list_entra_groups"] == ["sales", "sales-emea"]


# --------------------------------------------------------------------------- who may link

def test_owners_of_the_domain_or_its_parent_may_link_but_only_admins_name():
    for user, can_link in ((OWNER, True), (PARENT_OWNER, True), ("eve@contoso.com", False), ("", False)):
        out = Svc(_catalog(), me={"admin": False, "user": user}).platform_entra_groups("sales")
        assert (out["can_link"], out["can_name"]) == (can_link, False), user
    admin = Svc(_catalog(), me={"admin": True, "user": "eve@contoso.com"}).platform_entra_groups("sales")
    assert admin["can_link"] and admin["can_name"] and admin["owners"] == [OWNER]


# --------------------------------------------------------------------------- the roles script

def test_roles_script_names_the_domains_workspaces_per_environment():
    products = [
        {"domain_id": "sales", "ports": [
            {"env": "prod", "workspace_id": "ws-p", "workspace_name": "Sales Prod"},
            {"env": "dev", "workspace_id": "ws-d"},  # no name: the id stands in
            {"env": "prod", "workspace_id": "ws-p", "workspace_name": "Sales Prod"},  # two products, one workspace
            {"env": "sandbox", "workspace_id": "ws-x", "workspace_name": "Not an environment"},
            {"env": "test", "workspace_id": "", "workspace_name": "No id"}]},
        {"domain_id": "hr", "ports": [{"env": "prod", "workspace_id": "ws-hr", "workspace_name": "HR"}]},
        {"domain_id": "sales", "ports": None}]
    svc = Svc(_catalog(products=products))
    assert svc._entra_workspaces({"sales"}) == {"prod": [{"id": "ws-p", "name": "Sales Prod"}],
                                                "dev": [{"id": "ws-d", "name": "ws-d"}]}
    out = svc.platform_entra_script("sales", "roles")
    assert out["kind"] == "roles" and out["steps"] == [] and "ws-p" in out["text"] and "ws-hr" not in out["text"]


def test_roles_script_still_comes_when_products_cant_be_read():
    svc = Svc(_catalog(products=core.PlatformError("catalog offline")))
    assert svc._entra_workspaces({"sales"}) == {}
    text = svc.platform_entra_script("sales", "roles")["text"]
    stamp = re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC", text).group(0)  # as written, so this can't race the clock
    assert text == entra.roles(svc._entra_plan("sales", None, False)[1], {}, "chris@example.com", stamp)


def test_saving_the_naming_goes_to_the_catalog():
    cat = _catalog()
    cat.answers["save_entra_group_naming"] = lambda naming: naming | {"saved": True}
    assert Svc(cat).platform_entra_naming_save({"prefix": "SG"}) == {"prefix": "SG", "saved": True}
