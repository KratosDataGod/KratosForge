"""The Keys workspace's setup (central/keys_home.py): a kept workspace, a step that fails, key tables that
can't be made yet and a caller who can't claim admin. Fabric and UDF_Keys are fakes: nothing is reached."""

from fabric_desktop.central import keys_home as kh


class Fabric:
    def __init__(self, fail=""):
        self.fail = fail
        self.saved = []

    def list_workspaces(self):
        return []

    def create_workspace(self, name, capacity, description):
        if self.fail == "workspace":
            raise RuntimeError("no capacity")
        return {"id": "ws1", "displayName": name}

    def save_item(self, ws, item_type, name, definition=None, description=""):
        self.saved.append(name)
        if self.fail == name:
            raise RuntimeError(f"{name} refused")
        return {"id": f"{name}-id", "created": False}


def platform(answers):
    class Client:
        def __init__(self, token, ws, fid):
            self.where = (ws, fid)

        def call(self, op):
            got = answers[op]
            if isinstance(got, Exception):
                raise got
            return got

    return Client


def test_setup_keeps_the_workspace_it_has_and_a_caller_who_cant_claim_isnt_made_admin(
    monkeypatch,
):
    monkeypatch.setattr(
        kh,
        "FabricPlatform",
        platform(
            {
                "ensure_schema": {"created": []},
                "whoami": {"can_claim": False},
                "claim_admin": AssertionError("not called"),
            }
        ),
    )
    fabric = Fabric()
    out = kh.setup(fabric, lambda: "t", existing={"workspace_id": "ws0"})
    assert out["ready"] is True
    assert [s["detail"] for s in out["steps"]] == [
        "Kept ws0",
        "Kept SQL_Keys",
        "Updated UDF_Keys",
        "Tables were already there",
    ]
    assert out["config"]["functions_id"] == "UDF_Keys-id"


def test_setup_stops_at_a_failed_step_and_says_why():
    out = kh.setup(
        Fabric(fail="SQL_Keys"),
        lambda: "t",
        existing={"workspace_id": "ws0", "workspace_name": "Keys"},
    )
    assert out["ready"] is False
    assert out["steps"] == [
        {"step": "Keys workspace", "status": "done", "detail": "Kept Keys"},
        {"step": "SQL_Keys", "status": "failed", "detail": "SQL_Keys refused"},
    ]


def test_setup_says_how_to_connect_the_functions_when_the_key_tables_cant_be_made(
    monkeypatch,
):
    monkeypatch.setattr(
        kh, "FabricPlatform", platform({"ensure_schema": RuntimeError("no connection")})
    )
    out = kh.setup(Fabric(), lambda: "t", name="My keys")
    assert out["ready"] is False
    assert out["steps"][0]["detail"] == "Created My keys"
    last = out["steps"][-1]
    assert last["step"] == "Key tables" and last["status"] == "failed"
    assert last["detail"].startswith(
        "no connection. If UDF_Keys has no connection yet, open it in Fabric"
    )
    assert f'alias "{kh.udf.ALIAS}"' in last["detail"]


def test_setup_with_no_workspace_goes_no_further():
    fabric = Fabric(fail="workspace")
    out = kh.setup(fabric, lambda: "t")
    assert out["steps"] == [
        {"step": "Keys workspace", "status": "failed", "detail": "no capacity"}
    ]
    assert fabric.saved == []
