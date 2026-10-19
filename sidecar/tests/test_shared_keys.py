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
    out = svc.platform_share_keys(approve=True)
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
    assert svc.platform_share_keys(approve=True)["already"]


def test_nothing_switches_unless_every_key_table_checks_out(svc, monkeypatch):
    _issue(svc, "prod", "customer", ["ada"])
    monkeypatch.setattr(keys_home, "copy_keeping_numbers",
                        lambda rows, name, shared: {"table": name, "copied": 1, "renumbered": 0, "match": False, "differ": [1]})
    out = svc.platform_share_keys(approve=True)
    assert not out["ok"] and not out["status"]["keys"]["shared"]
    assert svc._keys_platform("dev") is svc._platform("dev")  # still each environment's own


def test_a_database_prod_shares_with_dev_keeps_prods_numbers(svc, monkeypatch):
    real = svc._platform
    monkeypatch.setattr(svc, "_platform", lambda env: real("prod") if env in ("dev", "prod") else real(env))
    prod = _issue(svc, "prod", "customer", ["b", "a"])
    out = svc.platform_share_keys(approve=True)
    assert out["renumbered"] == 0 and [t["env"] for t in out["tables"]] == ["prod"]
    assert {bk: sk for sk, bk, _ in svc._platform_keys("dev").export("customer") if sk != K.UNKNOWN_KEY} == prod


def test_new_key_tables_go_in_the_one_database(svc):
    svc.platform_share_keys(approve=True)
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
    svc.platform_share_keys(approve=True)
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
    svc.platform_share_keys(approve=True)
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
        return {"id": wid, "displayName": "KratosForge Keys"}

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
    assert out["config"] == {"workspace_id": "ws-keys", "workspace_name": "KratosForge Keys",
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
    svc.platform_share_keys(approve=True)
    svc.meta_keys_to_platform("dev")
    svc._platform_keys("dev").issue("customer", ["brand new"])
    with pytest.raises(GateError, match="reuse"):
        svc.meta_keys_to_lakehouse("dev")


def test_keys_in_fabric_never_move_into_a_database_on_this_pc(svc):
    svc._save_settings(platform={"prod": {"workspace_id": "ws", "functions_id": "fn"}})
    with pytest.raises(LookupError, match="Create or join the Keys workspace first"):
        svc.platform_share_keys(approve=True)
    assert not svc.platform_status()["keys"]["shared"]


def test_without_approval_the_move_only_says_what_it_would_do(svc):
    _issue(svc, "prod", "customer", ["ada", "grace"])
    _issue(svc, "dev", "customer", ["aaron", "ada"])
    out = svc.platform_share_keys()
    assert out["preview"] and out["message"].endswith("Confirm to continue.")
    assert {(t["env"], t["keys"], t["renumbered"]) for t in out["tables"]} == {("prod", 2, 0), ("dev", 2, 2)}
    assert not svc._shared_keys().call("list_key_tables")  # nothing copied
    assert not svc.platform_status()["keys"]["shared"]
    _issue(svc, "prod", "customer", ["still open"])  # and no environment's keys closed


def test_no_environment_issues_keys_while_they_move(svc, monkeypatch):
    """A Prod load during the move can't take a number that belongs to someone else afterwards."""
    _issue(svc, "prod", "customer", ["ada", "grace"])
    _issue(svc, "dev", "customer", ["aaron"])
    real, late = keys_home.add_renumbering, []

    def during(rows, name, shared):
        out = real(rows, name, shared)
        with pytest.raises(Exception, match="Keys are moving"):
            PlatformKeys(svc._platform("prod"), "prod").issue("customer", ["late customer"])
        late.append(True)
        return out

    monkeypatch.setattr(keys_home, "add_renumbering", during)
    assert svc.platform_share_keys(approve=True)["ok"] and late
    # Afterwards each environment's own keys stay closed and say where keys are now.
    with pytest.raises(Exception, match="Keys moved to the keys database on this PC"):
        PlatformKeys(svc._platform("prod"), "prod").issue("customer", ["late customer"])


def test_keys_that_change_during_the_move_stop_the_switch(svc, monkeypatch):
    _issue(svc, "prod", "customer", ["ada"])
    snaps = iter([{("prod", "customer"): (1, 2)}, {("prod", "customer"): (2, 3)}])
    monkeypatch.setattr(keys_home, "snapshot", lambda sources: next(snaps))
    out = svc.platform_share_keys(approve=True)
    assert not out["ok"] and out["changed_during_move"] == ["Prod customer"]
    assert not out["status"]["keys"]["shared"]


def test_a_move_that_stops_runs_again_or_is_cancelled(svc, monkeypatch):
    prod = _issue(svc, "prod", "customer", ["ada"])
    _issue(svc, "dev", "customer", ["aaron"])
    _issue(svc, "test", "customer", ["tess"])
    real = keys_home.add_renumbering

    def drop(rows, name, shared):
        if not drop.done:
            drop.done = True
            raise ConnectionError("network dropped")
        return real(rows, name, shared)

    drop.done = False
    monkeypatch.setattr(keys_home, "add_renumbering", drop)
    out = svc.platform_share_keys(approve=True)
    assert not out["ok"] and "network dropped" in out["message"] and not out["status"]["keys"]["shared"]
    with pytest.raises(Exception, match="Keys are moving"):  # Prod can't issue in between
        PlatformKeys(svc._platform("prod"), "prod").issue("customer", ["new prod customer"])
    out = svc.platform_share_keys(approve=True)  # the same keys copy again
    assert out["ok"] and svc._platform_keys("prod").lookup("customer", "ada")["sk"] == prod["ada"]

    # A move that is cancelled instead empties the keys database and reopens every environment.
    svc2 = Service(root=svc.root.parent / "app2")
    _issue(svc2, "prod", "customer", ["ada"])
    monkeypatch.setattr(keys_home, "add_renumbering", lambda *a: (_ for _ in ()).throw(ConnectionError("down")))
    _issue(svc2, "dev", "customer", ["aaron"])
    assert not svc2.platform_share_keys(approve=True)["ok"]
    assert svc2.platform_cancel_share_keys()["preview"]
    assert svc2.platform_cancel_share_keys(approve=True)["cleared"] == ["customer"]
    assert not svc2._shared_keys().call("list_key_tables")
    PlatformKeys(svc2._platform("prod"), "prod").issue("customer", ["open again"])


def test_keys_in_use_are_never_cleared(svc):
    svc.platform_share_keys(approve=True)
    with pytest.raises(GateError, match="can't be cancelled"):
        svc.platform_cancel_share_keys(approve=True)
    with pytest.raises(Exception, match="these keys are in use"):
        svc._shared_keys().call("clear_keys")


def test_a_platform_set_by_policy_moves_nothing_from_this_pc(svc, monkeypatch):
    _issue(svc, "prod", "customer", ["ada"])
    monkeypatch.setattr(Service, "_platform_source", lambda self: "policy")
    with pytest.raises(PermissionError, match="organization sets the platform"):
        svc.platform_share_keys(approve=True)
    assert not svc._shared_keys().call("list_key_tables")
