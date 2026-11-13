"""Every Fabric and OneLake list reads all its pages, once each: continuationUri and continuationToken,
a page cap that says the list has more than N pages, retried pages that add nothing twice, and
large-workspace fakes of 10,000 items. The inventory at the end fails when a list read doesn't page."""

import ast
import json
import pathlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from test_fabric_api import Resp, client

from fabric_desktop import paging
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.onelake import FileStore

TOTAL, PER_PAGE = 10_000, 100


class Workspace:
    """A Fabric list of ``total`` items in pages of ``per_page``, linked by ``continuationUri``,
    ``continuationToken`` or both. ``fail`` maps a page number to the statuses it answers before 200."""

    def __init__(self, link="uri", total=TOTAL, per_page=PER_PAGE, fail=None, etag=None):
        self.link, self.total, self.per_page, self.etag = link, total, per_page, etag
        self.fail = {k: list(v) for k, v in (fail or {}).items()}
        self.calls: list[tuple[str, str]] = []
        self.served: list[int] = []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        page = int(parse_qs(urlsplit(url).query).get("continuationToken", ["0"])[0])
        if self.fail.get(page):
            return Resp(self.fail[page].pop(0), {"error": {"code": "Busy"}}, {"Retry-After": "0"})
        self.served.append(page)
        start = page * self.per_page
        body = {"value": [{"id": f"item-{i}"} for i in range(start, min(start + self.per_page, self.total))]}
        if start + self.per_page < self.total:
            nxt = page + 1
            if self.link in ("uri", "both"):
                body["continuationUri"] = f"{url.split('?', 1)[0]}?continuationToken={nxt}"
            if self.link in ("token", "both"):
                body["continuationToken"] = str(nxt)
        return Resp(200, body, {"ETag": self.etag} if self.etag and page == 0 else {})


def fabric(ws):
    return FabricClient(lambda: "tok", session=ws, sleep=lambda s: None)


def ids(items):
    got = [i.get("id") or i.get("name") for i in items]
    assert len(got) == len(set(got)), "an item was listed twice"
    return got


EXPECTED = [f"item-{i}" for i in range(TOTAL)]


@pytest.mark.parametrize("link", ["uri", "token", "both"])
def test_a_10k_item_workspace_lists_every_item_once(link):
    ws = Workspace(link)
    assert ids(fabric(ws).list_items("ws1")) == EXPECTED
    assert ws.served == list(range(TOTAL // PER_PAGE))


def test_a_token_only_page_is_read_with_the_first_pages_url_and_its_query():
    ws = Workspace("token", total=250)
    libs = fabric(ws)._paged("/workspaces/ws1/environments/e1/libraries?beta=False")
    assert len(ids(libs)) == 250
    second = urlsplit(ws.calls[1][1])
    assert second.path == "/v1/workspaces/ws1/environments/e1/libraries"
    assert parse_qs(second.query) == {"beta": ["False"], "continuationToken": ["1"]}
    assert parse_qs(urlsplit(ws.calls[2][1]).query)["continuationToken"] == ["2"]  # replaced, not added again


def test_retried_pages_add_nothing_twice_and_drop_nothing():
    ws = Workspace("uri", fail={0: [429], 37: [503, 503], 99: [500]})
    assert ids(fabric(ws).list_items("ws1")) == EXPECTED
    assert ws.served == list(range(TOTAL // PER_PAGE))
    assert len(ws.calls) == TOTAL // PER_PAGE + 4


def test_a_page_that_keeps_failing_fails_the_list_instead_of_returning_part_of_it():
    ws = Workspace("token", fail={50: [503] * 20})
    with pytest.raises(FabricError):
        fabric(ws).list_items("ws1")


def test_the_cap_says_the_list_has_more_than_n_pages(monkeypatch):
    monkeypatch.setattr(paging, "MAX_PAGES", 30)
    for link in ("uri", "token"):
        ws = Workspace(link)
        with pytest.raises(FabricError) as exc:
            fabric(ws).list_items("ws1")
        assert exc.value.code == "PagingStopped"
        assert "more than 30 pages" in str(exc.value) and "isn't complete" in str(exc.value)
        assert len(ws.calls) == 30


def test_a_repeated_continuation_token_stops_the_list():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationToken": "t"})] * 3)
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.code == "PagingStopped" and "same next page twice" in str(exc.value) and len(s.calls) == 2


def test_null_continuations_end_the_list():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": "null", "continuationToken": None})])
    assert ids(c.list_workspaces()) == ["a"] and len(s.calls) == 1


def test_data_access_roles_page_by_token_and_keep_the_first_pages_etag():
    ws = Workspace("token", total=1_000, etag='"v1"')
    roles, etag = fabric(ws).list_data_access_roles_tagged("ws1", "lh1")
    assert len(ids(roles)) == 1_000 and etag == '"v1"'


def test_activity_runs_follow_the_query_continuation():
    c, s = client([Resp(200, {"value": [{"id": "r1"}], "continuationToken": "t2"}),
                   Resp(200, {"value": [{"id": "r2"}], "continuationToken": "t3"}),
                   Resp(200, {"value": [{"id": "r3"}]})])
    assert ids(c.query_activity_runs("ws1", "j1", "a", "b")) == ["r1", "r2", "r3"]
    assert [body.get("continuationToken") for _, _, body in s.calls] == [None, "t2", "t3"]
    assert all(body["lastUpdatedAfter"] == "a" for _, _, body in s.calls)


def test_activity_runs_stop_on_a_repeated_continuation():
    c, s = client([Resp(200, {"value": [{"id": "r1"}], "continuationToken": "t"})] * 3)
    with pytest.raises(FabricError) as exc:
        c.query_activity_runs("ws1", "j1", "a", "b")
    assert exc.value.code == "PagingStopped" and len(s.calls) == 2


class Dfs:
    """A OneLake folder of 10,000 files in pages of 500, the first answer to page 7 a 503."""

    def __init__(self):
        self.calls, self.failed = 0, False

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        self.calls += 1
        page = int((params or {}).get("continuation", "0"))
        r = requests.Response()
        r._content_consumed = True
        if page == 7 and not self.failed:
            self.failed, r.status_code, r._content = True, 503, b"{}"
            return r
        r.status_code = 200
        names = [{"name": f"lh/Files/f{i:05}", "contentLength": "1"} for i in range(page * 500, page * 500 + 500)]
        r._content = json.dumps({"paths": names}).encode()
        if page < 19:
            r.headers["x-ms-continuation"] = str(page + 1)
        return r


def test_a_10k_file_onelake_folder_lists_every_file_once_through_a_retry():
    dfs = Dfs()
    store = FileStore(lambda: "tok", session=dfs, sleep=lambda s: None)
    files = store.list("ws", "lh")
    assert [f["name"] for f in files] == [f"f{i:05}" for i in range(TOTAL)]
    assert dfs.calls == 21


# ---- inventory: a function that reads a list out of a JSON answer has to page it

SRC = pathlib.Path(__file__).resolve().parents[1] / "fabric_desktop"
LIST_KEY = re.compile(r"""\.get\(\s*["'](value|paths|data|libraries|domains)["']|\[\s*["'](value|paths)["']\s*\]""")
PAGES = re.compile(r"\bPages\(|\b_paged\(|\b_pages\(")
NOT_PAGED = {  # (file, function): why it's one answer
    ("fabric_api.py", "list_admin_domains"): "Fabric's List Domains answers every domain at once; it has no continuation",
    ("xray_service.py", "_powerbi_dataflows"): "Power BI's Get Dataflows in Group answers every dataflow at once",
    ("estate_service.py", "access_find_person"): "a Graph lookup for one person; only the first match is used",
    ("notebookutils_local.py", "_make_credentials"): "a Key Vault secret's value, not a list",
    ("notebookutils_local.py", "getSecret"): "a Key Vault secret's value, not a list",
}


def unpaged_list_reads():
    found = set()
    for path in SRC.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if ".json()" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node) or ""
                if ".json()" in body and LIST_KEY.search(body) and not PAGES.search(body):
                    found.add((path.name, node.name))
    return found


def test_every_list_read_pages_or_says_why_it_doesnt():
    found = unpaged_list_reads()
    assert found - set(NOT_PAGED) == set(), "these read a list from one answer without paging it: page it " \
        "(_paged, or a loop through paging.Pages) or add it to NOT_PAGED with the reason"
    assert set(NOT_PAGED) - found == set(), "NOT_PAGED names a function that no longer needs it: take it out"
