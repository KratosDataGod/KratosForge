"""One keys database for Dev, Test and Prod (item 25, Chris chose "One for all" on 2026-10-19)."""

import json

import pytest

from fabric_desktop.central import keys_home, udf
from fabric_desktop.central.client import PlatformKeys
from fabric_desktop.meta import keys as K
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _issue(svc, env, name, bks):
    client = svc._platform(env)
    client.call("ensure_key_table", name=name)
    return PlatformKeys(client, env).issue(name, bks)[0]


def test_every_environment_moves_in_and_prod_keeps_its_numbers(svc):
    prod = _issue(svc, "prod", "customer", ["ada", "grace"])
    dev = _issue(svc, "dev", "customer", ["aaron", "ada", "linus"])  # Dev numbered these its own way
    assert dev["ada"] != prod["ada"]
    out = svc.platform_share_keys()
    assert out["ok"] and out["status"]["keys"]["shared"]
    shared = {bk: sk for sk, bk, _ in svc._platform_keys("prod").export("customer") if sk != K.UNKNOWN_KEY}
    assert {bk: shared[bk] for bk in prod} == prod  # no Prod key changed
    assert shared["ada"] == prod["ada"]  # Dev's ada is Prod's ada now
    assert set(shared) == {"ada", "grace", "aaron", "linus"}
    assert out["renumbered"] == 3  # every Dev key: the one Prod had, and the two it didn't
    assert len(set(shared.values())) == 4
    # From now on Dev, Test and Prod issue from the same database: a key issued in Dev is Prod's too.
    new, _ = svc._platform_keys("dev").issue("customer", ["margaret"])
    assert svc._platform_keys("prod").lookup("customer", "margaret")["sk"] == new["margaret"]
    assert svc._platform_keys("test").stats("customer") == svc._platform_keys("prod").stats("customer")
    # Each environment's own database is left as it was.
    assert PlatformKeys(svc._platform("dev"), "dev").lookup("customer", "margaret") is None
    assert svc.platform_share_keys()["already"]


def test_nothing_switches_unless_every_key_table_checks_out(svc, monkeypatch):
    _issue(svc, "prod", "customer", ["ada"])
    monkeypatch.setattr(keys_home, "copy_keeping_numbers",
                        lambda rows, name, shared: {"table": name, "copied": 1, "renumbered": 0, "match": False, "differ": [1]})
    out = svc.platform_share_keys()
    assert not out["ok"] and not out["status"]["keys"]["shared"]
    assert svc._keys_platform("dev") is svc._platform("dev")  # still each environment's own


def test_a_database_prod_shares_with_dev_keeps_prods_numbers(svc, monkeypatch):
    real = svc._platform
    monkeypatch.setattr(svc, "_platform", lambda env: real("prod") if env in ("dev", "prod") else real(env))
    prod = _issue(svc, "prod", "customer", ["b", "a"])
    out = svc.platform_share_keys()
    assert out["renumbered"] == 0 and [t["env"] for t in out["tables"]] == ["prod"]
    assert {bk: sk for sk, bk, _ in svc._platform_keys("dev").export("customer") if sk != K.UNKNOWN_KEY} == prod


def test_new_key_tables_go_in_the_one_database(svc):
    svc.platform_share_keys()
    out = svc.platform_create_key_table("vendor", "vendor_id", "A vendor")
    assert [e["env"] for e in out["environments"]] == ["keys"]
    for env in ("dev", "test", "prod"):
        assert "vendor" in [t["name"] for t in svc.platform_key_tables(env)["key_tables"]]
    assert "vendor" not in [t["name"] for t in svc._platform("dev").call("list_key_tables")]


def test_a_projects_dev_keys_move_in_renumbered_and_say_so(svc):
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    project = svc._project()
    before = svc._keys_store(project, "dev").read(K.KEYS_LAKEHOUSE, K.KEY_SCHEMA, "customer")
    prod = _issue(svc, "prod", "customer", ["someone only prod has"])
    svc.platform_share_keys()
    out = svc.meta_keys_to_platform("dev")
    assert out["ok"] and out["renumbered"] > 0
    assert svc._project().environments["dev"]["keys"] == "platform"
    shared = svc._platform_keys("dev")
    assert shared.lookup("customer", "someone only prod has")["sk"] == prod["someone only prod has"]
    for bk in before.filter(before["customer_sk"] != K.UNKNOWN_KEY)["customer_bk"]:
        assert shared.lookup("customer", str(bk)) is not None
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6  # the re-run takes keys from the shared database


def test_projects_use_the_shared_keys_once_they_are_in_fabric(svc):
    project = svc._project()
    svc._save_settings(platform={"keys": {"shared": True, "workspace_id": "ws-keys", "functions_id": "fn-keys"}})
    assert svc._keys_in_platform(project, "test") and svc._keys_in_platform(project, "prod")
    client = svc._keys_platform("test")
    assert (client.workspace_id, client.functions_id) == ("ws-keys", "fn-keys")
    assert svc.meta_keys("prod")["shared"]


def test_only_platform_admins_add_key_tables_to_the_shared_database(svc):
    svc.platform_share_keys()
    path = svc._shared_keys().path
    first = type(svc._shared_keys())(path, lambda: "chris@example.com")  # the first to sign in claims it
    assert [a["user_name"] for a in first.call("list_admins")] == ["chris@example.com"]
    other = type(svc._shared_keys())(path, lambda: "someone@example.com")
    with pytest.raises(Exception, match="Only platform admins"):
        other.call("ensure_key_table", name="vendor")


class FakeFabric:
    def __init__(self, items=None):
        self.workspaces, self.items, self.saved = [], list(items or []), []

    def list_workspaces(self):
        return self.workspaces

    def create_workspace(self, name, capacity_id, description):
        ws = {"id": "ws-keys", "displayName": name}
        self.workspaces.append(ws)
        return ws

    def get_workspace(self, wid):
        return {"id": wid, "displayName": "Fabric Desktop Keys"}

    def list_items(self, wid):
        return self.items

    def save_item(self, ws, kind, name, definition=None, description=""):
        self.saved.append((ws, kind, name, definition))
        return {"id": f"{kind}-id", "created": True}


def test_setup_puts_the_database_and_its_functions_in_one_keys_workspace(monkeypatch):
    calls = []

    class Client:
        def __init__(self, token, ws, fn):
            calls.append((ws, fn))

        def call(self, op, **kw):
            return {"created": ["keys"]} if op == "ensure_schema" else {"can_claim": True} if op == "whoami" else {}

    monkeypatch.setattr(keys_home, "FabricPlatform", Client)
    fabric = FakeFabric()
    out = keys_home.setup(fabric, lambda: "tok")
    assert out["ready"], out["steps"]
    assert out["config"] == {"workspace_id": "ws-keys", "workspace_name": "Fabric Desktop Keys",
                             "sql_id": "SQLDatabase-id", "functions_id": "UserDataFunction-id"}
    [(_, _, sql, _), (_, _, fn, definition)] = fabric.saved
    assert (sql, fn) == ("SQL_Keys", "UDF_Keys")
    [source] = json.loads(definition["definition.json"])["connectedDataSources"]
    assert source == {"alias": udf.ALIAS, "artifactId": "SQLDatabase-id", "workspaceId": "ws-keys", "sourceType": "SqlDatabase"}
    assert calls == [("ws-keys", "UserDataFunction-id")]
    assert "you're its first admin" in out["steps"][-1]["detail"]


def test_joining_a_keys_workspace_finds_its_items_or_says_what_is_missing():
    fabric = FakeFabric([{"type": "SQLDatabase", "displayName": "SQL_Keys", "id": "s"},
                         {"type": "UserDataFunction", "displayName": "UDF_Keys", "id": "f"}])
    assert keys_home.join(fabric, "ws")["functions_id"] == "f"
    with pytest.raises(LookupError, match="UDF_Keys is missing"):
        keys_home.join(FakeFabric(fabric.items[:1]), "ws")


def test_going_back_to_lh_keys_is_still_refused_after_new_shared_keys(svc):
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    svc.platform_share_keys()
    svc.meta_keys_to_platform("dev")
    svc._platform_keys("dev").issue("customer", ["brand new"])
    with pytest.raises(GateError, match="reuse"):
        svc.meta_keys_to_lakehouse("dev")


def test_keys_in_fabric_never_move_into_a_database_on_this_pc(svc):
    svc._save_settings(platform={"prod": {"workspace_id": "ws", "functions_id": "fn"}})
    with pytest.raises(LookupError, match="Create or join the Keys workspace first"):
        svc.platform_share_keys()
    assert not svc.platform_status()["keys"]["shared"]
