"""UDF_Platform calls are sent again only where that's safe, and a change is made once (#1040).

Reads (and Finish or repair, and issuing keys) are sent again after a 429, a busy platform or a lost
connection. Any other change goes through call_once with a call id made once per change: the
platform makes it once, however often the app sends it. An older UDF_Platform without call_once gets
the change itself, sent again only after a 429. UDF_Platform is faked by a session that runs each
call on a SQLite platform, which can lose its answer after doing the work; no tenant is called."""

import datetime as dt
import json

import pytest
import requests

from fabric_desktop.central import core, policy  # noqa: F401  (policy adds its operations)
from fabric_desktop.central.client import FabricPlatform, LocalPlatform

ADMIN, DEV = "ana@contoso.com", "dev@contoso.com"


def response(status, body=None, headers=None):
    resp = requests.Response()
    resp.status_code, resp.headers, resp.encoding = status, requests.structures.CaseInsensitiveDict(headers or {}), "utf-8"
    resp._content = json.dumps(body).encode() if body is not None else b""
    resp._content_consumed = True
    return resp


class Fabric:
    """UDF_Platform over a SQLite platform. ``script`` holds what happens to the next calls, in turn:
    "ok", "429", "503", "timeout" (lost before the platform sees it) or "lost" (done, answer lost).
    ``once`` False is a UDF_Platform from before call_once."""

    def __init__(self, path, who, once=True):
        self.platform, self.who, self.once = LocalPlatform(path, lambda: who["user"]), who, once
        self.script, self.sent = [], []

    def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002 - requests' own name
        op = url.rsplit("/functions/", 1)[1].split("/")[0]
        self.sent.append((op, json))
        step = self.script.pop(0) if self.script else "ok"
        if op == "call_once" and not self.once:
            return response(404, {"errorCode": "FunctionNotFound"})
        if step == "429":
            return response(429, {"errors": [{"message": "Too many requests"}]}, {"Retry-After": "7"})
        if step == "503":
            return response(503, {"errors": [{"message": "Service Unavailable"}]})
        if step == "timeout":
            raise requests.Timeout("read timed out")
        try:
            out = self.platform.call(op, **(json or {}))
        except core.PlatformError as exc:
            return response(400, {"status": "Failed", "errors": [{"message": str(exc)}]})
        if step == "lost":
            raise requests.ConnectionError("connection reset after the platform answered")
        return response(200, {"status": "Succeeded", "output": out})


@pytest.fixture
def who():
    return {"user": ADMIN}


@pytest.fixture
def fabric(tmp_path, who):
    return Fabric(tmp_path / "platform.sqlite", who)


@pytest.fixture
def waits():
    return []


@pytest.fixture
def client(fabric, waits):
    return FabricPlatform(lambda: "tok", "ws", "fn", session=fabric, sleep=waits.append)


def rows(fabric, table):
    with fabric.platform._db() as db:
        return db.rows(f"SELECT * FROM {db.t('catalog', table)}")


def audit(fabric, action):
    return [e for e in rows(fabric, "audit") if e["action"] == action]


# ----------------------------------------------------------------------------- reads


@pytest.mark.parametrize("trouble", ["429", "503", "timeout", "lost"])
def test_a_read_is_sent_again_after_trouble_and_answers(fabric, client, waits, trouble):
    fabric.script = [trouble]
    assert [d["name"] for d in client.call("list_domains")] == []
    assert [op for op, _ in fabric.sent] == ["list_domains", "list_domains"]
    assert waits == ([7.0] if trouble == "429" else [2.0])  # Retry-After when the platform gives one


def test_a_read_that_keeps_failing_says_the_platform_could_not_be_reached(fabric, client):
    fabric.script = ["503"] * 3
    with pytest.raises(requests.HTTPError, match="couldn't be reached to list domains"):
        client.call("list_domains")
    assert len(fabric.sent) == 3


# ----------------------------------------------------------------------------- changes


def test_a_change_goes_through_call_once_and_answers_as_the_operation_does(fabric, client):
    out = client.call("save_domain", domain={"name": "Sales"})
    assert out["id"] == "sales" and out["updated_by"] == ADMIN
    [(op, body)] = fabric.sent
    assert op == "call_once" and body["operation"] == "save_domain" and body["args"] == {"domain": {"name": "Sales"}}
    assert len(body["call_id"]) == 32 and "by" not in body["args"]


def test_a_change_refused_unread_with_429_is_sent_again_with_the_same_call_id(fabric, client, waits):
    fabric.script = ["429"]
    client.call("save_domain", domain={"name": "Sales"})
    assert [b["call_id"] for _, b in fabric.sent] == [fabric.sent[0][1]["call_id"]] * 2
    assert waits == [7.0] and len(audit(fabric, "save domain")) == 1


@pytest.mark.parametrize("trouble", ["lost", "timeout", "503"])
def test_a_change_whose_answer_was_lost_is_made_once(fabric, client, trouble):
    """"lost": the platform made it and the answer never came; "timeout" and "503": it never got there."""
    fabric.script = [trouble]
    out = client.call("save_domain", domain={"name": "Sales"})
    assert out["id"] == "sales"
    assert len({b["call_id"] for _, b in fabric.sent}) == 1 and len(fabric.sent) == 2
    assert [d["id"] for d in rows(fabric, "domain")] == ["sales"]
    assert len(audit(fabric, "save domain")) == 1


def test_an_approval_whose_answer_was_lost_is_approved_once(fabric, client, who):
    who["user"] = DEV
    req = fabric.platform.call("request", kind="key_table", target="supplier", reason="Suppliers")
    who["user"] = ADMIN
    fabric.script = ["lost"]
    out = client.call("decide_request", id=req["id"], approve=True)
    assert out["status"] == "approved"
    assert [r["status"] for r in rows(fabric, "request")] == ["approved"]
    assert len(audit(fabric, "approved key_table")) == 1
    assert [t["name"] for t in rows(fabric, "key_table")] == ["supplier"]


def test_a_refused_change_is_not_sent_again_and_frees_its_call_id(fabric, client, who):
    who["user"] = DEV
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        client.call("save_domain", domain={"name": "Sales"})
    assert len(fabric.sent) == 1 and rows(fabric, "call") == []


def test_finish_or_repair_and_issuing_keys_are_sent_again_as_they_are(fabric, client):
    fabric.platform.call("ensure_key_table", name="customer")
    fabric.script = ["lost"]
    first = client.call("issue_keys", name="customer", business_keys=["a", "b"])
    assert [op for op, _ in fabric.sent] == ["issue_keys", "issue_keys"]
    assert first["keys"] == {"a": 1, "b": 2}  # the same keys the lost answer had, none issued twice


# ----------------------------------------------------------------------------- an older UDF_Platform


def test_an_older_platform_gets_the_change_itself_and_a_lost_answer_is_not_sent_again(tmp_path, who, waits):
    fabric = Fabric(tmp_path / "platform.sqlite", who, once=False)
    client = FabricPlatform(lambda: "tok", "ws", "fn", session=fabric, sleep=waits.append)
    assert client.call("save_domain", domain={"name": "Sales"})["id"] == "sales"
    assert [op for op, _ in fabric.sent] == ["call_once", "save_domain"]
    fabric.sent.clear()
    fabric.script = ["lost"]
    with pytest.raises(requests.ConnectionError) as err:
        client.call("save_domain", domain={"name": "HR"})
    assert [op for op, _ in fabric.sent] == ["save_domain"]  # it knows by now: no call_once, sent once
    assert "may or may not have been made" in str(err.value) and "Finish or repair" in str(err.value)
    assert len(audit(fabric, "save domain")) == 2  # Sales, and HR made once although its answer was lost


def test_an_older_platform_still_gets_a_change_sent_again_after_429(tmp_path, who, waits):
    fabric = Fabric(tmp_path / "platform.sqlite", who, once=False)
    client = FabricPlatform(lambda: "tok", "ws", "fn", session=fabric, sleep=waits.append)
    fabric.script = ["ok", "429"]  # call_once's 404, then the change refused unread
    client.call("save_domain", domain={"name": "Sales"})
    assert [op for op, _ in fabric.sent] == ["call_once", "save_domain", "save_domain"]
    assert len(audit(fabric, "save domain")) == 1


# ----------------------------------------------------------------------------- call_once itself


def platform_db(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    return p


CID = "0123456789abcdef0123456789abcdef"


def test_call_once_answers_a_second_call_with_the_first_answer(tmp_path):
    p = platform_db(tmp_path)
    first = p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    again = p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    assert first["replayed"] is False and again == {"result": first["result"], "replayed": True}


@pytest.mark.parametrize("call_id,op,args,message", [
    ("nope", "save_domain", {}, "32 hexadecimal"),
    (CID, "list_domains", {}, "isn't a change made through call_once"),
    (CID, "issue_keys", {}, "isn't a change made through call_once"),
    (CID, "call_once", {}, "isn't a change made through call_once"),
    (CID, "no_such_op", {}, "isn't a change made through call_once"),
    (CID, "save_domain", {"domain": {"name": "Sales"}, "by": "someone@else"}, "never an argument"),
])
def test_call_once_refuses_what_it_does_not_run(tmp_path, call_id, op, args, message):
    p = platform_db(tmp_path)
    with pytest.raises(core.PlatformError, match=message):
        p.call("call_once", call_id=call_id, operation=op, args=args)
    assert rows_of(p, "call") == [] and rows_of(p, "domain") == []


def rows_of(p, table):
    with p._db() as db:
        return db.rows(f"SELECT * FROM {db.t('catalog', table)}")


def test_a_call_id_answers_only_its_own_change_and_caller(tmp_path):
    who = {"user": ADMIN}
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: who["user"])
    p.call("add_admin", user=DEV)
    p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    with pytest.raises(core.PlatformError, match="belongs to another change"):
        p.call("call_once", call_id=CID, operation="delete_domain", args={"did": "sales"})
    who["user"] = DEV
    with pytest.raises(core.PlatformError, match="belongs to another change"):
        p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})


def test_call_ids_are_kept_seven_days(tmp_path):
    p = platform_db(tmp_path)
    p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=core.CALL_DAYS, minutes=1)).isoformat(timespec="seconds")
    with p._db() as db:
        db.run(f"UPDATE {db.t('catalog', 'call')} SET at = ?", (old,))
        db.commit()
    p.call("call_once", call_id="f" * 32, operation="save_domain", args={"domain": {"name": "HR"}})
    assert [r["id"] for r in rows_of(p, "call")] == ["f" * 32]


def test_an_answer_too_big_to_keep_says_the_change_was_made(tmp_path, monkeypatch):
    p = platform_db(tmp_path)
    monkeypatch.setattr(core, "MAX_CALL_RESULT", 10)
    p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    with pytest.raises(core.PlatformError, match="was made, but its answer wasn't kept. Refresh"):
        p.call("call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    assert len([r for r in rows_of(p, "audit") if r["action"] == "save domain"]) == 1


def test_a_change_that_commits_and_then_fails_frees_its_call_id(tmp_path, monkeypatch):
    """Many operations commit their own work; one that fails after that must still free its id, or the
    retry would be told the change was made with its answer lost, instead of running again."""
    p = platform_db(tmp_path)
    runs = []

    def commits_then_fails(db, name, by=""):
        db.run(f"INSERT INTO {db.t('catalog', 'domain')} (id, name) VALUES (?, ?)", (name.lower(), name))
        db.commit()
        runs.append(name)
        if len(runs) == 1:
            raise core.PlatformError("failed after committing")
        return {"ok": name}

    monkeypatch.setitem(core.OPERATIONS, "commits_then_fails", commits_then_fails)
    with pytest.raises(core.PlatformError, match="failed after committing"):
        p.call("call_once", call_id=CID, operation="commits_then_fails", args={"name": "Ops"})
    assert rows_of(p, "call") == []
    with p._db() as db:
        db.run(f"DELETE FROM {db.t('catalog', 'domain')}")
        db.commit()
    assert p.call("call_once", call_id=CID, operation="commits_then_fails", args={"name": "Ops"}) == {
        "result": {"ok": "Ops"}, "replayed": False}
    assert runs == ["Ops", "Ops"]


def test_every_operation_is_a_read_a_repeatable_one_or_a_change_call_once_runs():
    reads, again = core.READ_OPERATIONS, core.REPEATABLE_OPERATIONS
    assert reads <= set(core.OPERATIONS) and again <= set(core.OPERATIONS) and not reads & again
    changes = set(core.OPERATIONS) - reads - again - {"call_once"}
    assert {"save_domain", "decide_request", "register_product", "create_policy"} <= changes


def test_a_second_call_while_the_first_is_still_running_gets_the_first_answer(tmp_path, monkeypatch):
    """The app timed out and sent the change again while the platform was still making it."""
    import threading
    import time

    p = platform_db(tmp_path)
    save, started = core.OPERATIONS["save_domain"], threading.Event()

    def slow(db, domain, by=""):
        out = save(db, domain, by=by)  # made and committed; its answer isn't written yet
        started.set()
        time.sleep(1)
        return out

    monkeypatch.setitem(core.OPERATIONS, "save_domain", slow)
    answers = {}
    first = threading.Thread(target=lambda: answers.setdefault("first", p.call(
        "call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})))
    first.start()
    assert started.wait(10)
    answers["second"] = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN).call(
        "call_once", call_id=CID, operation="save_domain", args={"domain": {"name": "Sales"}})
    first.join(10)
    assert answers["second"] == {"result": answers["first"]["result"], "replayed": True}
    assert len([r for r in rows_of(p, "audit") if r["action"] == "save domain"]) == 1
