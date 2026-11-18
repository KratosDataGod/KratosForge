"""docs/ai/tools.md lists every MCP tool, so a new tool always gets a docs entry."""

import contextlib
import importlib.util
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "ai" / "tools.md"
_spec = importlib.util.spec_from_file_location("mcp_tools_doc", ROOT / ".github" / "scripts" / "mcp_tools_doc.py")
mcp_tools_doc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_tools_doc)


def test_the_committed_tools_page_is_what_the_generator_writes():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        mcp_tools_doc.main()
    assert out.getvalue() == DOC.read_text(encoding="utf-8"), (
        "docs/ai/tools.md is out of date: " + mcp_tools_doc.REGENERATE)


def test_every_capability_is_on_the_tools_page():
    doc = DOC.read_text(encoding="utf-8")
    missing = [c.id for c in mcp_tools_doc.registry().all() if f"`{c.id}`" not in doc]
    assert not missing, f"docs/ai/tools.md has no entry for {missing}: {mcp_tools_doc.REGENERATE}"
