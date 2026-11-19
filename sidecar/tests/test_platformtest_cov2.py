"""The smoke test's central platform flows (platformtest.py): a workspace that can't be read, items that
aren't the platform's, a setup that made nothing yet, and each flow's failure when the platform answers
wrongly. Fabric and UDF_Platform are fakes: nothing reaches a tenant."""

import json

import pytest

from fabric_desktop import platformtest as pt

WS = "11111111-1111-1111-1111-111111111111"


class Client:
    """A UDF_Platform that answers each operation from a dict (a list of answers is used in turn)."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def call(self, op, **kw):
        self.calls.append(op)
        got = self.answers.get(op)
        if isinstance(got, list) and got and isinstance(got[0], tuple):
            return got.pop(0)[0]
        return got


def flows(tmp_path, fabric=None, setup=None):
    return pt.PlatformTest(
        WS,
        fabric,
        lambda: "tok",
        tmp_path / "out",
        client=lambda ws, fid: None,
        setup=setup or (lambda *a: {}),
        run_id="run1",
    )


def test_a_workspace_that_cant_be_read_is_refused_before_anything_is_made(tmp_path):
    class Fabric:
        def get_workspace(self, ws):
            raise RuntimeError("403 Forbidden")

        def list_items(self, ws):
            raise AssertionError("not read")

    result = flows(tmp_path, Fabric()).run()
    assert result["refused"] == ["Couldn't read the workspace: 403 Forbidden"]
    assert result["checks"] == []


def test_items_that_arent_the_platforms_are_ignored():
    items = [
        {"displayName": "Sales report", "type": "Report"},
        {"displayName": "Notes", "type": "Notebook"},
    ]
    assert pt._platform_items(items) == ({}, [])


def test_setup_that_made_nothing_yet_adds_nothing_to_the_ledger(tmp_path):
    t = flows(
        tmp_path,
        setup=lambda *a: {"config": {"workspace_id": WS}, "steps": [], "ready": False},
    )
    assert t._setup()["ready"] is False
    assert t.ledger == [] and not (tmp_path / "out" / "platform-ledger.json").exists()


def test_a_product_that_isnt_listed_after_registering_fails(tmp_path):
    client = Client(
        register_product={"id": "fdlt_run1/fdlt_run1_product"},
        list_products=[{"id": "other/x"}],
    )
    with pytest.raises(
        RuntimeError, match="registered but isn't in the platform's product list"
    ):
        flows(tmp_path)._product(client)
    assert client.calls == ["register_project", "register_product", "list_products"]


def test_keys_issued_again_must_be_the_same(tmp_path):
    client = Client(
        issue_keys=[({"keys": {"A": 1, "B": 2}},), ({"keys": {"A": 3, "B": 4}},)]
    )
    with pytest.raises(
        RuntimeError, match="Issuing the same business keys again gave other keys"
    ):
        flows(tmp_path)._keys(client)


@pytest.mark.parametrize("found", [None, {"sk": 9}])
def test_a_key_that_looks_up_wrong_fails(tmp_path, found):
    keys = {"keys": {"A": 1, "B": 2}}
    client = Client(issue_keys=[(keys,), (keys,)], lookup_key=found)
    with pytest.raises(RuntimeError) as raised:
        flows(tmp_path)._keys(client)
    assert (
        str(raised.value) == f"Looking up A gave {found}, not the key it was issued (1)"
    )


def test_an_approved_exception_must_be_listed_active(tmp_path):
    client = Client(request={"id": "req1"}, list_policy_exceptions=[{"id": "other"}])
    with pytest.raises(
        RuntimeError, match="The approved exception isn't listed as active"
    ):
        flows(tmp_path)._exception(client)
    assert "end_policy_exception" not in client.calls


def test_an_ended_exception_must_not_stay_active(tmp_path):
    client = Client(request={"id": "req1"}, list_policy_exceptions=[{"id": "req1"}])
    with pytest.raises(
        RuntimeError, match="The exception is still active after it was ended"
    ):
        flows(tmp_path)._exception(client)
    assert client.calls[-2:] == ["end_policy_exception", "list_policy_exceptions"]


def test_the_ledger_names_the_run_and_workspace(tmp_path):
    t = flows(tmp_path)
    t._remember("UDF_Platform", "id-1")
    t._remember("UDF_Platform", "id-1")  # once
    body = json.loads(
        (tmp_path / "out" / "platform-ledger.json").read_text(encoding="utf-8")
    )
    assert body == {
        "run_id": "run1",
        "workspace_id": WS,
        "items": [{"name": "UDF_Platform", "id": "id-1"}],
    }
