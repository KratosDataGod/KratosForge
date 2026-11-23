"""Hostile arguments for every tool, on every route in (#2363 hardening).

Like test_integrations_tool_guards.py, each test walks the whole registry, core and built-in
extensions, so a tool added later is covered without anyone remembering to add it here. Each
string, array and object field gets values that are still inside its schema where that is possible,
and values past it: control characters, NUL, right-to-left overrides, lone surrogates and CRLF at
exactly the field's maxLength; SQL-, path- and template-shaped text; arrays just under, at and far
past maxItems; JSON nested 10,000 deep as a field's value, inside an object field and as an unknown
field; and ``__proto__``, ``constructor`` and duplicate keys.

For every case:

- the call ends with a result or an IntegrationError with a known code, never ``internal``, and no
  other Python exception (RecursionError included) gets out of ``Registry.invoke``;
- it ends within a few seconds;
- the handler, a spy, only ever sees arguments that pass the tool's schema, nested no deeper than
  core.MAX_DEPTH;
- the call still gets its audit line, whatever its arguments.

The nesting cases go over MCP (in memory, and over HTTP as raw JSON, since an MCP client can't
serialize 10,000 levels itself) and over the local API too, where the JSON is parsed. Then the real
read-only tools that read this PC (notebooks, local data projects, the metadata project) get
injection- and path-shaped values that their schemas allow: each is not_found or invalid_input (or
an empty answer), and nothing outside the data folders is opened, created or changed.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import threading
from pathlib import Path

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from fabric_desktop.integrations import peer
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CODES, MAX_DEPTH, CallContext, Caller, IntegrationError, validate
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_limits import PORT, AllowAll, api, valid_args
from test_integrations_tool_guards import spied

KNOWN = set(CODES) - {"internal"}
CALL_SECONDS = 10.0  # each call must end well within this; a fast handler answers in milliseconds
DEEP = 10_000

# Text a hostile caller sends: each is sent as it is and repeated to exactly the field's maxLength.
SEEDS = {
    "control": "".join(chr(i) for i in range(1, 32)) + "\x7f",
    "nul": "a\x00b\x00",
    "rtl_override": "\u202eexe.txt\u2066\u2067\u200f\u061c",
    "lone_surrogates": "\ud800x\udfff\udbff",
    "escaped_surrogates": "\\ud800\\udfff",
    "crlf": "a\r\nInjected: header\r\n\r\n",
    "sql": "'; DROP TABLE x; --",
    "sql_union": "' OR 1=1 UNION SELECT * FROM secrets --",
    "windows_up": "..\\..\\..\\Windows\\win.ini",
    "drive": "C:\\Windows\\System32\\config\\SAM",
    "unc": "\\\\server\\share\\file.txt",
    "file_url": "file:///etc/passwd",
    "posix_up": "../../../../etc/passwd",
    "template": "{{7*7}}${7*7}<%= 7*7 %>#{7*7}",
    "jndi": "${jndi:ldap://127.0.0.1:1389/a}",
    "proto_json": '{"__proto__": {"admin": true}, "a": 1, "a": 2}',
}
PROTO_KEYS = ("__proto__", "constructor", "prototype", "__class__", "__init__")


def hostile_strings(prop: dict) -> list[tuple[str, str]]:
    """(name, value) for a string field: each seed as it is, and at exactly maxLength (and one past)."""
    out = [(name, seed) for name, seed in SEEDS.items()]
    top = prop.get("maxLength")
    if top:
        for name, seed in SEEDS.items():
            filled = (seed * (top // len(seed) + 1))[:top]
            out += [(name + "@max", filled), (name + "@max+1", filled + "x")]
    return out


def deep_list(n: int):
    v: object = "x"
    for _ in range(n):
        v = [v]
    return v


def deep_dict(n: int):
    v: object = "x"
    for _ in range(n):
        v = {"a": v}
    return v


def depth(value) -> int:
    """How many lists and objects deep ``value`` goes, without recursion."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        v, d = stack.pop()
        children = v.values() if isinstance(v, dict) else v if isinstance(v, list) else None
        if children is None:
            continue
        deepest = max(deepest, d)
        stack.extend((c, d + 1) for c in children)
    return deepest


def kinds(prop: dict) -> list[str]:
    k = prop.get("type")
    return [k] if isinstance(k, str) else list(k or [])


class Recorder:
    """A spy for every handler: records the arguments each call reached it with."""

    def __init__(self):
        self.seen: list[tuple[str, dict]] = []

    def registry(self, authorizer=None) -> Registry:
        plain, _ = spied()
        reg = Registry(authorizer or AllowAll())
        for cap in plain.all():
            def handler(svc, ctx, _id=cap.id, **args):
                self.seen.append((_id, args))
                return {"ran": _id}
            # Any object out: the MCP client checks results against the output schema, which isn't this file's subject.
            reg.register(dataclasses.replace(cap, handler=handler, bind=None, output_schema={"type": "object"}))
        return reg

    def check(self, reg: Registry) -> None:
        """Every call that reached a handler had arguments its schema takes, nested no deeper than allowed."""
        for cap_id, args in self.seen:
            validate(reg.get(cap_id).input_schema, args)
            assert depth(args) <= MAX_DEPTH, (cap_id, depth(args))


def ctx() -> CallContext:
    return CallContext(Caller("t", "test"), timeout=5)


def bounded(fn, seconds: float = CALL_SECONDS):
    """``fn()``'s result, or its exception, run on a thread that must finish within ``seconds``."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back below
            box["error"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"the call didn't end within {seconds:g} seconds"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def outcome(reg: Registry, cap_id: str, args) -> str:
    """"ok" or the IntegrationError's code; anything else fails the test."""
    try:
        bounded(lambda: reg.invoke(object(), cap_id, args, ctx()))
    except IntegrationError as err:
        assert err.code in KNOWN, (cap_id, err.code, err.message)
        return err.code
    except BaseException as exc:  # noqa: BLE001 - the point: nothing but IntegrationError gets out
        pytest.fail(f"{cap_id}: {type(exc).__name__} got out of Registry.invoke: {str(exc)[:200]}")
    return "ok"


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setattr(peer, "checked", lambda: False)


# ------------------------------------------------------------------------- Registry.invoke

def test_hostile_strings_in_every_string_field_are_answered_or_refused():
    rec = Recorder()
    reg = rec.registry()
    seen = refused = 0
    for cap in reg.all():
        base = valid_args(cap)
        for field, prop in cap.input_schema.get("properties", {}).items():
            if "string" in kinds(prop):
                values = hostile_strings(prop)
            elif "array" in kinds(prop) and "string" in kinds(prop.get("items") or {}):
                values = [(n, [v]) for n, v in hostile_strings(prop["items"])]
            else:
                continue
            for name, value in values:
                got = outcome(reg, cap.id, base | {field: value})
                assert got in ("ok", "invalid_input"), (cap.id, field, name, got)
                seen += got == "ok"
                refused += got == "invalid_input"
    rec.check(reg)
    assert seen and refused, "some hostile text fits a schema and some doesn't"
    assert len(rec.seen) == seen


@pytest.mark.parametrize("make", [deep_list, deep_dict], ids=["lists", "objects"])
def test_json_nested_10000_deep_in_any_field_is_refused_before_anything_runs(make):
    rec = Recorder()
    reg = rec.registry()
    deep = make(DEEP)
    for cap in reg.all():
        base = valid_args(cap)
        fields = list(cap.input_schema.get("properties", {})) + ["zz_unknown"]
        for field in fields:
            assert outcome(reg, cap.id, base | {field: deep}) == "invalid_input", (cap.id, field)
            prop = cap.input_schema.get("properties", {}).get(field, {})
            if "object" in kinds(prop):  # inside an object field that takes anything
                assert outcome(reg, cap.id, base | {field: {"k": deep, "ok": 1}}) == "invalid_input", (cap.id, field)
        assert outcome(reg, cap.id, deep if make is deep_dict else {"a": deep}) == "invalid_input", cap.id
    assert rec.seen == []


def test_nesting_up_to_the_limit_still_reaches_an_object_field_and_one_more_level_doesnt():
    rec = Recorder()
    reg = rec.registry()
    objects = [(cap, f) for cap in reg.all() for f, p in cap.input_schema.get("properties", {}).items()
               if "object" in kinds(p)]
    assert objects, "some tool takes an object (pipeline parameters, mocks, proof rules)"
    for cap, field in objects:
        base = valid_args(cap)
        # the arguments are level 1 and the field's object level 2, so MAX_DEPTH - 2 more fit
        assert outcome(reg, cap.id, base | {field: {"k": deep_dict(MAX_DEPTH - 2)}}) == "ok", (cap.id, field)
        assert outcome(reg, cap.id, base | {field: {"k": deep_dict(MAX_DEPTH - 1)}}) == "invalid_input", (cap.id, field)
    rec.check(reg)
    assert len(rec.seen) == len(objects)


def test_arrays_just_under_at_and_far_past_max_items():
    rec = Recorder()
    reg = rec.registry()
    arrays = 0
    for cap in reg.all():
        base = valid_args(cap)
        for field, prop in cap.input_schema.get("properties", {}).items():
            if "array" not in kinds(prop):
                continue
            arrays += 1
            items = prop.get("items") or {}
            one = items["enum"][0] if "enum" in items else "ab"
            top = prop["maxItems"]
            for n in {max(0, top - 1), top}:
                value = list(items["enum"][:n]) if "enum" in items and n <= len(items["enum"]) else [one] * n
                got = outcome(reg, cap.id, base | {field: value})
                assert got in ("ok", "invalid_input"), (cap.id, field, n, got)  # an enum list may refuse repeats
            for n in (top + 1, 100_000):
                assert outcome(reg, cap.id, base | {field: [one] * n}) == "invalid_input", (cap.id, field, n)
            assert outcome(reg, cap.id, base | {field: [deep_list(50)] * 3}) == "invalid_input", (cap.id, field)
    assert arrays
    rec.check(reg)


def test_a_huge_flat_object_in_an_object_field_ends_quickly():
    rec = Recorder()
    reg = rec.registry()
    huge = {f"k{i}": i for i in range(200_000)}
    for cap in reg.all():
        for field, prop in cap.input_schema.get("properties", {}).items():
            if "object" in kinds(prop):
                assert outcome(reg, cap.id, valid_args(cap) | {field: huge}) == "ok", (cap.id, field)
    rec.check(reg)


def test_proto_and_constructor_keys_are_unknown_fields_everywhere():
    rec = Recorder()
    reg = rec.registry()
    for cap in reg.all():
        base = valid_args(cap)
        for key in PROTO_KEYS:
            assert outcome(reg, cap.id, base | {key: {"admin": True}}) == "invalid_input", (cap.id, key)
    assert rec.seen == []


def test_arguments_that_arent_an_object_are_refused():
    rec = Recorder()
    reg = rec.registry()
    for cap in reg.all():
        for bad in ([], "x", 1, True, [valid_args(cap)], deep_list(DEEP)):
            assert outcome(reg, cap.id, bad) == "invalid_input", (cap.id, bad if not isinstance(bad, list) else "list")
    assert rec.seen == []


def test_every_hostile_call_still_gets_its_audit_line(tmp_path):
    """The audit line is written from the arguments as sent, refused or not: a value nested past
    the recursion limit, or text that isn't valid UTF-8, must not cost the call its line."""
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    audit = IntegrationAudit(store)
    rec = Recorder()
    reg = rec.registry()
    reg.audit = audit.record
    cap = reg.get("fabric_desktop_check_pipeline_run")
    cases = [{"zz": deep_dict(DEEP)}, {"zz": deep_list(DEEP)}, valid_args(cap) | {"parameters": {"k": deep_dict(DEEP)}},
             valid_args(cap) | {"definition": SEEDS["lone_surrogates"]}, valid_args(cap) | {"definition": SEEDS["nul"]},
             valid_args(cap) | {"parameters": {SEEDS["lone_surrogates"]: SEEDS["rtl_override"]}}]
    for args in cases:
        outcome(reg, cap.id, args)
    lines = audit.recent(limit=100)
    assert len(lines) == len(cases), [line["outcome"] for line in lines]
    assert audit.verify()["ok"] is True


# ------------------------------------------------------------------------------ transports

def test_nesting_over_mcp_is_refused_and_the_connection_keeps_working():
    rec = Recorder()
    reg = rec.registry()
    # The MCP client serializes arguments itself and gives up long before 10,000 levels; on Windows,
    # with its smaller stack, pydantic's serializer already gives up near 200 ("Circular reference
    # detected (depth exceeded)"). A few levels past MAX_DEPTH is what this test is about; 10,000
    # deep over MCP is the HTTP test below.
    n = MAX_DEPTH + 8

    async def go():
        async with Client(build_server(reg, lambda: object(), "cl_t")) as c:
            for cap in reg.all():
                base = valid_args(cap)
                for field in list(cap.input_schema.get("properties", {})) + ["zz_unknown"]:
                    for deep in (deep_dict(n), deep_list(n)):
                        with anyio.fail_after(CALL_SECONDS):
                            out = await c.call_tool(cap.id, base | {field: deep})
                        assert out.is_error, (cap.id, field)
                        assert out.structured_content["error"]["code"] == "invalid_input", (cap.id, field)
            out = await c.call_tool("fabric_desktop_get_status", {})
            assert not out.is_error and out.structured_content == {"ran": "fabric_desktop_get_status"}
    anyio.run(go)
    assert rec.seen == [("fabric_desktop_get_status", {})]


def test_hostile_strings_over_mcp_are_answered_or_refused():
    rec = Recorder()
    reg = rec.registry()
    sendable = {k: v for k, v in SEEDS.items() if k != "lone_surrogates"}  # not encodable as UTF-8 JSON

    async def go():
        async with Client(build_server(reg, lambda: object(), "cl_t")) as c:
            for cap in reg.all():
                base = valid_args(cap)
                for field, prop in cap.input_schema.get("properties", {}).items():
                    if "string" not in kinds(prop):
                        continue
                    for value in sendable.values():
                        with anyio.fail_after(CALL_SECONDS):
                            out = await c.call_tool(cap.id, base | {field: value}) if not prop.get("enum") else None
                        if out is not None and out.is_error:
                            assert out.structured_content["error"]["code"] == "invalid_input", (cap.id, field)
    anyio.run(go)
    rec.check(reg)


@pytest.fixture
def token(tmp_path):
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    store.set_setting("enabled", True)
    _, tok = store.add_client("Script", "other", transport="http")
    return store, tok


def post(c: TestClient, path: str, body: str | bytes, tok: str, **extra):
    return c.post(path, content=body, headers={"Authorization": f"Bearer {tok}", "Host": f"127.0.0.1:{PORT}",
                                               "Content-Type": "application/json", **extra})


def nested_json(n: int, kind: str) -> str:
    return "[" * n + "]" * n if kind == "list" else '{"a":' * n + "1" + "}" * n


@pytest.mark.parametrize("kind", ["list", "object"])
def test_nesting_over_the_local_api_is_a_400_and_the_next_request_works(token, kind):
    store, tok = token
    rec = Recorder()
    reg = rec.registry()
    app = api(reg, store)
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000), raise_server_exceptions=False)
    url = "/api/v1/capabilities/fabric_desktop_check_pipeline_run/invoke"
    deep = nested_json(DEEP, kind)
    for body in ('{"arguments": {"definition": "ab", "parameters": ' + deep + "}}",
                 '{"arguments": {"definition": "ab", "parameters": {"k": ' + deep + "}}}",
                 '{"arguments": {"definition": "ab", "zz": ' + deep + "}}",
                 '{"arguments": ' + deep + "}",
                 deep,
                 '{"arguments": {"definition": "ab", "parameters": {"k": ' + nested_json(MAX_DEPTH, kind) + "}}}"):
        res = bounded(lambda body=body: post(c, url, body, tok))
        assert res.status_code == 400, (res.status_code, res.text[:200])
        err = res.json()["error"]
        assert err["code"] == "invalid_input" and err["message"], err
    assert rec.seen == []
    res = post(c, url, '{"arguments": {"definition": "ab"}}', tok)
    assert res.status_code == 200 and res.json()["result"] == {"ran": "fabric_desktop_check_pipeline_run"}


def test_duplicate_keys_and_proto_over_the_local_api(token):
    """JSON with a key twice is read as its last value (Python's json), and the schema then checks that:
    a caller can't slip a second value past the check."""
    store, tok = token
    rec = Recorder()
    reg = rec.registry()
    app = api(reg, store)
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000), raise_server_exceptions=False)
    url = "/api/v1/capabilities/fabric_desktop_get_local_tables/invoke"
    res = post(c, url, '{"arguments": {"project": "good-1", "project": "../../etc"}}', tok)
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_input"
    res = post(c, url, '{"arguments": {"project": "../../etc", "project": "good-1"}}', tok)
    assert res.status_code == 200 and rec.seen == [("fabric_desktop_get_local_tables",
                                                    {"project": "good-1", "offset": 0, "limit": 100})]
    for key in PROTO_KEYS:
        res = post(c, url, '{"arguments": {"project": "good-1", "%s": {"admin": true}}}' % key, tok)
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_input", key
        res = post(c, url, '{"%s": {"admin": true}, "arguments": {"project": "good-1"}}' % key, tok)
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_input", key
    rec.check(reg)


def test_nesting_over_mcp_over_http_is_a_parse_error_and_the_session_keeps_working(token):
    """The MCP SDK parses each request with a depth limit, so 10,000 levels never reach KratosForge."""
    store, tok = token
    rec = Recorder()
    reg = rec.registry()
    app = api(reg, store)
    accept = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000),
                    raise_server_exceptions=False) as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fuzz", "version": "1"}}}
        res = post(c, "/mcp", json.dumps(init), tok, **accept)
        assert res.status_code == 200
        session = {"mcp-session-id": res.headers["mcp-session-id"], "mcp-protocol-version": "2025-11-25", **accept}
        post(c, "/mcp", '{"jsonrpc": "2.0", "method": "notifications/initialized"}', tok, **session)
        for kind in ("list", "object"):
            for where in ("parameters", "zz"):
                body = ('{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"fabric_desktop_check_pipeline_run",'
                        '"arguments":{"definition":"ab","%s":%s}}}' % (where, nested_json(DEEP, kind)))
                res = bounded(lambda body=body: post(c, "/mcp", body, tok, **session))
                assert res.status_code == 400 and res.json()["error"]["code"] == -32700, res.text[:200]
        call = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "fabric_desktop_get_status", "arguments": {}}}
        res = post(c, "/mcp", json.dumps(call), tok, **session)
        assert res.status_code == 200 and '"ran":"fabric_desktop_get_status"' in res.text.replace(" ", "")
    assert rec.seen == [("fabric_desktop_get_status", {})]


# ------------------------------------------------------------ real read-only tools on this PC

_OPENED: list[str] = []
_RECORDING = [False]


def _hook(event, args):
    if _RECORDING[0] and event in ("open", "os.listdir", "os.scandir") and args and args[0] is not None:
        _OPENED.append(os.fsdecode(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else "")


sys.addaudithook(_hook)  # can't be removed; it does nothing unless _RECORDING is on


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*"))}


def under(path: str, roots) -> bool:
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in roots)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The engine on a data folder with a notebook, a local data project reading a folder of CSV and a
    metadata project, beside a folder it must never touch (with a sentinel file in it)."""
    from fabric_desktop.service import Service
    base = tmp_path_factory.mktemp("hostile")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ProgramData", str(base / "programdata"))
        outside = base / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not read", encoding="utf-8")
        data = base / "data"
        data.mkdir()
        (data / "orders.csv").write_text("id,amount\n1,10\n", encoding="utf-8")
        svc = Service(root=base / "app")
        svc.notebooks.save("Good", [{"type": "code", "source": "x = 1"}])
        pid = svc.local_create_project("Sales")["id"]
        svc.local_add_source(pid, str(data))
        svc.meta_init(str(base / "crm"), "3")
        reg = Registry()
        register_core(reg)
        yield {"svc": svc, "reg": reg, "base": base, "outside": outside, "pid": pid,
               "roots": [(base / d).resolve() for d in ("app", "data", "crm")]}
        svc.local_data.close_all()


def aimed_at(world) -> list[str]:
    """Schema-valid text aimed at the sentinel and at system files, plus injection-shaped text."""
    sentinel = world["outside"] / "sentinel.txt"
    return [*SEEDS.values(), str(sentinel), str(world["outside"]), "../../outside/sentinel.txt", "..\\..\\outside\\sentinel",
            "../outside/sentinel", "sentinel", sentinel.as_uri(), "x" * 150, "CON", "NUL", "aux.txt", ".", "..",
            "main.orders; DROP TABLE orders", "main.\"orders\"", "main.orders/../../outside"]


READS = {  # tool: (the field given the hostile value, the other arguments)
    "fabric_desktop_get_notebook": ("name", {}),
    "fabric_desktop_get_notebook_dependencies": ("name", {}),
    "fabric_desktop_get_notebook_environment": ("name", {}),
    "fabric_desktop_validate_notebook": ("name", {}),
    "fabric_desktop_check_proof": ("name", {}),
    "fabric_desktop_list_proofs": ("name", {}),
    "fabric_desktop_get_local_tables": ("project", {}),
    "fabric_desktop_get_local_table_history": ("table", {"project": None}),
    "fabric_desktop_get_lineage": ("entity", {}),
    "fabric_desktop_get_project_run": ("run_id", {}),
}


def test_real_read_only_tools_refuse_hostile_names_and_touch_nothing_outside(world):
    reg, svc, base = world["reg"], world["svc"], world["base"]
    keep = [p for p in base.iterdir() if p.name != "app"]
    before = {str(p): tree(p) for p in keep}
    sentinel = (world["outside"] / "sentinel.txt").read_bytes()
    wrong = []
    _OPENED.clear()
    _RECORDING[0] = True
    try:
        for cap_id, (field, others) in READS.items():
            for value in aimed_at(world):
                args = {k: (world["pid"] if k == "project" else v) for k, v in others.items()} | {field: value}
                try:
                    out = bounded(lambda cap_id=cap_id, args=args: reg.invoke(svc, cap_id, args, CallContext(Caller("t", "test"), timeout=30)), 30)
                except IntegrationError as err:
                    if err.code not in ("not_found", "invalid_input"):
                        wrong.append(f"{cap_id}.{field}={value[:40]!r}: {err.code} {err.message[:120]}")
                    continue
                if cap_id == "fabric_desktop_list_proofs":
                    assert out["items"] == [], (value, out)
                else:
                    wrong.append(f"{cap_id}.{field}={value[:40]!r}: answered {str(out)[:120]}")
    finally:
        _RECORDING[0] = False
    assert wrong == [], "hostile names not refused as not_found or invalid_input:\n" + "\n".join(wrong)
    assert (world["outside"] / "sentinel.txt").read_bytes() == sentinel
    assert {str(p): tree(p) for p in keep} == before, "something outside the app's data folder was created or changed"
    assert not any(base.glob("*passwd*")) and not any(base.rglob("*win.ini*"))
    assert any(under(p, world["roots"]) for p in _OPENED if p), "the audit hook saw the tools read their own files"
    strays = sorted({p for p in _OPENED if p and under(p, [base.resolve()]) and not under(p, world["roots"])})
    assert strays == [], f"read outside the data folders: {strays}"
    assert not [p for p in _OPENED if p and under(p, [world["outside"].resolve()])]
