"""What leaves through a tool never carries a secret, a traceback, or a path outside the data folders (#2363 hardening).

Secrets. Each tool's handler is replaced by one that answers with secrets in every place a result
can hold them: in text (every kind ``redact.secrets`` names: bearer and Basic credentials, JWTs,
KratosForge's own fdt_ and kfp_ tokens, SAS signatures, storage and Service Bus account keys,
passwords in connection strings, JSON and command lines, URL user info, provider keys, cookies,
OAuth codes, PEM private keys whole and cut off), as the value of fields named like secrets
(password, token, secret, connection_string, sas, api_key, ...) whether that value is text, a list
or an object, in object keys, in tuples, and in an object that isn't JSON. None of it may come out
of ``Registry.invoke``, MCP (the text content and the structured content, tools and resources) or
the local API, while counts and flags stay. Then each handler raises instead, with the same secrets
in the message (and, for an IntegrationError a handler raises itself, in the hint and details):
none may come out of any transport either, nor may a Python traceback.

Paths. Results may name the person's own folders (a metadata project's folder, a local data
project's source folders): those are what the tools are about. An error, though, names a file, not
the folders above it: a file error's message keeps only the file's name, so a missing notebook
doesn't tell a caller where the person's data folder (and their Windows user name) is. The real
notebook tools are checked for that on every transport, and the real read-only tools' results for
paths outside the folders they read.
"""

from __future__ import annotations

import dataclasses
import json
import ntpath
import posixpath
from pathlib import Path

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from fabric_desktop.integrations import peer
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CODES, CallContext, Caller, IntegrationError, error_from, redact_result
from fabric_desktop.integrations.http_server import RateLimiter, build_app
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_limits import PORT, AllowAll, valid_args
from test_integrations_tool_guards import spied

# (text holding a secret, the part of it that must never come out)
TEXT = [
    ("Authorization: Bearer Zq9bearerTOKENvalue123", "Zq9bearer"),
    ("Authorization: Basic Zq9basicQWxhZGRpbjpvcGVu", "Zq9basic"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJacTlqd3QifQ.Zq9jwtSIGNATUREabc", "Zq9jwtSIG"),  # pragma: allowlist secret
    ("fdt_Zq9fdtTOKENaaaaaaaaaaaaaaaaaaaa", "Zq9fdt"),
    ("kfp_Zq9kfpTOKENaaaaaaaaaaaaaaaaaaaa", "Zq9kfp"),
    ("https://acct.blob.core.windows.net/c/f.csv?sv=2022-11-02&ss=b&sig=Zq9sasSIG%2Babc%3D&se=2030-01-01", "Zq9sasSIG"),
    ("SharedAccessSignature sr=x&sig=Zq9sigTWO&se=1", "Zq9sigTWO"),
    ("DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=Zq9accountKEYabc+/==;EndpointSuffix=core.windows.net",
     "Zq9accountKEY"),
    ("Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=Zq9sharedKEY123=", "Zq9sharedKEY"),
    ("Server=x;User Id=u;Password=Zq9pwKV;", "Zq9pwKV"),
    ('{"password": "Zq9pwJSON two words"}', "Zq9pwJSON"),  # pragma: allowlist secret
    ("{'client_secret': 'Zq9pyREPR words'}", "Zq9pyREPR"),  # pragma: allowlist secret
    ("client_secret=Zq9clientSECRET", "Zq9clientSECRET"),
    ("api_key: Zq9apiKEY", "Zq9apiKEY"),
    ("access_token=Zq9accessTOK", "Zq9accessTOK"),
    ("x-auth-token: Zq9xauthTOK", "Zq9xauthTOK"),
    ("Ocp-Apim-Subscription-Key: Zq9apimKEY", "Zq9apimKEY"),
    ("https://user:Zq9urlTOKEN@dev.azure.com/org", "Zq9urlTOKEN"),  # pragma: allowlist secret
    ("ghp_Zq9ghaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "Zq9gh"),  # pragma: allowlist secret
    ("AKIAZQ9AWSKEY1234567", "ZQ9AWSKEY"),  # pragma: allowlist secret
    ("sk-Zq9skKEYaaaaaaaaaaaaaaaaaaa", "Zq9skKEY"),
    ("Cookie: session=Zq9cookieVAL; other=1", "Zq9cookieVAL"),
    ("--password Zq9cliPW", "Zq9cliPW"),
    ("https://localhost/cb?code=Zq9oauthCODE&state=1", "Zq9oauthCODE"),
    ("-----BEGIN PRIVATE KEY-----\nMIIZq9pemBODYabc\n-----END PRIVATE KEY-----", "Zq9pemBODY"),  # pragma: allowlist secret
    ("-----BEGIN RSA PRIVATE KEY-----\nZq9rsaBODY cut off before its end", "Zq9rsaBODY"),  # pragma: allowlist secret
]
# Fields whose value is a secret whatever it looks like: plain words no pattern would find.
KEYS = ["password", "Password", "passwd", "passphrase", "token", "access_token", "Refresh-Token", "secret",
        "client_secret", "connection_string", "ConnectionString", "sas", "sas_url", "blob_sas", "api_key", "Api-Key",
        "authorization", "subscription_key"]
MARKERS = [m for _, m in TEXT] + ["Zq9plain"]
TRACEBACK = ("Traceback (most recent call last)", 'File "')


class NotJson:
    """An object a handler might return by mistake: the transports write its str()."""

    def __str__(self):
        return "\n".join(t for t, _ in TEXT)


def stuffed() -> dict:
    texts = [t for t, _ in TEXT]
    out = {
        "text": "\n".join(texts),
        "lines": texts,
        "nested": {"a": [{"b": [t, {"c": t}]} for t in texts]},
        "tuple": tuple(texts[:4]),
        "keys": {t: 1 for t in texts},
        "not_json": NotJson(),
        "counts": {"secrets_ignored": 3, "token_count": 2, "password_set": True, "secret": None},
    }
    for i, key in enumerate(KEYS):
        out[key] = f"Zq9plain value {i}"
        out[f"list_{i}"] = {key: [f"Zq9plain list {i}", [f"Zq9plain inner {i}"]]}
        out[f"dict_{i}"] = {key: {"value": f"Zq9plain dict {i}", "more": [{"x": f"Zq9plain deep {i}"}]}}
    return out


def leaks(text: str) -> list[str]:
    return [m for m in MARKERS if m in text]


def tracebacks(text: str) -> list[str]:
    return [t for t in TRACEBACK if t in text]


def answering(make) -> Registry:
    """Every capability, with a handler made by ``make(cap)``, any object as output, and nothing asking first."""
    plain, _ = spied()
    reg = Registry(AllowAll())
    for cap in plain.all():
        reg.register(dataclasses.replace(cap, handler=make(cap), bind=None, output_schema={"type": "object"}))
    return reg


def ctx() -> CallContext:
    return CallContext(Caller("t", "test"), timeout=5)


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setattr(peer, "checked", lambda: False)


@pytest.fixture
def token(tmp_path):
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    store.set_setting("enabled", True)
    _, tok = store.add_client("Script", "other", transport="http")
    return store, tok


def rest_client(reg, store, factory=lambda: object()) -> TestClient:
    app = build_app(reg, store, PORT, factory, limiter=RateLimiter(rate=1e6, burst=10**6))
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000), raise_server_exceptions=False)


def rest(c: TestClient, cap_id: str, args: dict, tok: str):
    return c.post(f"/api/v1/capabilities/{cap_id}/invoke", json={"arguments": args},
                  headers={"Authorization": f"Bearer {tok}", "Host": f"127.0.0.1:{PORT}"})


# ------------------------------------------------------------------------- the claims themselves

@pytest.mark.parametrize("text, secret", TEXT, ids=[m for _, m in TEXT])
def test_each_kind_of_secret_in_text_is_removed(text, secret):
    out = redact_result({"t": text, "l": [text, (text,)], "d": {"x": {"y": text}}})
    assert not leaks(json.dumps(out)), out


def test_a_secret_in_an_object_key_is_removed():
    out = redact_result({t: [{t: 1}] for t, _ in TEXT})
    assert not leaks(json.dumps(out)), out


def test_everything_under_a_field_named_like_a_secret_is_blanked_and_counts_stay():
    out = redact_result(stuffed())
    assert not leaks(json.dumps(out)), [k for k in out if leaks(json.dumps(out[k]))]
    assert out["counts"] == {"secrets_ignored": 3, "token_count": 2, "password_set": True, "secret": None}
    assert out["password"] == "[removed]" and out["list_0"]["password"] == ["[removed]", ["[removed]"]]
    assert out["dict_0"]["password"] == {"value": "[removed]", "more": [{"x": "[removed]"}]}
    assert out["tuple"] == ["Authorization: Bearer [removed]", "Authorization: Basic [removed]", "[removed]", "[removed]"]
    assert isinstance(out["not_json"], str)


def test_a_private_key_on_the_same_line_as_a_cookie_header_is_removed():
    out = redact_result({"t": "Cookie: a=1 -----BEGIN PRIVATE KEY-----\nMIIZq9pemBODYabc\n-----END PRIVATE KEY-----"})
    assert "Zq9pemBODY" not in json.dumps(out), out


# ----------------------------------------------------------------------------- results, every tool

def test_no_secret_in_any_tools_result_through_the_registry():
    reg = answering(lambda cap: lambda svc, ctx, **a: stuffed())
    for cap in reg.all():
        out = reg.invoke(object(), cap.id, valid_args(cap), ctx())
        text = json.dumps(out, default=str, ensure_ascii=False)
        assert not leaks(text), (cap.id, leaks(text))
        assert out["counts"]["secrets_ignored"] == 3, cap.id


def test_no_secret_in_any_tools_result_over_mcp():
    reg = answering(lambda cap: lambda svc, ctx, **a: stuffed())

    async def go():
        async with Client(build_server(reg, lambda: object(), "cl_t")) as c:
            for cap in reg.all():
                out = await c.call_tool(cap.id, valid_args(cap))
                assert not out.is_error, (cap.id, out.content[0].text[:200])
                assert not leaks(out.content[0].text), (cap.id, leaks(out.content[0].text))
                assert not leaks(json.dumps(out.structured_content)), cap.id
                assert out.structured_content["counts"]["secrets_ignored"] == 3, cap.id
            uris = [str(r.uri) for r in (await c.list_resources()).resources]
            assert uris
            for uri in uris:
                body = (await c.read_resource(uri)).contents[0].text
                assert not leaks(body), (uri, leaks(body))
    anyio.run(go)


def test_no_secret_in_any_tools_result_over_the_local_api(token):
    store, tok = token
    reg = answering(lambda cap: lambda svc, ctx, **a: stuffed())
    c = rest_client(reg, store)
    for cap in reg.all():
        res = rest(c, cap.id, valid_args(cap), tok)
        assert res.status_code == 200, (cap.id, res.text[:200])
        assert not leaks(res.text), (cap.id, leaks(res.text))
        assert res.json()["result"]["counts"]["secrets_ignored"] == 3
    for cap in reg.resources():
        if not cap.resource_params:
            res = c.get("/api/v1/resources/read", params={"uri": cap.resource},
                        headers={"Authorization": f"Bearer {tok}", "Host": f"127.0.0.1:{PORT}"})
            assert res.status_code == 200 and not leaks(res.text), cap.resource


def test_a_result_nested_past_the_limit_is_refused_not_a_crash():
    def deep(svc, ctx, **a):
        v: object = "Zq9plain"
        for _ in range(10_000):
            v = {"password": v}
        return v
    reg = answering(lambda cap: deep)
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), "fabric_desktop_get_status", {}, ctx())
    assert err.value.code == "too_large" and err.value.hint


# ----------------------------------------------------------------------------------- errors

# One per line: a Cookie header runs to the end of its line.
SECRET_TEXT = "\n".join(t for t, _ in TEXT)


class FabricError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class GateError(Exception):
    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


def _deep_raise(n, exc):
    if n == 0:
        raise exc
    _deep_raise(n - 1, exc)


def raising():
    """(name, a function raising it): the exceptions a handler can end with, each carrying every secret."""
    def chained():
        try:
            raise KeyError(SECRET_TEXT)
        except KeyError as inner:
            raise RuntimeError(SECRET_TEXT) from inner
    return [
        ("RuntimeError", lambda: _deep_raise(30, RuntimeError(SECRET_TEXT))),
        ("ValueError", lambda: _deep_raise(5, ValueError(SECRET_TEXT))),
        ("KeyError", lambda: _deep_raise(5, KeyError(SECRET_TEXT))),
        ("PermissionError", lambda: _deep_raise(5, PermissionError(SECRET_TEXT))),
        ("TimeoutError", lambda: _deep_raise(5, TimeoutError(SECRET_TEXT))),
        ("unexpected", lambda: _deep_raise(5, ZeroDivisionError(SECRET_TEXT))),
        ("fabric", lambda: _deep_raise(5, FabricError(SECRET_TEXT, 401))),
        ("fabric_busy", lambda: _deep_raise(5, FabricError(SECRET_TEXT, 503))),
        ("gate", lambda: _deep_raise(5, GateError(SECRET_TEXT, [SECRET_TEXT, {"password": "Zq9plain gate"}]))),
        ("chained", chained),
        ("integration_error", lambda: _deep_raise(5, IntegrationError(
            "failed", SECRET_TEXT, hint=SECRET_TEXT,
            details={"why": [SECRET_TEXT], "password": "Zq9plain detail", "nested": {"token": ["Zq9plain list"]}}))),  # pragma: allowlist secret
        ("os_error", lambda: _deep_raise(5, OSError(13, SECRET_TEXT, "C:\\Users\\chris\\Zq9plain\\x.txt"))),
    ]


def error_registry(kind):
    raise_it = dict(raising())[kind]

    def handler(svc, ctx, **a):
        raise_it()
    return answering(lambda cap: handler)


KINDS = [name for name, _ in raising()]


def clean_error(err: dict, where: str) -> None:
    text = json.dumps(err, ensure_ascii=False)
    assert err["code"] in CODES, (where, err)
    assert not leaks(text), (where, leaks(text))
    assert not tracebacks(text), (where, text[:300])


@pytest.mark.parametrize("kind", KINDS)
def test_no_secret_or_traceback_in_any_tools_error_through_the_registry(kind):
    reg = error_registry(kind)
    for cap in reg.all():
        with pytest.raises(IntegrationError) as err:
            reg.invoke(object(), cap.id, valid_args(cap), ctx())
        clean_error(err.value.to_dict(), cap.id)
        assert not leaks(str(err.value)), cap.id


@pytest.mark.parametrize("kind", KINDS)
def test_no_secret_or_traceback_in_any_tools_error_over_mcp(kind):
    reg = error_registry(kind)

    async def go():
        async with Client(build_server(reg, lambda: object(), "cl_t")) as c:
            for cap in reg.all():
                out = await c.call_tool(cap.id, valid_args(cap))
                assert out.is_error, cap.id
                clean_error(out.structured_content["error"], cap.id)
                assert not leaks(out.content[0].text) and not tracebacks(out.content[0].text), cap.id
            for r in (await c.list_resources()).resources:
                with pytest.raises(Exception) as err:  # noqa: PT011 - the SDK's MCPError
                    await c.read_resource(str(r.uri))
                text = str(err.value) + json.dumps(getattr(getattr(err.value, "error", None), "data", None))
                assert not leaks(text) and not tracebacks(text), (str(r.uri), text[:300])
    anyio.run(go)


@pytest.mark.parametrize("kind", KINDS)
def test_no_secret_or_traceback_in_any_tools_error_over_the_local_api(token, kind):
    store, tok = token
    reg = error_registry(kind)
    c = rest_client(reg, store)
    for cap in reg.all():
        res = rest(c, cap.id, valid_args(cap), tok)
        assert res.status_code >= 400 and res.headers["content-type"].startswith("application/json"), (cap.id, res.text[:200])
        clean_error(res.json()["error"], cap.id)
        assert not leaks(res.text), cap.id


def test_a_refusal_that_echoes_the_callers_text_has_no_secret(token):
    """Refusals built before any handler runs: an unknown resource names its URI, which a caller can fill."""
    store, tok = token
    reg = answering(lambda cap: lambda svc, ctx, **a: {})
    uri = "fabric-desktop://nope/" + "Bearer Zq9bearerTOKENvalue123?x=1&sig=Zq9sasSIG"
    with pytest.raises(IntegrationError) as err:
        reg.resolve(uri)
    assert not leaks(json.dumps(error_from(err.value).to_dict()))
    c = rest_client(reg, store)
    res = c.get("/api/v1/resources/read", params={"uri": uri},
                headers={"Authorization": f"Bearer {tok}", "Host": f"127.0.0.1:{PORT}"})
    assert res.status_code == 404 and not leaks(res.text), res.text


# ------------------------------------------------------------------------------------- paths

HOME_PATHS = [
    OSError(2, "No such file or directory", "C:\\Users\\chris\\AppData\\Local\\KratosForge\\notebooks\\Sales\\nb.ipynb"),
    FileNotFoundError(2, "No such file or directory", "/home/chris/.local/share/kratosforge/notebooks/nb.ipynb"),
    PermissionError(13, "Permission denied", "\\\\server\\share\\chris\\nb.ipynb"),
    FileExistsError(17, "File exists", "C:\\Users\\chris\\a.ipynb", None, "C:\\Users\\chris\\nb.ipynb"),  # a rename's two paths
    IsADirectoryError(21, "Is a directory", b"/Users/chris/Library/nb.ipynb"),
]


@pytest.mark.parametrize("exc", HOME_PATHS, ids=[type(e).__name__ for e in HOME_PATHS])
def test_a_file_error_names_the_file_but_not_its_folders(exc):
    err = error_from(exc)
    assert "chris" not in err.message and "Users" not in err.message and "home" not in err.message, err.message
    assert "nb.ipynb" in err.message and err.code in CODES, err.message
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        assert err.code in ("not_found", "permission_denied") and err.message.startswith(exc.strerror), err.message


def test_a_file_error_over_every_transport_names_no_folder(token):
    store, tok = token

    def handler(svc, ctx, **a):
        raise FileNotFoundError(2, "No such file or directory", "C:\\Users\\chris\\AppData\\Local\\KratosForge\\nb.ipynb")
    reg = answering(lambda cap: handler)
    cap = reg.get("fabric_desktop_get_status")
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), cap.id, {}, ctx())
    texts = [json.dumps(err.value.to_dict())]

    async def go():
        async with Client(build_server(reg, lambda: object(), "cl_t")) as c:
            out = await c.call_tool(cap.id, {})
            texts.extend([out.content[0].text, json.dumps(out.structured_content)])
    anyio.run(go)
    texts.append(rest(rest_client(reg, store), cap.id, {}, tok).text)
    for text in texts:
        assert "chris" not in text and "AppData" not in text and "nb.ipynb" in text, text
        assert '"code": "not_found"' in text or '"code":"not_found"' in text or text.startswith("not_found"), text


@pytest.fixture
def engine(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    svc.notebooks.save("Good", [{"type": "code", "source": "x = 1"}])
    data = tmp_path / "data"
    data.mkdir()
    (data / "orders.csv").write_text("id,amount\n1,10\n", encoding="utf-8")
    pid = svc.local_create_project("Sales")["id"]
    svc.local_add_source(pid, str(data))
    svc.pid = pid
    yield svc
    svc.local_data.close_all()


NOTEBOOK_TOOLS = ("fabric_desktop_get_notebook", "fabric_desktop_get_notebook_dependencies",
                  "fabric_desktop_get_notebook_environment", "fabric_desktop_validate_notebook", "fabric_desktop_check_proof")


def test_a_missing_notebook_is_not_found_without_the_data_folder_on_every_transport(engine, token, tmp_path):
    store, tok = token
    reg = Registry()
    register_core(reg)
    folders = {str(tmp_path), str(tmp_path).replace("\\", "\\\\"), str(Path.home()), "notebooks" + ntpath.sep,
               "notebooks" + posixpath.sep}
    seen = []
    for cap_id in NOTEBOOK_TOOLS:
        for name in ("nope", "Sales/nope"):
            with pytest.raises(IntegrationError) as err:
                reg.invoke(engine, cap_id, {"name": name}, ctx())
            seen.append(("invoke", cap_id, err.value.code, json.dumps(err.value.to_dict())))

    async def go():
        async with Client(build_server(reg, lambda: engine, "cl_t")) as c:
            for cap_id in NOTEBOOK_TOOLS:
                out = await c.call_tool(cap_id, {"name": "nope"})
                seen.append(("mcp", cap_id, out.structured_content["error"]["code"],
                             out.content[0].text + json.dumps(out.structured_content)))
    anyio.run(go)
    c = rest_client(reg, store, lambda: engine)
    for cap_id in NOTEBOOK_TOOLS:
        res = rest(c, cap_id, {"name": "nope"}, tok)
        seen.append(("rest", cap_id, res.json()["error"]["code"], res.text))
    for route, cap_id, code, text in seen:
        assert code == "not_found", (route, cap_id, code, text[:300])
        assert not [f for f in folders if f in text], (route, cap_id, text[:300])
        assert "nope.ipynb" in text, (route, cap_id, text[:300])
        assert not tracebacks(text), (route, cap_id)


def absolute(value) -> list[str]:
    """Every string in ``value`` that is an absolute path, Windows or POSIX."""
    out, stack = [], [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str) and (ntpath.splitdrive(v)[0] or (v.startswith("/") and len(v) > 1)):
            out.append(v)
    return out


def test_real_read_only_results_name_no_path_outside_the_folders_they_read(engine, tmp_path):
    reg = Registry()
    register_core(reg)
    roots = [(tmp_path / d).resolve() for d in ("app", "data")]
    calls = [("fabric_desktop_list_projects", {}), ("fabric_desktop_get_project", {"id": "notebooks"}),
             ("fabric_desktop_list_notebooks", {}), ("fabric_desktop_get_notebook", {"name": "Good"}),
             ("fabric_desktop_list_local_projects", {}), ("fabric_desktop_get_local_tables", {"project": engine.pid}),
             ("fabric_desktop_get_status", {})]
    found = []
    for cap_id, args in calls:
        out = reg.invoke(engine, cap_id, args, ctx())
        for p in absolute(out):
            path = Path(p).resolve()
            if not any(path == r or r in path.parents for r in roots):
                found.append((cap_id, p))
    assert found == [], f"paths outside the data folders in results: {found}"


@pytest.mark.parametrize("text", [
    "can't read C:\\Users\\chris\\Documents\\Sales\\orders.csv",
    "can't read c:/users/chris/Documents/orders.csv",
    "Permission denied: /home/chris/.local/share/kratosforge/x",
    "missing /Users/chris/Library/kratosforge/x",
])
def test_an_engine_message_naming_a_home_folder_keeps_the_file_but_not_whose(text):
    """A message that isn't a file error (the engine's own words) can still name a file under the
    person's home folder: the file's name is kept, its folders (and so the user name) are not."""
    err = error_from(RuntimeError(text))
    last = text.replace("\\", "/").rsplit("/", 1)[1]
    assert "chris" not in err.message and err.message.endswith(last), err.message


def test_a_home_folder_path_inside_other_text_still_hides_whose():
    """A path the folder cut doesn't reach, joined to other text, still loses the user name."""
    err = error_from(RuntimeError("bad file=C:\\Users\\chris\\x.csv"))
    assert "chris" not in err.message, err.message
