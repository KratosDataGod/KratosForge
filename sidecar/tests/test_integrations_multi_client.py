"""Several AI tools using the integrations server at once (PR-AI-011): each sees only its own
executions, logs, approvals and audit lines, and one that cancels, hangs up or ends early doesn't
hold up or break the others.

Separate tools are separate connected clients with their own tokens on the real local API (MCP
over Streamable HTTP and REST), as on a PC with Claude Code, Cursor and a script all connected.

Each tool sends 20 calls at once, and the MCP client asks for the tool list again whenever a result
arrives before it has one, so a tool can send 40 or more requests in a moment: past the local API's
per-tool rate limit (RATE_BURST). That limit isn't what these tests are about, so it is lifted for
them; the last test checks it with the real limits, and that one tool reaching it doesn't slow the others.
"""

import json
import socket
import threading
import time

import anyio
import httpx2
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from fabric_desktop.integrations import http_server as local_api
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.mcp_server import MAX_RESULT_BYTES
from fabric_desktop.integrations.registry import Capability, schema
from test_integrations_conformance import connect, env, http_server  # noqa: F401 - env and http_server are fixtures

GRANTS = ["READ_METADATA", "READ_DATA"]
SQL_CALLS, STATUS_CALLS = 5, 15  # 20 calls per tool, all at once
BIG = MAX_RESULT_BYTES - 100_000  # a large result that is still allowed, as JSON, on both routes


def run(fn):
    return anyio.run(fn)


async def await_for(condition, seconds=10.0):
    """Poll ``condition`` until it holds or ``seconds`` pass; what it last said."""
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        await anyio.sleep(0.02)
    return condition()


def wait_for(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


def call_threads():
    """The worker threads handlers run on (core.run_call) that are still running."""
    return {t for t in threading.enumerate() if t.name == "integration-call" and t.is_alive()}


def error_of(result):
    assert result.is_error, result
    return result.structured_content["error"]


@pytest.fixture(autouse=True)
def no_rate_limit(request, monkeypatch):
    """The local API built in these tests has no rate limit, except where a test says it wants the real one."""
    if "real_rate_limit" not in request.keywords:
        unlimited = local_api.RateLimiter
        monkeypatch.setattr(local_api, "RateLimiter", lambda: unlimited(rate=1e6, burst=10**6))


@pytest.fixture
def tools(env, tmp_path, monkeypatch):
    """Three connected tools, each with its own token, over one engine whose lakehouse has a table;
    every call is audited."""
    tables = tmp_path / "tables"
    write_deltalake(str(tables / "sales"), pa.table({"Country": ["US", "UK", "DE"], "Amount": [10.0, 3.0, 1.0]}))
    monkeypatch.setattr(lakehouse, "open_table", lambda name: DeltaTable(str(tables / lakehouse.table_ref(name).table)))
    svc = env["service"]
    svc._auth = type("Auth", (), {"storage_token": lambda self: "storage-tok", "fabric_token": lambda self: "fabric-tok",
                                  "signed_in_user": lambda self: None, "signed_in_tenant": lambda self: None})()
    svc._schema_cache = {"lh1": False}
    env["registry"].audit = IntegrationAudit(env["store"]).record
    out = []
    for name in ("Claude Code", "Cursor", "Script"):
        client, token = env["store"].add_client(name, "other-mcp", transport="http", grants=GRANTS, auto=["READ_DATA"])
        out.append({"client": client, "token": token, "name": name})
    return out


def as_tool(env, tool):
    """``env`` as the conformance helpers take it, for this tool."""
    return {**env, "client": tool["client"], "token": tool["token"]}


def sql_args(n):
    return {"workspace_id": "ws1", "lakehouse_id": "lh1", "query": f"SELECT count(*) + {n} AS n FROM sales",
            "wait_seconds": 30}


# ---- 1. isolation under interleaved calls

def test_interleaved_clients_each_see_only_their_own_executions(env, tools, http_server):
    port = http_server
    started: dict[str, list] = {t["client"]["id"]: [] for t in tools}
    statuses: dict[str, list] = {t["client"]["id"]: [] for t in tools}

    async def calls(tool, n):
        me = tool["client"]["id"]
        async with connect("http", as_tool(env, tool), "auto", port) as c:
            async def sql(i):
                started[me].append(await c.call_tool("fabric_desktop_execute_sql", sql_args(n * 100 + i)))

            async def status():
                statuses[me].append(await c.call_tool("fabric_desktop_get_status", {}))
            async with anyio.create_task_group() as tg:
                for i in range(SQL_CALLS + STATUS_CALLS):
                    tg.start_soon(sql, i) if i % 4 == 0 else tg.start_soon(status)

    async def check(tool):
        me = tool["client"]["id"]
        mine = {o.structured_content["id"] for o in started[me]}
        async with connect("http", as_tool(env, tool), "auto", port) as c:
            listed = await c.call_tool("fabric_desktop_list_executions", {"limit": 500})
            assert not listed.is_error, listed
            assert {row["id"] for row in listed.structured_content["items"]} == mine
            assert {row["client_id"] for row in listed.structured_content["items"]} == {me}
            own = sorted(mine)[0]
            status = await c.call_tool("fabric_desktop_get_execution_status", {"execution_id": own})
            assert (status.structured_content["client_id"], status.structured_content["state"]) == (me, "succeeded")
            logs = await c.call_tool("fabric_desktop_get_execution_logs", {"execution_id": own})
            assert not logs.is_error and any("succeeded" in line for line in logs.structured_content["items"]), logs
            for other in tools:
                if other is tool:
                    continue
                theirs = sorted(o.structured_content["id"] for o in started[other["client"]["id"]])[0]
                for cap in ("fabric_desktop_get_execution_status", "fabric_desktop_get_execution_logs"):
                    refused = await c.call_tool(cap, {"execution_id": theirs})
                    assert error_of(refused)["code"] == "not_found", refused
                    # Nothing of the record comes back: not whose it is, its query or its rows.
                    text = json.dumps(refused.structured_content) + refused.content[0].text
                    assert other["client"]["id"] not in text and "sales" not in text and "rows" not in text

    async def go():
        async with anyio.create_task_group() as tg:  # every tool's calls at once, interleaved
            for n, tool in enumerate(tools):
                tg.start_soon(calls, tool, n)
        for tool in tools:
            await check(tool)
    run(go)

    for n, tool in enumerate(tools):
        me = tool["client"]["id"]
        assert len(started[me]) == SQL_CALLS and len(statuses[me]) == STATUS_CALLS
        for out in started[me]:
            assert not out.is_error, out
            assert (out.structured_content["state"], out.structured_content["client_id"]) == ("succeeded", me), out
        # Each tool got its own queries' answers (3 rows plus the number it put in its query).
        assert sorted(o.structured_content["result"]["rows"][0][0] for o in started[me]) == \
            [3 + n * 100 + i for i in range(SQL_CALLS + STATUS_CALLS) if i % 4 == 0]
        for out in statuses[me]:
            assert not out.is_error and out.structured_content["caller"]["client_id"] == me, out

    # The audit trail has every call under the tool that made it, with that tool's executions only.
    trail = IntegrationAudit(env["store"])
    for tool in tools:
        me = tool["client"]["id"]
        mine = {o.structured_content["id"] for o in started[me]}
        rows = trail.recent(1000, client_id=me)
        assert {(r["client_id"], r["client"], r["channel"]) for r in rows} == {(me, tool["name"], "mcp-http")}
        sql_rows = [r for r in rows if r["capability"] == "fabric_desktop_execute_sql"]
        assert len(sql_rows) == SQL_CALLS and {r["execution_id"] for r in sql_rows} == mine
        assert sum(r["capability"] == "fabric_desktop_get_status" for r in rows) == STATUS_CALLS
        assert {r["execution_id"] for r in rows if r["execution_id"] and r["outcome"] == "ok"} == mine
        # Asking for each other tool's execution (status and logs) is recorded as not found.
        assert sum(r["outcome"] == "not_found" for r in rows) == 2 * (len(tools) - 1)
        # Recent activity in Settings, for this tool, shows only its calls.
        activity = env["service"].integrations_activity(1000, client_id=me)
        assert len(activity) == len(rows) and {r["client_id"] for r in activity} == {me}


# ---- 2. one tool cancelling

@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_one_client_cancelling_mid_call_leaves_the_others_working(env, tools, http_server, mode):
    port = http_server
    a, b = tools[0], tools[1]
    entered, stopped = threading.Event(), []

    def block(svc, ctx):
        entered.set()
        deadline = time.monotonic() + 20  # never hang the run if the cancel doesn't arrive
        while not ctx.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        stopped.append((ctx.caller.client_id, ctx.cancelled))
        return {}
    env["registry"].register(Capability("ext_block", "Blocks", "Waits until cancelled.", schema(), schema(extra=True),
                                        "READ_METADATA", block, needs_service=False, timeout=30))

    async def go():
        async with connect("http", as_tool(env, a), mode, port) as ca, connect("http", as_tool(env, b), mode, port) as cb:
            async with anyio.create_task_group() as tg:
                tg.start_soon(ca.call_tool, "ext_block", {})
                assert await await_for(entered.is_set, 20), "the handler never started"
                # While A's call is stuck, B's go straight through, several at once.
                during = []

                async def status():
                    during.append(await cb.call_tool("fabric_desktop_get_status", {}))
                async with anyio.create_task_group() as calls:
                    for _ in range(8):
                        calls.start_soon(status)
                assert len(during) == 8 and not any(r.is_error for r in during), during
                assert not stopped, "B's calls ended A's"
                tg.cancel_scope.cancel()  # A cancels its call
            assert await await_for(lambda: stopped, 10), "the cancelled handler was never told to stop"
            assert stopped == [(a["client"]["id"], True)]
            after = await cb.call_tool("fabric_desktop_get_status", {})
            assert not after.is_error and after.structured_content["caller"]["client_id"] == b["client"]["id"]
            # A's session carries on too.
            assert not (await ca.call_tool("fabric_desktop_get_status", {})).is_error
    run(go)


# ---- 3. one tool hanging up on a large result

def _rest_call(port, token, cap_id):
    """A raw REST call on a socket of our own, so the test decides when it is closed."""
    body = b'{"arguments": {}}'
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(b"POST /api/v1/capabilities/%s/invoke HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                 b"Authorization: Bearer %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                 % (cap_id.encode(), port, token.encode(), len(body), body))
    return sock


@pytest.mark.parametrize("route", ["rest", "mcp"])
@pytest.mark.parametrize("when", ["producing", "sending"])
def test_a_client_hanging_up_on_a_large_result_leaves_the_server_serving(env, tools, http_server, route, when):
    port = http_server
    a, b = tools[0], tools[1]
    before = call_threads()  # other tests' leftovers in this process are not this test's
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def large(svc, ctx):
        entered.set()
        release.wait(10)
        try:
            return {"blob": "x" * BIG}
        finally:
            finished.set()
    env["registry"].register(Capability("ext_large", "Large", "A large result.", schema(), schema(extra=True),
                                        "READ_METADATA", large, needs_service=False, timeout=30))

    if route == "rest":
        sock = _rest_call(port, a["token"], "ext_large")
        assert wait_for(entered.is_set, 20), "the handler never started"
        if when == "sending":
            release.set()
            assert sock.recv(1024).startswith(b"HTTP/1.1 200"), "the result wasn't being sent"
        sock.close()  # hang up with the result unread
        release.set()
    else:
        async def hang_up():
            async with connect("http", as_tool(env, a), "auto", port) as ca:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(ca.call_tool, "ext_large", {})
                    assert await await_for(entered.is_set, 20), "the handler never started"
                    if when == "sending":
                        release.set()
                        assert await await_for(finished.is_set, 10)
                    tg.cancel_scope.cancel()
            # The client and its connection are closed here, the call unanswered.
            release.set()
        run(hang_up)

    # Another tool's next requests are answered at once, on both routes.
    status = httpx2.post(f"http://127.0.0.1:{port}/api/v1/capabilities/fabric_desktop_get_status/invoke",
                         headers={"Authorization": f"Bearer {b['token']}"}, json={"arguments": {}}, timeout=10)
    assert status.status_code == 200 and status.json()["result"]["caller"]["client_id"] == b["client"]["id"]

    async def other():
        async with connect("http", as_tool(env, b), "auto", port) as cb:
            out = await cb.call_tool("ext_large", {})  # the same large result, in full
            assert not out.is_error and len(out.structured_content["blob"]) == BIG, out.structured_content.get("error")
    run(other)
    # No handler thread is left running: the hung-up call's ended once its work did.
    assert wait_for(finished.is_set, 10)
    assert wait_for(lambda: not (call_threads() - before), 5), call_threads() - before


# ---- 4. approvals belong to the tool that asked

def test_an_approval_given_to_one_client_cant_be_used_by_another(env, tools, http_server):
    """An approval is bound to the tool that asked as well as the arguments: another tool making
    the identical call gets its own request, and can't spend the first."""
    port = http_server
    store = env["store"]
    # No pre-approval for READ_DATA, so every query asks.
    a, a_token = store.add_client("Asker", "other-mcp", transport="http", grants=GRANTS)
    b, b_token = store.add_client("Other", "other-mcp", transport="http", grants=GRANTS)
    url = f"http://127.0.0.1:{port}/api/v1/capabilities/fabric_desktop_execute_sql/invoke"
    body = {"arguments": sql_args(0)}

    def ask(token):
        return httpx2.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=10)

    first = ask(a_token)
    assert first.status_code == 428 and first.json()["error"]["code"] == "approval_required", first.text
    a_approval = first.json()["error"]["details"]["approval_id"]
    store.decide(a_approval, True)

    for _ in range(2):  # asking again doesn't change that
        theirs = ask(b_token)
        assert theirs.status_code == 428 and theirs.json()["error"]["code"] == "approval_required", theirs.text
        b_approval = theirs.json()["error"]["details"]["approval_id"]
        assert b_approval != a_approval and store.approval(b_approval)["client_id"] == b["id"]
        assert store.approval(a_approval)["state"] == "approved"
    # Nor at the store, with the exact arguments' hash.
    assert store.consume(a_approval, b["id"], store.approval(a_approval)["args_hash"]) is False
    assert store.approval(a_approval)["state"] == "approved"

    mine = ask(a_token)
    assert mine.status_code == 200 and mine.json()["result"]["state"] == "succeeded", mine.text
    assert store.approval(a_approval)["state"] == "used"
    assert ask(b_token).status_code == 428
    assert store.approval(b_approval)["state"] == "pending"
    lines = IntegrationAudit(store).recent(100, client_id=b["id"])
    assert len(lines) == 3 and {r["decision"] for r in lines} == {"approval_pending"}
    assert all(r.get("approval_id", "") != a_approval for r in lines)


# ---- 5. one tool reaching its rate limit

@pytest.mark.real_rate_limit
def test_one_tool_reaching_its_rate_limit_doesnt_limit_the_others(env, tools, http_server):
    port = http_server

    def status(tool):
        sock = _rest_call(port, tool["token"], "fabric_desktop_get_status")
        try:
            return int(sock.recv(4096).split(b" ", 2)[1])
        finally:
            sock.close()
    noisy, quiet = tools[0], tools[1]
    codes = [status(noisy) for _ in range(local_api.RATE_BURST + 5)]
    assert 429 in codes and codes[:local_api.RATE_BURST // 2] == [200] * (local_api.RATE_BURST // 2), codes
    assert [status(quiet) for _ in range(5)] == [200] * 5
