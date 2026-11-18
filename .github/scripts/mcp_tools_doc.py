"""Write docs/ai/tools.md: every tool a connected AI tool can use, from the capability registry.

Run from sidecar/:  python ../.github/scripts/mcp_tools_doc.py > ../docs/ai/tools.md

sidecar/tests/test_mcp_tools_doc.py fails when the committed page is not what this writes, so a new
tool always gets a docs entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from fabric_desktop.integrations.capabilities import register_core  # noqa: E402
from fabric_desktop.integrations.extensions import BUILT_IN, check  # noqa: E402
from fabric_desktop.integrations.registry import Capability, Registry  # noqa: E402

REGENERATE = "from sidecar/, run python ../.github/scripts/mcp_tools_doc.py > ../docs/ai/tools.md"
ASKS = {"never": "No", "always": "Every time", "ask": "Unless pre-approved"}
WHERE = {"this_pc": "This PC", "fabric": "Fabric"}

INTRO = f"""# Tools an AI tool can use

<!-- Generated from the capability registry. Do not edit by hand: {REGENERATE} -->

These are the tools a connected AI tool can use in KratosForge. This page is generated from the
capability registry, so it lists every tool there is. Your AI tool can also get the live list, with
each tool's inputs and outputs, from the `fabric_desktop_list_capabilities` tool. Tools marked
**Preview** say so when your tool asks for them.

- **Permission** is what you give the tool when you add it in **Settings > Integrations**.
- **Asks you** says whether KratosForge asks you to approve each call.
- **Where** is where the tool reads or changes things: this PC, Fabric, or both.
- **Resource** is the address the tool is also served at as an MCP resource.
"""


def registry() -> Registry:
    reg = Registry()
    register_core(reg)
    for ext in BUILT_IN:  # extensions' capabilities are capabilities like any other
        for cap in check(ext):
            reg.register(cap)
    return reg


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def row(c: Capability) -> list[str]:
    asks = "Every time" if c.destructive else ASKS.get(c.approval, c.approval)
    return [
        f"`{c.id}`",
        _cell(c.title),
        _cell(c.permission),
        asks,
        "Reads" if c.read_only else "Changes",
        ", ".join(WHERE.get(loc, loc) for loc in c.locations),
        "Preview" if c.status == "preview" else "",
        f"`{_cell(c.resource)}`" if c.resource else "",
    ]


def main() -> None:
    head = ["Tool", "What it does", "Permission", "Asks you", "Reads or changes", "Where", "Status", "Resource"]
    lines = [INTRO, "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for c in sorted(registry().all(), key=lambda c: c.id):
        lines.append("| " + " | ".join(row(c)) + " |")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
