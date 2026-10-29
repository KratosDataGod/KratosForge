"""Workflow prompts (integrations/prompts.py): served as MCP prompts and kept the same as
docs/ai/workflows.md; every tool a prompt names exists."""

import re
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from fabric_desktop.integrations import prompts
from fabric_desktop.integrations.config import snippets
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.registry import default_registry
from fabric_desktop.service import Service

DOCS = Path(__file__).resolve().parents[2] / "docs" / "ai"


def sample(p):
    return {a: f"<{a}>" for a, _, _ in p.arguments}


def test_every_prompt_renders_with_the_ground_rules_and_its_arguments():
    names = [p.name for p in prompts.PROMPTS]
    assert len(names) == len(set(names))
    for p in prompts.PROMPTS:
        text = p.render(sample(p))
        assert text.startswith(prompts.GROUND_RULES) and p.title in text
        for a, _, _ in p.arguments:
            assert f"<{a}>" in text
        assert not re.search(r"\{[a-z_]+\}", text), p.name


def test_render_refuses_missing_and_unknown_arguments_and_caps_values():
    p = prompts.get("answer_with_sql")
    with pytest.raises(ValueError, match="needs question"):
        p.render({"workspace_id": "w", "lakehouse_id": "l", "question": "  "})
    with pytest.raises(ValueError, match="doesn't take extra"):
        p.render({"workspace_id": "w", "lakehouse_id": "l", "question": "q", "extra": "x"})
    long = p.render({"workspace_id": "w", "lakehouse_id": "l", "question": "x" * 5000})
    assert "x" * 500 in long and "x" * 501 not in long
    assert prompts.get("nope") is None


def test_prompts_only_name_tools_that_exist():
    tools = {c.id for c in default_registry().all()}
    for text in [prompts.GROUND_RULES, *(p.steps for p in prompts.PROMPTS)]:
        for name in re.findall(r"fabric_desktop_[a-z_]+", text):
            assert name in tools, name


def test_prompts_never_steer_toward_actions_tools_cannot_take():
    # Writes, Fabric changes and Git aren't offered; prompts must not suggest them.
    for p in prompts.PROMPTS:
        for word in ("start_proof", "delete_proof_outputs", "git ", "commit", "push"):
            assert word not in p.steps.lower(), (p.name, word)


def test_mcp_lists_and_gets_prompts(tmp_path):
    svc = Service(root=tmp_path)

    async def go():
        async with Client(build_server(default_registry(), lambda: svc, "cl_1")) as c:
            listed = {p.name: p for p in (await c.list_prompts()).prompts}
            assert set(listed) == {p.name for p in prompts.PROMPTS}
            sql = listed["answer_with_sql"]
            assert [(a.name, a.required) for a in sql.arguments] == [
                ("workspace_id", True), ("lakehouse_id", True), ("question", True)]
            got = await c.get_prompt("review_notebook", {"name": "nb_sales"})
            assert got.messages[0].role == "user"
            text = got.messages[0].content.text
            assert text == prompts.get("review_notebook").render({"name": "nb_sales"})
            with pytest.raises(MCPError, match="needs name"):
                await c.get_prompt("review_notebook", {})
            with pytest.raises(MCPError, match="doesn't take"):
                await c.get_prompt("check_project", {"run": "everything"})
            with pytest.raises(MCPError, match="no prompt"):
                await c.get_prompt("delete_everything", {})
    anyio.run(go)


def test_workflows_page_matches_the_prompts():
    page = (DOCS / "workflows.md").read_text(encoding="utf-8")
    assert prompts.GROUND_RULES in page
    for p in prompts.PROMPTS:
        assert f"MCP prompt `{p.name}`" in page and p.steps in page and p.title in page
        for a, desc, _ in p.arguments:
            assert f"`{a}`" in page and desc in page


def test_connect_page_carries_every_setup_from_the_app():
    page = (DOCS / "README.md").read_text(encoding="utf-8")
    cmd = r"C:\Users\you\AppData\Local\FabricDesktop\bin\fabric-desktop-mcp.cmd"
    for key, s in snippets({"id": "YOUR-CLIENT-ID"}, cmd, "http://127.0.0.1:47615").items():
        assert s["text"].rstrip() in page, key
        assert f"### {s['label']}" in page, key


def test_agent_instructions_name_every_prompt_and_only_real_tools():
    page = (DOCS / "agent-instructions.md").read_text(encoding="utf-8")
    for p in prompts.PROMPTS:
        assert p.name in page
    tools = {c.id for c in default_registry().all()}
    for name in re.findall(r"fabric_desktop_[a-z_]+", page):
        assert name in tools, name


def test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page():
    skill = (DOCS.parents[1] / "deploy" / "ai" / "kratosforge" / "SKILL.md").read_text(encoding="utf-8")
    head = re.match(r"---\nname: kratosforge\ndescription: \"([^\"\n]+)\"\n---\n", skill)
    assert head and len(head.group(1)) <= 1024
    tools = {c.id for c in default_registry().all()}
    for name in re.findall(r"fabric_desktop_[a-z_]+", skill):
        assert name in tools, name
    for p in prompts.PROMPTS:
        assert f"`{p.name}`" in skill, p.name
    assert "```" not in skill
    page = (DOCS / "microsoft-fabric-skills.md").read_text(encoding="utf-8")
    assert re.search(r"## The skill\n.*?```markdown\n(.*?)```", page, re.S).group(1) == skill
