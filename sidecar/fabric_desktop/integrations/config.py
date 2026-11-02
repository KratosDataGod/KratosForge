"""What a person pastes into their AI tool to connect it, and the launcher that command runs.

On a release install the engine lives in ``%LOCALAPPDATA%\\FabricDesktop\\engine\\<build>\\`` and
that folder changes with every update, so a tool can't be pointed at it. Instead the app keeps a
launcher with a fixed path, ``%LOCALAPPDATA%\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd``,
rewritten to point at the current engine each time the app's engine starts. ``kratosforge.cmd``
beside it does the same for the command line.

The snippets are examples in each tool's documented configuration format. Which of them has been
tried is recorded in docs/product/ai-integration-compatibility.md; none is a runtime integration
with that vendor, and every tool gets the same server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from .. import jsonfile

SERVER_NAME = "fabric-desktop"
TOOLS = {
    "claude-code": "Claude Code",
    "claude-desktop": "Claude Desktop",
    "codex": "OpenAI Codex CLI",
    "cursor": "Cursor",
    "vscode": "VS Code (GitHub Copilot agent mode)",
    "other-mcp": "Another MCP client",
    "http": "An HTTP client (REST API)",
}


def launcher_path(root: Path) -> Path:
    return Path(root) / "bin" / ("fabric-desktop-mcp.cmd" if os.name == "nt" else "fabric-desktop-mcp")


def cli_launcher_path(root: Path) -> Path:
    """The command line's launcher, ``kratosforge``, next to the MCP one (VS Code's Validate task runs it)."""
    return Path(root) / "bin" / ("kratosforge.cmd" if os.name == "nt" else "kratosforge")


def write_launcher(root: Path) -> Path:
    """(Re)write the launcher so it runs this engine's Python. Called when the app's engine starts
    and when integrations are switched on."""
    return _write_script(launcher_path(root), "mcp ")


def write_cli_launcher(root: Path) -> Path:
    """(Re)write ``kratosforge``, which runs any CLI command with this engine's Python."""
    return _write_script(cli_launcher_path(root), "")


def _script_text(path: Path, command: str, windows: bool = os.name == "nt") -> str:
    if windows:
        # Some AI tools start a server with only a short list of environment variables (Codex CLI
        # does), and without LOCALAPPDATA the engine would look for its data, the tool's id and the
        # sign-in somewhere else (service.data_dir, auth). The launcher sits in <data>\\bin, so when
        # the variable is missing it puts back the folder above <data>. A path cmd would expand is left out.
        base = str(path.parent.parent.parent)
        restore = "" if any(c in base for c in '%"!^') else f'if not defined LOCALAPPDATA set "LOCALAPPDATA={base}"\r\n'
        return f'@echo off\r\n{restore}"{sys.executable}" -m fabric_desktop.cli {command}%*\r\n'
    return f'#!/bin/sh\nexec "{sys.executable}" -m fabric_desktop.cli {command}"$@"\n'


def _write_script(path: Path, command: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _script_text(path, command)
    if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        jsonfile.replace(tmp, path)
        if os.name != "nt":
            path.chmod(0o755)
    return path


def snippets(client: dict[str, Any], command: str, http_url: str = "") -> dict[str, dict[str, str]]:
    """Configuration examples for ``client``, keyed by tool. Each has a label, where it goes and the text."""
    args = ["--client", client["id"]]
    entry = {"command": command, "args": args}
    out: dict[str, dict[str, str]] = {
        "claude-code": {"label": TOOLS["claude-code"], "where": "Run in a terminal",
                        "text": f'claude mcp add {SERVER_NAME} -- "{command}" ' + " ".join(args)},
        "claude-desktop": {"label": TOOLS["claude-desktop"], "where": "claude_desktop_config.json, under mcpServers",
                           "text": json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)},
        "codex": {"label": TOOLS["codex"], "where": "~/.codex/config.toml",
                  "text": f'[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n'},
        "cursor": {"label": TOOLS["cursor"], "where": ".cursor/mcp.json in your project, or ~/.cursor/mcp.json",
                   "text": json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)},
        "vscode": {"label": TOOLS["vscode"], "where": ".vscode/mcp.json in your workspace",
                   "text": json.dumps({"servers": {SERVER_NAME: {"type": "stdio", **entry}}}, indent=2)},
        "other-mcp": {"label": TOOLS["other-mcp"], "where": "Your tool's MCP server settings (stdio)",
                      "text": json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)},
    }
    if http_url:
        out["http"] = {"label": TOOLS["http"], "where": "Any HTTP client",
                       "text": f"Base URL: {http_url}/api/v1\nOpenAPI: {http_url}/api/v1/openapi.json\n"
                               f"MCP (Streamable HTTP): {http_url}/mcp\nHeader: Authorization: Bearer <the token shown once>"}
    return out
