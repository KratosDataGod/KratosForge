"""The examples in the AI integration docs run (#2363 hardening).

The pages an AI tool's author reads, docs/ai/*.md, docs/architecture/ai-integration.md and the
KratosForge skill, name tools, resource URIs and a curl call. Each is checked against the real
registry and the real local API, so a renamed tool, a moved resource or a changed endpoint fails
here instead of in someone's setup:

- every tool a page names is one KratosForge registers;
- every resource URI a page gives resolves to a tool, with arguments that tool accepts, and its
  placeholders filled with sample values;
- every kind of resource is in the architecture page's resource table;
- the curl call on the architecture page, sent as written (with a real token and port) to the real
  local API, answers ok, as does the OpenAPI document's example resource read.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import httpx2 as httpx

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import validate
from fabric_desktop.integrations.extensions import BUILT_IN, check as check_extension
from fabric_desktop.integrations.registry import Registry

from test_integrations_conformance import env, http_server  # noqa: F401 - fixtures

REPO = Path(__file__).resolve().parents[2]
PAGES = sorted((REPO / "docs" / "ai").glob("*.md")) + [REPO / "docs" / "architecture" / "ai-integration.md",
                                                     REPO / "deploy" / "ai" / "kratosforge" / "SKILL.md"]
ARCHITECTURE = REPO / "docs" / "architecture" / "ai-integration.md"
URI = re.compile(r"fabric-desktop://[A-Za-z0-9_\-./{}?=&]+")
SAMPLE = {"id": "metadata", "name": "nb", "workspace_id": "ws1", "item_id": "it1", "lakehouse_id": "lh1",
          "table": "orders", "entity": "silver.orders", "project": "p1"}


def registry() -> Registry:
    reg = Registry()
    register_core(reg)
    for ext in BUILT_IN:
        for cap in check_extension(ext):
            reg.register(cap)
    return reg


def text(page: Path) -> str:
    return page.read_text(encoding="utf-8")


def filled(uri: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: SAMPLE[m.group(1)], uri.rstrip("."))


def test_the_pages_exist():
    assert all(p.is_file() for p in PAGES) and len(PAGES) >= 6, PAGES


def test_every_tool_a_page_names_is_registered():
    ids = {cap.id for cap in registry().all()}
    unknown = [f"{p.relative_to(REPO)}: {name}" for p in PAGES
               for name in sorted(set(re.findall(r"fabric_desktop_[a-z_]+", text(p))) - ids)]
    assert unknown == [], "the docs name tools that don't exist:\n" + "\n".join(unknown)


def test_every_resource_uri_a_page_gives_resolves_with_arguments_its_tool_accepts():
    reg = registry()
    checked, wrong = 0, []
    for page in PAGES:
        for uri in sorted(set(URI.findall(text(page)))):
            if uri.endswith("/"):  # a prefix in prose ("under fabric-desktop://ext/<extension id>/")
                continue
            try:
                cap, args = reg.resolve(filled(uri))
                validate(cap.input_schema, args)
                checked += 1
            except Exception as exc:  # noqa: BLE001 - listed below
                wrong.append(f"{page.relative_to(REPO)}: {uri}: {exc}")
    assert wrong == [], "resource URIs in the docs that don't resolve:\n" + "\n".join(wrong)
    assert checked >= 30, checked


def test_every_kind_of_resource_is_in_the_architecture_pages_table():
    table = text(ARCHITECTURE)
    kinds = {cap.resource.split("://", 1)[1].split("/")[0] for cap in registry().resources()
             if not cap.resource.startswith("fabric-desktop://ext/")}
    missing = sorted(k for k in kinds if f"`fabric-desktop://{k}" not in table)
    assert missing == [], f"resources missing from the table in {ARCHITECTURE.relative_to(REPO)}: {missing}"


def curl_example() -> list[str]:
    block = re.search(r"```\w*\n(curl [^\n]+)\n```", text(ARCHITECTURE))
    assert block, "the architecture page has a curl example"
    return shlex.split(block.group(1))


def test_the_curl_example_runs_against_the_local_api(env, http_server):  # noqa: F811 - fixtures
    words = curl_example()
    headers = dict(words[i + 1].split(": ", 1) for i, w in enumerate(words) if w == "-H")
    method = words[words.index("-X") + 1]
    url = next(w for w in words if w.startswith("http://"))
    assert headers["Authorization"] == "Bearer fdt_..." and url.startswith("http://127.0.0.1:47615/")
    # The page's placeholders: the token the person got, and the port the server is on.
    headers["Authorization"] = f"Bearer {env['token']}"
    url = url.replace(":47615/", f":{http_server}/")
    env["service"].list_projects = lambda: {"items": [], "total": 0}  # no Fabric sign-in in a test
    res = httpx.request(method, url, headers=headers, timeout=30)
    out = res.json()
    assert res.status_code == 200 and out["ok"] is True and out["capability"] == url.split("/")[-2], out


def test_the_openapi_example_resource_reads(env, http_server):  # noqa: F811 - fixtures
    base = f"http://127.0.0.1:{http_server}"
    auth = {"Authorization": f"Bearer {env['token']}"}
    doc = httpx.get(f"{base}/api/v1/openapi.json", timeout=30).json()
    params = doc["paths"]["/api/v1/resources/read"]["get"]["parameters"]
    example = next(p["example"] for p in params if p["name"] == "uri")
    res = httpx.get(f"{base}/api/v1/resources/read", params={"uri": example}, headers=auth, timeout=30)
    assert res.status_code == 200 and res.json()["ok"] is True, (example, res.status_code, res.text[:500])
