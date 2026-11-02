"""Contract tests for OneLake file writes and deletes (HD-053, finding TS-1).

They pin what ``onelake.FileStore`` sends to the OneLake DFS (ADLS Gen2) API, request by request,
through a fake transport under a real ``requests.Session`` (tests/fake_http.py). Nothing reaches the
network or a tenant. The contracts:

* upload is create (PUT ?resource=file), append (PATCH ?action=append&position=0) then flush
  (PATCH ?action=flush&position=<length>), with the bearer token and x-ms-version on each request;
  it replaces an existing file (no If-None-Match), and a failed step stops before the file is
  committed (no flush after a failed append, nothing after a failed create);
* delete sends one DELETE with recursive=true by default (false on request), treats 404 as done and
  never repeats itself on an error other than throttling (429, where OneLake did nothing);
* upload waits out a 429 on any step and sends anything else once; mkdir (If-None-Match: *) is
  tried again after throttling, a 5xx or a dropped connection;
* the read-only and dry-run paths (probe, list, the live test's plan and --dry-run, the readiness
  write test refused for a production workspace) send no mutating request;
* every request stays under https://onelake.dfs.fabric.microsoft.com/<workspace>/<lakehouse>/.
  A path with a ``..`` segment and an empty path to upload or delete are refused (onelake.item_path):
  requests resolves ``..`` before sending, and an empty delete path would remove the lakehouse's root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from fabric_desktop import readiness
from fabric_desktop.onelake import DFS_HOST, FileStore, TableCatalog
from fake_http import MUTATING, Reply, session

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"
ITEM = f"https://{DFS_HOST}/{WS}/{LH}"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A request that bypasses the fake transport fails instead of going out."""
    def refuse(self, request, *args, **kwargs):
        raise AssertionError(f"real network call attempted: {request.method} {request.url}")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)


def store(replies):
    s, transport = session(replies)
    return FileStore(lambda: "tok", session=s), transport


# ---- upload: create, append, flush


def test_upload_is_create_append_flush_with_exact_urls_methods_and_headers():
    fs, t = store([Reply(201), Reply(202), Reply(200)])
    fs.upload(WS, LH, "Files/in/data.csv", b"a,b\n1,2\n")

    assert [(r.method, r.base, r.query) for r in t.sent] == [
        ("PUT", f"{ITEM}/Files/in/data.csv", {"resource": "file"}),
        ("PATCH", f"{ITEM}/Files/in/data.csv", {"action": "append", "position": "0"}),
        ("PATCH", f"{ITEM}/Files/in/data.csv", {"action": "flush", "position": "8"}),
    ]
    for r in t.sent:
        assert r.headers["Authorization"] == "Bearer tok"
        assert r.headers["x-ms-version"] == "2023-11-03"
    assert t.sent[0].body is None and t.sent[2].body is None
    assert t.sent[1].body == b"a,b\n1,2\n"


def test_upload_replaces_an_existing_file_it_is_not_create_only():
    """FileStore offers create-or-replace only: the create carries no If-None-Match (or overwrite
    flag), so OneLake truncates an existing file. A create-only caller must check first."""
    fs, t = store([Reply(201), Reply(202), Reply(200)])
    fs.upload(WS, LH, "Files/x.txt", b"new")
    create = t.sent[0]
    assert create.method == "PUT"
    assert not {"if-none-match", "if-match"} & {h.lower() for h in create.headers}
    assert "overwrite" not in create.query


def test_upload_of_an_empty_file_skips_the_append():
    fs, t = store([Reply(201), Reply(200)])
    fs.upload(WS, LH, "Files/empty.txt", b"")
    assert [(r.method, r.query.get("action"), r.query.get("position")) for r in t.sent] == [
        ("PUT", None, None), ("PATCH", "flush", "0")]


def test_a_failed_create_sends_nothing_more():
    fs, t = store([Reply(403, {"error": {"code": "Forbidden"}})])
    with pytest.raises(requests.HTTPError):
        fs.upload(WS, LH, "Files/x.txt", b"data")
    assert [r.method for r in t.sent] == ["PUT"]


def test_a_failed_append_is_not_flushed_or_repeated():
    """Without the flush OneLake never commits the bytes, so a reader can't see half a file."""
    fs, t = store([Reply(201), Reply(500)])
    with pytest.raises(requests.HTTPError):
        fs.upload(WS, LH, "Files/x.txt", b"data")
    assert [(r.method, r.query.get("action")) for r in t.sent] == [("PUT", None), ("PATCH", "append")]


def test_a_failed_flush_is_not_repeated():
    fs, t = store([Reply(201), Reply(202), Reply(503)])
    with pytest.raises(requests.HTTPError):
        fs.upload(WS, LH, "Files/x.txt", b"data")
    assert len(t.sent) == 3 and t.sent[-1].query["action"] == "flush"


# ---- delete: file and folder, recursive flag, 404


def test_delete_a_folder_is_one_recursive_delete_by_default():
    fs, t = store([Reply(200)])
    fs.delete(WS, LH, "Files/_fabric_desktop_check")
    [r] = t.sent
    assert (r.method, r.base, r.query) == ("DELETE", f"{ITEM}/Files/_fabric_desktop_check", {"recursive": "true"})
    assert r.headers["Authorization"] == "Bearer tok" and r.headers["x-ms-version"] == "2023-11-03"
    assert r.body is None


def test_delete_without_recursive_says_so_and_a_non_empty_folder_is_refused():
    fs, t = store([Reply(409, {"error": {"code": "DirectoryNotEmpty"}})])
    with pytest.raises(requests.HTTPError):
        fs.delete(WS, LH, "Files/folder", recursive=False)
    [r] = t.sent
    assert r.query == {"recursive": "false"}


def test_delete_a_file():
    fs, t = store([Reply(200)])
    fs.delete(WS, LH, "Files/timings/run-1.json", recursive=False)
    [r] = t.sent
    assert (r.method, r.base, r.query) == ("DELETE", f"{ITEM}/Files/timings/run-1.json", {"recursive": "false"})


def test_deleting_something_already_gone_is_fine():
    fs, t = store([Reply(404, {"error": {"code": "PathNotFound"}})])
    fs.delete(WS, LH, "Files/gone")
    assert len(t.sent) == 1


@pytest.mark.parametrize("status", [500, 503])
def test_a_failed_delete_raises_and_is_not_repeated_by_filestore(status):
    fs, t = store([Reply(status)])
    with pytest.raises(requests.HTTPError):
        fs.delete(WS, LH, "Files/folder")
    assert len(t.sent) == 1


# ---- throttling (#1287): a 429 is waited out, since OneLake did nothing


def waiting_store(replies):
    s, transport = session(replies)
    waits = []
    return FileStore(lambda: "tok", session=s, sleep=waits.append), transport, waits


def test_an_upload_whose_append_is_throttled_writes_once():
    fs, t, waits = waiting_store([Reply(201), Reply(429, headers={"Retry-After": "2"}), Reply(202), Reply(200)])
    fs.upload(WS, LH, "Files/x.txt", b"data")
    assert [(r.method, r.query.get("action")) for r in t.sent] == [
        ("PUT", None), ("PATCH", "append"), ("PATCH", "append"), ("PATCH", "flush")]
    assert waits == [2]


def test_a_throttled_delete_is_sent_again_after_the_wait():
    fs, t, waits = waiting_store([Reply(429, headers={"Retry-After": "1"}), Reply(200)])
    fs.delete(WS, LH, "Files/folder")
    assert [r.method for r in t.sent] == ["DELETE", "DELETE"] and waits == [1]


@pytest.mark.parametrize("first", [Reply(503), Reply(429)])
def test_a_folder_create_is_tried_again_after_a_busy_answer(first):
    fs, t, _ = waiting_store([first, Reply(201)])
    fs.mkdir(WS, LH, "Files/in")
    assert [r.method for r in t.sent] == ["PUT", "PUT"]
    assert all(r.headers["If-None-Match"] == "*" for r in t.sent)


def test_a_folder_that_is_there_is_still_fine():
    fs, t, _ = waiting_store([Reply(409, {"error": {"code": "PathAlreadyExists"}})])
    fs.mkdir(WS, LH, "Files/in")
    assert len(t.sent) == 1


def test_a_refused_write_says_what_and_why_without_the_url():
    fs, _, _ = waiting_store([Reply(201), Reply(202), Reply(503, {"error": {"code": "ServerBusy", "message": "x"}})])
    with pytest.raises(requests.HTTPError) as err:
        fs.upload(WS, LH, "Files/in/data.csv", b"data")
    assert str(err.value) == "OneLake didn't commit Files/in/data.csv (503: ServerBusy)."
    assert err.value.response.status_code == 503 and "https://" not in str(err.value)

    fs, _, _ = waiting_store([Reply(403, headers={"x-ms-error-code": "AuthorizationPermissionMismatch"})])
    with pytest.raises(requests.HTTPError, match=r"^OneLake didn't delete Files/a \(403: AuthorizationPermissionMismatch\)\.$"):
        fs.delete(WS, LH, "Files/a")

    fs, t, _ = waiting_store([Reply(500)] * 5)  # a folder create is tried 5 times before it gives up
    with pytest.raises(requests.HTTPError, match=r"^OneLake didn't create the folder Files/b \(500\)\.$"):
        fs.mkdir(WS, LH, "Files/b")
    assert len(t.sent) == 5


# ---- read-only and dry-run paths send no mutating request


def test_probe_reads_at_most_one_entry_and_writes_nothing():
    fs, t = store([Reply(200, {"paths": []})])
    assert fs.probe(WS, LH, "Files/shortcut") == (200, "")
    [r] = t.sent
    assert (r.method, r.base) == ("GET", f"https://{DFS_HOST}/{WS}")
    assert r.query == {"resource": "filesystem", "recursive": "false", "maxResults": "1",
                       "directory": f"{LH}/Files/shortcut"}


def test_probe_waits_out_throttling_and_a_busy_onelake():
    s, t = session([Reply(429, headers={"Retry-After": "1"}), Reply(503), Reply(200, {"paths": []})])
    waits = []
    fs = FileStore(lambda: "tok", session=s, sleep=waits.append)
    assert fs.probe(WS, LH, "Files/shortcut") == (200, "")
    assert [r.method for r in t.sent] == ["GET"] * 3 and waits[0] == 1


def test_probe_gives_its_last_answer_after_a_few_tries():
    s, t = session([Reply(503, {"error": {"code": "ServerBusy", "message": "busy"}})] * 5)
    fs = FileStore(lambda: "tok", session=s, sleep=lambda w: None)
    assert fs.probe(WS, LH, "Files/shortcut") == (503, "ServerBusy busy")
    assert len(t.sent) == 3  # onelake.PROBE_ATTEMPTS


def test_probe_doesnt_repeat_a_404():
    fs, t = store([Reply(404, {"error": {"code": "PathNotFound", "message": "gone"}})])
    assert fs.probe(WS, LH, "Files/shortcut") == (404, "PathNotFound gone")
    assert len(t.sent) == 1


def test_list_follows_the_continuation_header_and_only_reads():
    fs, t = store([
        Reply(200, {"paths": [{"name": f"{LH}/Files/b.csv", "contentLength": "3"}]},
              headers={"x-ms-continuation": "page2"}),
        Reply(200, {"paths": [{"name": f"{LH}/Files/a", "isDirectory": "true"}]}),
    ])
    entries = fs.list(WS, LH, "Files", recursive=True)
    assert [e["path"] for e in entries] == ["Files/a", "Files/b.csv"]
    assert [r.method for r in t.sent] == ["GET", "GET"]
    assert "continuation" not in t.sent[0].query
    assert t.sent[1].query["continuation"] == "page2"
    assert t.sent[1].query["directory"] == f"{LH}/Files"


def test_list_with_max_results_reads_one_page_only():
    fs, t = store([Reply(200, {"paths": []}, headers={"x-ms-continuation": "more"})])
    fs.list(WS, LH, "Files", max_results=10)
    assert len(t.sent) == 1 and t.sent[0].query["maxResults"] == "10"


def test_mkdir_is_one_put_that_leaves_an_existing_folder_alone():
    fs, t = store([Reply(201), Reply(409, {"error": {"code": "PathAlreadyExists"}}), Reply(403)])
    fs.mkdir(WS, LH, "Files/new/deep")
    [r] = t.sent
    assert (r.method, r.base, r.query) == ("PUT", f"{ITEM}/Files/new/deep", {"resource": "directory"})
    assert r.headers["If-None-Match"] == "*"
    fs.mkdir(WS, LH, "Files/new/deep")  # 409: already there
    with pytest.raises(requests.HTTPError):
        fs.mkdir(WS, LH, "Files/denied")


def test_a_partial_read_asks_for_only_those_bytes():
    fs, t = store([Reply(206, b"hello"), Reply(416, b""), Reply(200, b"whole file")])
    assert fs.read_bytes(WS, LH, "Files/big.csv", length=5) == b"hello"
    assert t.sent[0].headers["Range"] == "bytes=0-4" and t.sent[0].method == "GET"
    assert fs.read_bytes(WS, LH, "Files/empty.csv", length=5) == b""  # 416: an empty file
    assert fs.read_bytes(WS, LH, "Files/a.csv") == b"whole file" and "Range" not in t.sent[2].headers


def test_the_live_test_plan_sends_nothing(tmp_path):
    from fabric_desktop import livetest
    from fabric_desktop.fabric_api import FabricClient

    s, t = session([])
    fabric = FabricClient(lambda: "tok", session=s, sleep=lambda _: None)
    test = livetest.LiveTest(WS, fabric, FileStore(lambda: "tok", session=s), None, tmp_path,
                             bad_token_files=FileStore(lambda: "bad", session=s))
    assert test.plan()
    assert t.sent == []


def test_live_test_dry_run_signs_in_to_nothing_and_writes_nothing(tmp_path, capsys):
    from fabric_desktop import cli

    with pytest.raises(SystemExit) as done:
        cli.main(["live-test", "--workspace", WS, "--dry-run", "--out", str(tmp_path)])
    assert done.value.code in (0, None)
    assert "Dry run: nothing was signed in to, created or written." in capsys.readouterr().out
    assert not any(tmp_path.iterdir())  # not even a ledger


def test_the_write_test_refuses_a_production_workspace_before_any_request():
    fs, t = store([])
    with pytest.raises(PermissionError):
        readiness.write_test(fs, WS, LH, "Sales PROD")
    assert t.sent == []


def test_the_write_test_writes_reads_and_deletes_only_its_own_folder():
    written = {}

    def scripted(r):
        if r.method == "PATCH" and r.query.get("action") == "append":
            written["body"] = r.body
        if r.method == "GET":
            return Reply(200, written.get("body", b""))
        return Reply(201 if r.method == "PUT" else 200)

    s, t = session(scripted)
    result = readiness.write_test(FileStore(lambda: "tok", session=s), WS, LH, "Sales Dev")
    assert result["status"] == "ok"
    mutating = [r for r in t.sent if r.method in MUTATING]
    assert {r.path for r in mutating[:3]} == {f"/{WS}/{LH}/{readiness.CHECK_FILE}"}
    assert (mutating[-1].method, mutating[-1].path, mutating[-1].query) == (
        "DELETE", f"/{WS}/{LH}/{readiness.CHECK_FOLDER}", {"recursive": "true"})


# ---- workspace and lakehouse boundaries


@pytest.mark.parametrize("path, expected", [
    ("Files/a.txt", "Files/a.txt"),
    ("/Files/a.txt/", "Files/a.txt"),
    ("Files/my folder/a b.txt", "Files/my%20folder/a%20b.txt"),
    ("Files/ünïcode.txt", "Files/%C3%BCn%C3%AFcode.txt"),
])
def test_writes_and_deletes_go_to_the_given_workspace_and_lakehouse(path, expected):
    fs, t = store([Reply(201), Reply(202), Reply(200), Reply(200)])
    fs.upload(WS, LH, path, b"x")
    fs.delete(WS, LH, path)
    assert {r.host for r in t.sent} == {DFS_HOST}
    assert all(r.url.startswith("https://") for r in t.sent)
    assert {r.path for r in t.sent} == {f"/{WS}/{LH}/{expected}"}


def test_each_call_uses_its_own_workspace_and_lakehouse():
    other_ws, other_lh = "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444"
    fs, t = store([Reply(200), Reply(200)])
    fs.delete(WS, LH, "Files/a")
    fs.delete(other_ws, other_lh, "Files/a")
    assert [r.path for r in t.sent] == [f"/{WS}/{LH}/Files/a", f"/{other_ws}/{other_lh}/Files/a"]


def _inside_item(r) -> bool:
    return r.host == DFS_HOST and r.path.startswith(f"/{WS}/{LH}/") and r.path != f"/{WS}/{LH}/"


@pytest.mark.parametrize("path", ["Files/../../" + "55555555-5555-5555-5555-555555555555/Files/x",
                                  "Files/../../../66666666-6666-6666-6666-666666666666/lh/Files/x"])
def test_a_dot_dot_path_cannot_write_outside_the_lakehouse(path):
    fs, t = store([Reply(201), Reply(202), Reply(200)] * 2)
    try:
        fs.upload(WS, LH, path, b"x")
    except ValueError:
        pass  # refused before anything is sent
    else:
        raise AssertionError("the path was accepted")
    assert t.sent == []


@pytest.mark.parametrize("path", ["", "/"])
def test_an_empty_path_cannot_delete_the_whole_lakehouse(path):
    fs, t = store([Reply(200)])
    try:
        fs.delete(WS, LH, path)
    except ValueError:
        pass  # refused before anything is sent
    else:
        raise AssertionError("the path was accepted")
    assert t.sent == []


@pytest.mark.parametrize("path", ["Files/%2e%2e/%2E%2E/other/Files/x", "Files\\..\\..\\other\\x", "./Files/x"])
def test_encoded_and_backslash_dot_segments_are_refused(path):
    fs, t = store([])
    with pytest.raises(ValueError):
        fs.upload(WS, LH, path, b"x")
    assert t.sent == []


def test_a_folder_name_with_dots_is_still_a_path():
    fs, t = store([Reply(201), Reply(202), Reply(200)])
    fs.upload(WS, LH, "Files/v1..2/..hidden/x.parquet", b"x")
    assert all(_inside_item(r) for r in t.sent) and len(t.sent) == 3


@pytest.mark.parametrize("path", ["Files/../..", "Files/..", "/lakehouse/default/Files/%2e%2e"])
def test_notebookutils_rm_cant_climb_out_of_files(path, tmp_path, monkeypatch):
    # A notebook's fs.rm with recurse reaches FileStore.delete; a climbing path sends nothing.
    from fabric_desktop import lakehouse, notebookutils_local
    real, t = store([])
    monkeypatch.setattr(notebookutils_local, "_store", lambda: real)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached(WS, LH, "Sales", lambda: "tok", tmp_path))
    try:
        fs = notebookutils_local.build(notebookutils_local.Host()).fs
        with pytest.raises((ValueError, PermissionError)):
            fs.rm(path, True)
    finally:
        lakehouse.attach(None)
    assert [s for s in t.sent if s.method in MUTATING] == []


# ---- refused reads (#1313): one line, what wasn't read, the status and OneLake's code, no URL


def test_a_refused_list_says_what_and_why_without_the_url():
    fs, _ = store([Reply(403, {"error": {"code": "AuthorizationPermissionMismatch", "message": "x"}})])
    with pytest.raises(requests.HTTPError) as err:
        fs.list(WS, LH, "Files/in")
    assert str(err.value) == "OneLake didn't list Files/in (403: AuthorizationPermissionMismatch)."
    assert err.value.response.status_code == 403


def test_refused_reads_of_a_file_say_which_file():
    fs, _ = store([Reply(403, headers={"x-ms-error-code": "AuthorizationFailure"})])
    with pytest.raises(requests.HTTPError, match=r"^OneLake didn't read Files/a\.csv \(403: AuthorizationFailure\)\.$"):
        fs.read_bytes(WS, LH, "Files/a.csv")

    fs, _ = store([Reply(401)])
    with pytest.raises(requests.HTTPError, match=r"^OneLake didn't read Files/a\.csv \(401\)\.$") as err:
        fs.download(WS, LH, "Files/a.csv", Path("unused"))
    assert err.value.response.status_code == 401


def test_a_missing_file_is_still_none():
    fs, _ = store([Reply(404, {"error": {"code": "PathNotFound"}})])
    assert fs.read_bytes(WS, LH, "Files/gone.csv") is None


def test_a_refused_table_listing_gives_the_table_apis_code():
    s, _ = session([Reply(403, {"error_code": "PERMISSION_DENIED", "message": "no"})])
    with pytest.raises(requests.HTTPError) as err:
        TableCatalog(lambda: "tok", session=s).list_tables(WS, LH, "dbo")
    assert str(err.value) == "OneLake didn't list the tables in dbo (403: PERMISSION_DENIED)."
    assert "https://" not in str(err.value)


@pytest.mark.parametrize("code, shown", [("AuthorizationFailure", "AuthorizationFailure"), ("a" * 64, "a" * 64),
                                         ("a" * 65, ""), ("Bad code\nwith lines", ""), ("x" * 10000, ""), (None, "")])
def test_an_error_code_is_shown_only_when_it_looks_like_one(code, shown):
    from fabric_desktop.onelake import error_code
    assert error_code(code) == shown
    fs, _ = store([Reply(403, {"error": {"code": code}})])
    with pytest.raises(requests.HTTPError) as err:
        fs.list(WS, LH, "Files/in")
    assert str(err.value) == f"OneLake didn't list Files/in (403{': ' + shown if shown else ''})."
