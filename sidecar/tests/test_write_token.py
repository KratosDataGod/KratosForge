"""A table write that outlasts its OneLake token (HD-034, DI-6). The write lease is renewed with a
current token, a write OneLake refuses partway through is sent again with a fresh token when nothing
was committed, and a refused commit that may have gone in is never sent again. Fakes only: no tenant."""

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, publish
from fabric_desktop.publish import WriteTokenExpired
from fabric_desktop.retrying import data_upload_dropped, token_refused_on_log, write_token_refused

PART = "https://onelake.blob.fabric.microsoft.com/ws/lh/Tables/dbo/t/part-00000-x.zstd.parquet?comp=block"
LOG = "https://onelake.blob.fabric.microsoft.com/ws/lh/Tables/dbo/t/_delta_log/00000000000000000001.json"


def refused(url, answer="Server returned non-2xx status code: 401 Unauthorized: "
                        "<Code>InvalidAuthenticationInfo</Code> Lifetime validation failed. The token is expired."):
    return OSError(f"Generic MicrosoftAzure error: Error performing PUT {url} in 0.2s - {answer}")


class Ref:
    def __init__(self, uri):
        self.uri, self.schema, self.table = uri, "dbo", "t"
        self.workspace_id, self.lakehouse_id = "ws", "lh"


def rows(*ids):
    return pa.table({"id": list(ids)})


# ---- what counts as a refused token


@pytest.mark.parametrize("error, token, on_log", [
    (refused(PART), True, False),
    (refused(LOG), True, True),
    (refused(PART, "Server returned non-2xx status code: 401 Unauthorized"), True, False),
    (refused(PART, "Server returned non-2xx status code: 403 Forbidden: InvalidAuthenticationInfo"), True, False),
    (refused(PART, "Server returned non-2xx status code: 403 Forbidden"), False, False),  # no permission: not a token
    (refused(PART, "Server returned non-2xx status code: 503"), False, False),
    (ValueError("Schema error: the token is expired"), False, False),  # not a request OneLake answered
])
def test_a_refused_token_is_told_from_other_write_errors(error, token, on_log):
    assert write_token_refused(error) is token
    assert token_refused_on_log(error) is on_log
    if token:
        assert not data_upload_dropped(error)  # the dropped-upload retry never takes these


# ---- the lease is renewed with a current token


def test_the_lease_asks_for_a_token_each_time(monkeypatch):
    seen = {}

    def lease(ws, lh, schema, table, token, owner):
        seen["token"] = token
        return None
    monkeypatch.setattr(publish, "onelake_table_lease", lease)
    tokens = iter(["first", "second"])
    publish._lease(Ref("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/dbo/t"), lambda: next(tokens))
    assert seen["token"]() == "first" and seen["token"]() == "second"  # not one token kept for the write


def test_publish_takes_a_token_function(tmp_path):
    asked = []

    def token():
        asked.append(1)
        return "tok"
    table = publish.publish(rows(1, 2), Ref(str(tmp_path / "t")), "overwrite", token)
    assert table.version() == 0 and asked
    assert publish.publish(rows(3), Ref(str(tmp_path / "t")), "append", "tok").version() == 1  # a plain token still works


# ---- a write OneLake refuses partway through


def test_a_refused_data_file_raises_write_token_expired(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise refused(PART)
    monkeypatch.setattr(publish, "write_deltalake", refuse)
    with pytest.raises(WriteTokenExpired, match="Nothing was committed"):
        publish.publish(rows(1), Ref(str(tmp_path / "t")), "overwrite", "tok")


def test_a_refused_commit_that_didnt_go_in_can_be_sent_again(tmp_path, monkeypatch):
    ref = Ref(str(tmp_path / "t"))
    publish.publish(rows(1), ref, "overwrite", "tok")

    def refuse(*a, **k):
        raise refused(LOG)
    monkeypatch.setattr(publish, "write_deltalake", refuse)
    with pytest.raises(WriteTokenExpired):  # still at version 0: the commit wasn't written
        publish.publish(rows(2), ref, "append", "tok")
    assert DeltaTable(ref.uri).version() == 0


def test_a_refused_commit_on_a_new_table_that_didnt_go_in_can_be_sent_again(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise refused(LOG)
    monkeypatch.setattr(publish, "write_deltalake", refuse)
    with pytest.raises(WriteTokenExpired):  # still no table
        publish.publish(rows(1), Ref(str(tmp_path / "t")), "overwrite", "tok")


def test_a_refused_commit_that_may_have_gone_in_is_never_called_retryable(tmp_path, monkeypatch):
    ref = Ref(str(tmp_path / "t"))
    publish.publish(rows(1), ref, "overwrite", "tok")

    def commit_then_refuse(target, data, **kw):
        write_deltalake(ref.uri, data, mode="append")  # the table moved on: it may be this write's commit
        raise refused(LOG)
    monkeypatch.setattr(publish, "write_deltalake", commit_then_refuse)
    with pytest.raises(RuntimeError, match="may or may not have gone in") as caught:
        publish.publish(rows(2), ref, "append", "tok")
    assert not isinstance(caught.value, WriteTokenExpired)


def test_a_refused_commit_on_an_unreadable_table_is_never_called_retryable(tmp_path, monkeypatch):
    ref = Ref(str(tmp_path / "t"))
    publish.publish(rows(1), ref, "overwrite", "tok")
    real = publish.DeltaTable

    def refuse(*a, **k):
        def unreadable(*a, **k):
            raise OSError("Error performing GET: status code: 401 Unauthorized")
        monkeypatch.setattr(publish, "DeltaTable", unreadable)
        raise refused(LOG)
    monkeypatch.setattr(publish, "write_deltalake", refuse)
    with pytest.raises(RuntimeError, match="may or may not have gone in") as caught:
        publish.publish(rows(2), ref, "append", "tok")
    assert not isinstance(caught.value, WriteTokenExpired)
    monkeypatch.setattr(publish, "DeltaTable", real)


# ---- notebook writes: sent again with a fresh token


LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "LH_Gold"}
WRITE = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
         "lakehouse.write_table(pa.table({'id': list(range(10))}), 'sales')\n")


@pytest.fixture
def service(tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    from fabric_desktop.service import Service
    monkeypatch.setattr(lakehouse, "UPLOAD_RETRY_WAIT", 0.0)
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "settings.json")
    svc = Service(client_id="x", root=tmp_path)
    tokens = iter(f"tok{n}" for n in range(100))
    svc._auth = type("Auth", (), {"storage_token": lambda self: next(tokens)})()
    svc._schema_cache = {LH["lakehouse_id"]: True}
    svc.onelake = tmp_path / "onelake"
    return svc


def _expires_once(service, monkeypatch, error):
    real, tries = lakehouse.publish, []

    def expires_once(data, ref, mode, token, **kw):
        tries.append(token())  # each try asks for a token of its own
        if len(tries) == 1:
            raise error
        return real(data, Ref(str(service.onelake / ref.table)), mode, token, **kw)
    monkeypatch.setattr(lakehouse, "publish", expires_once)
    return tries


def test_a_notebook_write_whose_token_expired_is_sent_again_with_a_fresh_one(service, monkeypatch):
    tries = _expires_once(service, monkeypatch, WriteTokenExpired("Writing dbo.sales: refused. Nothing was committed."))
    out = service.run_cell(WRITE, LH)
    assert out["status"] == "ok", out
    assert len(tries) == 2 and tries[0] != tries[1]
    table = DeltaTable(str(service.onelake / "sales"))
    assert table.version() == 0 and sorted(table.to_pyarrow_table().column("id").to_pylist()) == list(range(10))


def test_a_notebook_write_whose_commit_may_have_gone_in_is_not_sent_again(service, monkeypatch):
    tries = _expires_once(service, monkeypatch, RuntimeError("Writing dbo.sales: may or may not have gone in."))
    out = service.run_cell(WRITE, LH)
    assert out["status"] == "error" and len(tries) == 1
    assert "may or may not have gone in" in json.dumps(out)
