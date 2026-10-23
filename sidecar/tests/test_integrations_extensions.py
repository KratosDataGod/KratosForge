"""Extensions (PR-AI-012): the registration interface, its checks, the person's switch, the IT
policy, the built-in sample, and connected MCP clients being told their lists changed."""

import json
import os
import sys
from dataclasses import replace

import anyio
import pytest
from mcp import Client, StdioServerParameters

from fabric_desktop.integrations import extensions
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.extensions import BUILT_IN, OUTLINE_ID, Extension, ExtensionHost, check
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

OUTLINE = "notebook_outline_get"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    root = tmp_path / "FabricDesktop"
    store = IntegrationStore.at(root)
    store.set_setting("enabled", True)
    client, _ = store.add_client("Tests", "other-mcp", grants=["READ_METADATA"])
    reg = Registry(PermissionAuthorizer(store, wait_seconds=0.2, poll=0.02))
    register_core(reg)
    service = Service(root=root)
    service.notebooks.save("Sales", [
        {"type": "markdown", "source": "# Sales load\n## Read the files\nnot a heading"},
        {"type": "code", "source": "import os\nTOKEN = 'abc'\ndef load(path):\n    return path\nclass Loader:\n    pass"},
        {"type": "code", "source": "%%sql\nselect * from sales"}])
    return {"tmp": tmp_path, "root": root, "store": store, "client": client, "registry": reg, "service": service}


def ctx(env):
    return CallContext(Caller(env["client"]["id"], "mcp-stdio", "Tests"))


def policy(env, **keys):
    path = env["tmp"] / "programdata" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys), encoding="utf-8")


def cap(cap_id="sample_thing_read", source="sample-thing", **changes):
    base = Capability(cap_id, "Read a thing", "Reads a thing.", schema(), schema(extra=True), "READ_METADATA",
                      lambda svc, ctx: {"ok": True}, source=source, needs_service=False)
    return replace(base, **changes)


def ext(caps=None, **changes):
    base = Extension("sample-thing", "Sample", "1.0.0", "Tests", "A sample.", ("READ_METADATA",),
                     lambda: caps if caps is not None else [cap()])
    return replace(base, **changes)


# ---- the checks


@pytest.mark.parametrize("change, message", [
    ({"id": "core"}, "not 'core'"),
    ({"id": "Bad Id"}, "lower case"),
    ({"id": "fabric_desktop.more"}, "fabric_desktop"),
    ({"version": "latest"}, "1.2.3"),
    ({"publisher": " "}, "publisher"),
    ({"permissions": ()}, "permissions"),
    ({"permissions": ("ROOT",)}, "permissions"),
])
def test_a_badly_formed_extension_is_refused(change, message):
    with pytest.raises(ValueError, match=message):
        check(ext(**change))


@pytest.mark.parametrize("caps, message", [
    ([], "at least one"),
    ([cap(source="other")], "source"),
    ([cap("other_thing_read")], "start with sample_thing_"),
    ([cap("fabric_desktop_get_status", source="sample-thing")], "start with sample_thing_"),
    ([cap(), cap()], "twice"),
    ([cap(permission="READ_DATA")], "doesn't declare"),
    ([cap(resource="fabric-desktop://status")], "go under fabric-desktop://ext/sample-thing/"),
])
def test_an_extension_keeps_to_what_it_declares(caps, message):
    with pytest.raises(ValueError, match=message):
        check(ext(caps))


def test_the_built_in_sample_passes_the_checks():
    assert [c.id for c in check(BUILT_IN[0])] == [OUTLINE]


def test_an_outside_extension_cant_take_a_built_in_id(env):
    host = ExtensionHost(env["registry"], env["store"])
    with pytest.raises(ValueError, match="built-in"):
        host.add(replace(BUILT_IN[0], built_in=False))


# ---- the person's switch and the policy


def test_extensions_are_off_until_the_person_turns_one_on(env):
    reg = env["registry"]
    host = ExtensionHost(reg, env["store"])
    host.sync()
    assert reg.get(OUTLINE) is None
    assert host.describe(OUTLINE_ID)["enabled"] is False

    before = reg.version
    row = host.set_enabled(OUTLINE_ID, True)
    assert row["enabled"] and row["loaded"] and reg.version > before
    assert reg.get(OUTLINE).source == OUTLINE_ID

    host.set_enabled(OUTLINE_ID, False)
    assert reg.get(OUTLINE) is None
    assert env["store"].settings()["extensions"] == {OUTLINE_ID: False}


def test_another_process_picks_up_the_switch(env):
    """The app's engine sets the switch; each MCP server process has its own registry and host."""
    app = ExtensionHost(Registry(), env["store"])
    server_registry = env["registry"]
    server = ExtensionHost(server_registry, env["store"])
    app.set_enabled(OUTLINE_ID, True)
    assert server_registry.get(OUTLINE) is None
    assert server.sync() is True and server_registry.get(OUTLINE) is not None
    assert server.sync() is False  # nothing new
    app.set_enabled(OUTLINE_ID, False)
    assert server.sync() is True and server_registry.get(OUTLINE) is None


def test_two_hosts_on_one_registry_agree_and_a_new_version_reloads(env):
    reg = env["registry"]
    first, second = ExtensionHost(reg, env["store"], catalog=[ext()]), ExtensionHost(reg, env["store"], catalog=[ext()])
    first.set_enabled("sample-thing", True)
    assert second.sync() is False and reg.get("sample_thing_read") is not None

    newer = ext([cap(title="Read a thing, v2")], version="1.1.0")
    second.add(newer)
    assert second.sync() is True and reg.get("sample_thing_read").title == "Read a thing, v2"


def test_the_policy_limits_which_extensions_can_be_turned_on(env):
    host = ExtensionHost(env["registry"], env["store"])
    host.set_enabled(OUTLINE_ID, True)

    policy(env, integrations_extensions=[])
    assert host.sync() is True and env["registry"].get(OUTLINE) is None  # already on: taken away
    with pytest.raises(PermissionError, match="organization"):
        host.set_enabled(OUTLINE_ID, True)
    assert host.describe(OUTLINE_ID)["allowed"] is False

    policy(env, integrations_extensions=[OUTLINE_ID])
    host.sync()
    assert env["registry"].get(OUTLINE) is not None


def test_an_unreadable_policy_allows_no_extensions(env):
    host = ExtensionHost(env["registry"], env["store"])
    policy(env, integrations_extensions="notebook-outline")
    with pytest.raises(PermissionError):
        host.set_enabled(OUTLINE_ID, True)


def test_registration_is_all_or_nothing(env):
    """The second capability breaks a registry rule (destructive with no approval): neither stays."""
    bad = ext([cap(), cap("sample_thing_wipe", read_only=False, destructive=True, permission="WRITE_LOCAL_FILES")],
              permissions=("READ_METADATA", "WRITE_LOCAL_FILES"))
    reg = env["registry"]
    host = ExtensionHost(reg, env["store"], catalog=[bad])
    with pytest.raises(ValueError, match="approval"):
        host.set_enabled("sample-thing", True)
    assert reg.get("sample_thing_read") is None and reg.get("sample_thing_wipe") is None
    assert "approval" in host.describe("sample-thing")["error"]


# ---- calls go through the same checks


def test_an_extension_capability_is_called_through_invoke_with_its_permission_and_audit(env):
    reg = env["registry"]
    reg.audit = IntegrationAudit(env["store"]).record
    ExtensionHost(reg, env["store"]).set_enabled(OUTLINE_ID, True)

    out = reg.invoke(env["service"], OUTLINE, {"name": "Sales"}, ctx(env))
    markdown, code, sql = out["cells"]
    assert markdown["headings"] == [{"level": 1, "text": "Sales load"}, {"level": 2, "text": "Read the files"}]
    assert code["functions"] == ["load"] and code["classes"] == ["Loader"]
    assert sql["functions"] == [] and sql["lines"] == 2
    assert "abc" not in json.dumps(out) and "source" not in json.dumps(out)  # names only

    with pytest.raises(IntegrationError) as missing:
        reg.invoke(env["service"], OUTLINE, {"name": "Nope"}, ctx(env))
    assert missing.value.code == "not_found"

    env["store"].update_client(env["client"]["id"], grants=[])
    with pytest.raises(IntegrationError) as refused:
        reg.invoke(env["service"], OUTLINE, {"name": "Sales"}, ctx(env))
    assert refused.value.code == "permission_denied"

    lines = IntegrationAudit(env["store"]).recent()
    assert [(l["capability"], l["decision"]) for l in lines][:3] == [
        (OUTLINE, "denied"), (OUTLINE, "allowed"), (OUTLINE, "allowed")]


def test_the_sample_is_also_a_resource(env):
    reg = env["registry"]
    ExtensionHost(reg, env["store"]).set_enabled(OUTLINE_ID, True)
    found, args = reg.resolve("fabric-desktop://ext/notebook-outline/notebooks/Sales")
    assert found.id == OUTLINE and args == {"name": "Sales"}


# ---- connected clients are told


def collect(into):
    async def handler(message):
        if not isinstance(message, Exception):
            into.append(type(message).__name__)
    return handler


def test_legacy_clients_get_list_changed_notifications(env):
    reg = env["registry"]
    host = ExtensionHost(reg, env["store"])
    server = build_server(reg, lambda: env["service"], env["client"]["id"], refresh=host.sync, poll=0.05)
    seen = []

    async def main():
        async with Client(server, mode="legacy", message_handler=collect(seen)) as c:
            caps = c.server_capabilities
            assert caps.tools.list_changed and caps.resources.list_changed
            assert OUTLINE not in [t.name for t in (await c.list_tools()).tools]
            await anyio.to_thread.run_sync(lambda: ExtensionHost(Registry(), env["store"]).set_enabled(OUTLINE_ID, True))
            with anyio.fail_after(5):
                while "ToolListChangedNotification" not in seen:
                    await anyio.sleep(0.02)
            assert OUTLINE in [t.name for t in (await c.list_tools()).tools]
            assert "ResourceListChangedNotification" in seen
            templates = (await c.list_resource_templates()).resource_templates
            assert "fabric-desktop://ext/notebook-outline/notebooks/{name}" in [t.uri_template for t in templates]

    anyio.run(main)


def test_2026_clients_get_list_changes_on_their_listen_stream(env):
    reg = env["registry"]
    host = ExtensionHost(reg, env["store"])
    server = build_server(reg, lambda: env["service"], env["client"]["id"], refresh=host.sync, poll=0.05)

    async def main():
        async with Client(server, mode="auto") as c:
            assert c.protocol_version == "2026-07-28"
            assert c.server_capabilities.tools.list_changed
            async with c.listen(tools_list_changed=True, resources_list_changed=True) as sub:
                await anyio.to_thread.run_sync(lambda: ExtensionHost(Registry(), env["store"]).set_enabled(OUTLINE_ID, True))
                kinds = set()
                with anyio.fail_after(5):
                    async for event in sub:
                        kinds.add(type(event).__name__)
                        if kinds >= {"ToolsListChanged", "ResourcesListChanged"}:
                            break
            assert OUTLINE in [t.name for t in (await c.list_tools()).tools]

    anyio.run(main)


def test_the_stdio_server_process_follows_the_switch_in_the_app(env):
    """The real ``fabric-desktop mcp`` process: the person turns the extension on in the app (here,
    the store), and the connected tool is told and sees the new tool."""
    process_env = dict(os.environ, LOCALAPPDATA=str(env["tmp"]), HOME=str(env["tmp"]),
                       ProgramData=str(env["tmp"] / "programdata"), PYTHONPATH=os.pathsep.join(sys.path))
    params = StdioServerParameters(command=sys.executable, env=process_env,
                                   args=["-m", "fabric_desktop.cli", "mcp", "--client", env["client"]["id"]])
    seen = []

    async def main():
        async with Client(params, mode="legacy", message_handler=collect(seen)) as c:
            assert OUTLINE not in [t.name for t in (await c.list_tools()).tools]
            await anyio.to_thread.run_sync(lambda: ExtensionHost(Registry(), env["store"]).set_enabled(OUTLINE_ID, True))
            with anyio.fail_after(20):
                while "ToolListChangedNotification" not in seen:
                    await anyio.sleep(0.1)
            result = await c.call_tool(OUTLINE, {"name": "Sales"})
            assert not result.is_error, result
            assert result.structured_content["cells"][1]["functions"] == ["load"]

    anyio.run(main)


def test_the_store_default_has_no_extensions_on(tmp_path):
    assert IntegrationStore.at(tmp_path).settings()["extensions"] == {}
    assert extensions.SETTING == "extensions"


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_the_local_api_tells_http_clients_too(env, mode):
    import socket
    import threading
    import time

    import httpx2
    import uvicorn
    from mcp.client.streamable_http import streamable_http_client

    from fabric_desktop.integrations.http_server import build_app

    _, token = env["store"].add_client("HTTP tool", "other-mcp", transport="http", grants=["READ_METADATA"])
    host = ExtensionHost(env["registry"], env["store"])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_app(env["registry"], env["store"], port, lambda: env["service"], refresh=host.sync)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    seen = []

    async def main():
        target = streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=30))
        async with Client(target, mode=mode, message_handler=collect(seen)) as c:
            await c.list_tools()
            switch = lambda: ExtensionHost(Registry(), env["store"]).set_enabled(OUTLINE_ID, True)  # noqa: E731
            if mode == "auto":
                async with c.listen(tools_list_changed=True) as sub:
                    await anyio.to_thread.run_sync(switch)
                    with anyio.fail_after(10):
                        assert type(await sub.__anext__()).__name__ == "ToolsListChanged"
            else:
                await anyio.to_thread.run_sync(switch)
                with anyio.fail_after(10):
                    while "ToolListChangedNotification" not in seen:
                        await anyio.sleep(0.05)
            assert OUTLINE in [t.name for t in (await c.list_tools()).tools]

    try:
        anyio.run(main)
    finally:
        server.should_exit = True
        thread.join(10)
