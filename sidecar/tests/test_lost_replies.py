"""A retried Fabric or OneLake write never makes a duplicate, and never fails without saying so.

Each test is the same story, on fakes only (no tenant): the server applied the write, then the reply was
lost (a timeout or a dropped connection). What must hold: the write isn't sent again by itself, the error
says it may or may not have happened, and doing it again makes no second item, run, file or commit.
"""

from __future__ import annotations

import json

import pytest
import requests

from fabric_desktop import app_settings, onelake
from fabric_desktop.fabric_api import FabricClient, FabricError, WriteOutcomeUnknown
from fabric_desktop.retrying import data_upload_dropped

WS, LH = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def answer(status: int, body: dict | None = None, headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code, r._content = status, json.dumps(body or {}).encode()
    r.headers.update(headers or {})
    return r


class FakeFabric:
    """Fabric's items and job runs. With ``lose``, the next write of that kind is applied and its reply lost."""

    def __init__(self):
        self.items: list[dict] = []
        self.runs: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.lose: set[str] = set()

    def request(self, method, url, headers=None, timeout=None, params=None, json=None, **_):  # noqa: A002
        self.sent.append((method, url))
        if method == "POST" and url.endswith("/items"):
            if any(i["displayName"] == json["displayName"] and i["type"] == json["type"] for i in self.items):
                return answer(400, {"errorCode": "ItemDisplayNameAlreadyInUse", "message": "Name in use.",
                                    "requestId": "req-1"})
            self.items.append({"id": f"item-{len(self.items) + 1}", **json})
            if "item" in self.lose:
                self.lose.discard("item")
                raise requests.ReadTimeout("read timed out")
            return answer(201, self.items[-1])
        if method == "POST" and url.endswith("/jobs/instances"):
            self.runs.append(f"run-{len(self.runs) + 1}")
            if "run" in self.lose:
                self.lose.discard("run")
                raise requests.ConnectionError("connection reset by peer")
            return answer(202, {}, {"Location": f"{url}/{self.runs[-1]}"})
        if method == "GET" and url.endswith("/items"):
            return answer(200, {"value": self.items})
        return answer(404, {"errorCode": "NotFound"})


@pytest.fixture
def fabric(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "allow_fabric_runs" else None)
    fake = FakeFabric()
    return fake, FabricClient(lambda: "tok", session=fake, sleep=lambda s: None)


def test_an_item_created_whose_reply_was_lost_is_never_made_twice(fabric):
    fake, client = fabric
    fake.lose.add("item")
    with pytest.raises(WriteOutcomeUnknown) as err:
        client.create_item(WS, "Notebook", "Load sales")
    assert "may or may not have done it" in str(err.value) and "check in Fabric" in str(err.value)
    assert isinstance(err.value, FabricError) and isinstance(err.value, requests.ConnectionError)
    assert err.value.response is None and err.value.request is None  # as any RequestException without an answer
    assert [m for m, _ in fake.sent] == ["POST"]  # not sent again by itself
    with pytest.raises(FabricError, match="ItemDisplayNameAlreadyInUse"):  # doing it again: refused by name
        client.create_item(WS, "Notebook", "Load sales")
    assert len(fake.items) == 1


def test_save_item_after_a_lost_reply_finds_the_item_it_made(fabric):
    fake, client = fabric
    fake.lose.add("item")
    with pytest.raises(WriteOutcomeUnknown):
        client.save_item(WS, "Notebook", "Load sales")
    assert client.save_item(WS, "Notebook", "Load sales")["id"] == "item-1"  # found, not made again
    assert len(fake.items) == 1


def test_a_run_started_whose_reply_was_lost_is_never_started_twice(fabric):
    """Pipeline and notebook runs started through the REST API: the run may be going, so it isn't resent."""
    fake, client = fabric
    fake.lose.add("run")
    with pytest.raises(WriteOutcomeUnknown, match="may or may not have done it"):
        client.run_item_job(WS, "pipe-1", "Pipeline")
    assert fake.runs == ["run-1"] and [m for m, _ in fake.sent] == ["POST"]


def test_a_write_that_got_a_5xx_says_it_may_have_happened(fabric):
    fake, client = fabric
    fake.request = lambda *a, **k: answer(503, {"message": "Busy", "requestId": "req-5"})  # type: ignore[method-assign]
    with pytest.raises(FabricError) as err:
        client.run_item_job(WS, "nb-1", "RunNotebook")
    text = str(err.value)
    assert "may or may not have done it" in text and text.endswith("Request id req-5.")
    assert "Try again" not in text


def test_a_read_whose_connection_drops_keeps_its_own_error(fabric):
    fake, client = fabric

    def dropped(*a, **k):
        raise requests.ConnectionError("connection reset by peer")
    fake.request = dropped  # type: ignore[method-assign]
    with pytest.raises(requests.ConnectionError) as err:
        client.list_workspaces()
    assert not isinstance(err.value, WriteOutcomeUnknown)


class FakeADLS:
    """OneLake's ADLS Gen2 files: create, append, flush, rename. ``lose_rename`` applies the next rename
    (the upload's last step) and then loses its reply."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.staged: dict[str, bytes] = {}
        self.lose_rename = False

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        path, params = url.split("/", 3)[3], params or {}
        if method == "PUT" and (headers or {}).get("x-ms-rename-source"):
            src = headers["x-ms-rename-source"].split("/", 3)[-1].lstrip("/")
            self.files[path] = self.files.pop(next(k for k in self.files if k.endswith(src) or src.endswith(k)))
            if self.lose_rename:
                self.lose_rename = False
                raise requests.ReadTimeout("read timed out")
            return answer(201)
        if method == "PUT" and params.get("resource") == "file":
            self.files[path] = b""
            return answer(201)
        if params.get("action") == "append":
            self.staged[path] = data
            return answer(202)
        if params.get("action") == "flush":
            self.files[path] = self.staged.pop(path)
            return answer(200)
        if method == "DELETE":
            return answer(200) if self.files.pop(path, None) is not None else answer(404)
        return answer(404)


def test_a_file_upload_whose_rename_reply_was_lost_leaves_one_file_and_says_so():
    """Upload is a new file renamed over the old one: doing it again replaces it again, never a second copy."""
    fake = FakeADLS()
    fs = onelake.FileStore(lambda: "tok", session=fake, sleep=lambda s: None)
    fs.upload(WS, LH, "Files/config.json", b'{"v": 1}')
    fake.lose_rename = True
    with pytest.raises(requests.RequestException):  # reported, not swallowed
        fs.upload(WS, LH, "Files/config.json", b'{"v": 2}')
    fs.upload(WS, LH, "Files/config.json", b'{"v": 2}')  # doing it again
    assert list(fake.files.values()) == [b'{"v": 2}']  # one file, no leftover upload beside it
    assert all(k.endswith("Files/config.json") for k in fake.files)


@pytest.mark.parametrize("error, again", [
    ("Generic S3 error: Error performing PUT https://onelake/t/part-0001.parquet in 30s - timed out", True),
    (("Generic S3 error: Error performing PUT https://onelake/t/_delta_log/00000000000000000003.json "
     "in 30s - timed out"), False),
    (("Generic S3 error: Error performing PUT https://onelake/t/_delta_log/00000000000000000003.json "
     "- status code: 503"), False),
])
def test_a_delta_commit_whose_reply_was_lost_is_never_committed_again(error, again):
    """A dropped data file is sent again (nothing is in the table until the log is written); a dropped
    write of the log itself may have committed, so the table write fails and says so instead."""
    assert data_upload_dropped(RuntimeError(error)) is again


def test_a_metadata_run_whose_commit_connection_dropped_says_to_check_history(tmp_path):
    """A metadata run's write whose Delta log entry was cut off: the run fails in plain words naming the
    table, keeps object_store's text as a step for support, and isn't marked for the agent to retry."""
    from test_large_tables import DAY1, Setup, rows

    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    dropped = OSError("Generic MicrosoftAzure error: Error performing PUT https://onelake.blob.fabric.microsoft.com/"
                      "ws/lh/Tables/bronze/events/_delta_log/00000000000000000001.json in 30s - error sending request")

    def fail(*a, **k):
        raise dropped
    for n in ("write", "write_stream", "merge_insert", "upsert"):
        setattr(s.store, n, fail)
    res = s.engine(DAY1).run("bronze.events")
    assert res.status == "failed" and res.retryable is False
    assert res.message.startswith("The connection to OneLake dropped while the commit to bronze.events was being")
    assert "may or may not have gone in" in res.message and "history" in res.message
    assert any(st.name == "error" and "Error performing PUT" in st.detail for st in res.steps)


class DroppingADLS(FakeADLS):
    """FakeADLS whose appends drop the connection ``drops`` times (Wi-Fi gone, a VPN reconnecting)."""

    def __init__(self, drops: int):
        super().__init__()
        self.drops, self.appends = drops, 0

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        if (params or {}).get("action") == "append":
            self.appends += 1
            if self.drops:
                self.drops -= 1
                raise requests.ConnectionError("('Connection aborted.', ConnectionResetError(10054, 'An existing "
                                               "connection was forcibly closed by the remote host'))")
        return super().request(method, url, params=params, data=data, headers=headers, timeout=timeout)


def test_a_file_upload_whose_connection_drops_for_a_moment_is_finished():
    """The new file beside the target is written again at the same position: nothing twice, and the upload
    goes through once the line is back, with growing waits."""
    fake, slept = DroppingADLS(drops=2), []
    fs = onelake.FileStore(lambda: "tok", session=fake, sleep=slept.append)
    fs.upload(WS, LH, "Files/config.json", b'{"v": 2}')
    assert list(fake.files.values()) == [b'{"v": 2}'] and all(k.endswith("Files/config.json") for k in fake.files)
    assert fake.appends == 3 and slept == [2.0, 4.0]


def test_a_file_upload_whose_connection_stays_down_keeps_the_old_file_and_says_so_plainly():
    fake = DroppingADLS(drops=0)
    fs = onelake.FileStore(lambda: "tok", session=fake, sleep=lambda s: None)
    fs.upload(WS, LH, "Files/config.json", b'{"v": 1}')
    fake.drops = 99
    with pytest.raises(onelake.UploadDropped) as err:
        fs.upload(WS, LH, "Files/config.json", b'{"v": 2}')
    assert str(err.value) == (
        "Uploading Files/config.json to OneLake stopped because the connection dropped (Wi-Fi, a VPN or a proxy), "
        "even after trying again. Files/config.json wasn't changed: it still has its old content, or isn't there "
        "if it's new. Upload it again once you're connected.")
    assert isinstance(err.value, requests.ConnectionError)  # callers that catch requests' errors still do
    assert fake.appends == 1 + onelake.UPLOAD_STEP_ATTEMPTS
    assert list(fake.files.values()) == [b'{"v": 1}']  # the old file, and no upload left beside it


def test_a_file_upload_whose_rename_reply_was_lost_says_the_file_may_be_either():
    fake = FakeADLS()
    fs = onelake.FileStore(lambda: "tok", session=fake, sleep=lambda s: None)
    fake.lose_rename = True
    with pytest.raises(onelake.UploadDropped, match="may have its old content or its new one. Upload it again"):
        fs.upload(WS, LH, "Files/config.json", b'{"v": 2}')
