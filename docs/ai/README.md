# Connect your AI tool

KratosForge is **AI-ready, not AI-dependent**. It has no AI of its own: no chat, no model, no AI key or subscription. AI tools you already use (Claude Code, Claude Desktop, Codex, Cursor, VS Code with GitHub Copilot, or any MCP client) can connect to it and use the same features you do, with the permissions you give them. Nothing is sent to an AI service by KratosForge; your tool asks it for things, and you can switch all of it off. Everything here is **Preview**.

| Page | For |
| --- | --- |
| This page | Connecting a tool, step by step, with the setup for each one |
| [Workflows](workflows.md) | Step-by-step workflows your tool can follow (also served as MCP prompts) |
| [Instructions for your AI tool](agent-instructions.md) | Text to paste into `AGENTS.md`, `CLAUDE.md`, `.cursorrules` or `.github/copilot-instructions.md` |
| [Use with Microsoft's Fabric skills](microsoft-fabric-skills.md) | The KratosForge skill for Claude Code and GitHub Copilot CLI, and how it works beside Microsoft's Fabric skills |
| [Architecture](../architecture/ai-integration.md), [security](../product/ai-integration-security.md), [compatibility](../product/ai-integration-compatibility.md) | Developers and IT |

## Which tools have been tried

Only the MCP Python SDK client and an HTTP client are tested, automatically on every build. Claude Code is **Partly tested**: most of the checklist passed on Linux without a Fabric sign-in, and it hasn't been run on Windows yet. Codex CLI reads its setup below, but no steps have been run with it. The other tools are **Untested**: each setup follows that tool's documented MCP configuration format, but nobody has run it with that tool yet. The [compatibility page](../product/ai-integration-compatibility.md) is kept up to date; if you try one, open an issue with the tool, its version and what happened.

## Connect a tool

1. Open **Settings > Integrations** and turn on **Let AI tools connect**. It's off until you do, and your organization's policy can keep it off.
2. **Add a tool**: pick which one, give it a name, and choose what it may do. **Read metadata** (names, structure, schemas, checks; no rows) is always included. **Read data** reads rows from tables, a few at a time. **Run code on this PC** runs SQL, Python and notebooks, and asks you every time.
3. **Copy setup** and paste it into your tool as shown below. Your setup has your tool's own id in place of `YOUR-CLIENT-ID` and your own path in place of `C:\Users\you\...`. The command's path stays the same when KratosForge updates.
4. **Test connection**, then ask your tool something like "What lakehouses can you see in KratosForge?".

KratosForge has to be open while you use it: requests that need your approval appear there, over whatever page you're on. **Allow once** lets that one request through; **Deny** refuses it. Your tool can't approve anything itself.

## Setup for each tool

### Claude Code

Run this once in a terminal. Status: **Partly tested** ([what was run](../product/ai-integration-compatibility.md#checklist-runs)).

```text
claude mcp add fabric-desktop -- "C:\Users\you\AppData\Local\FabricDesktop\bin\fabric-desktop-mcp.cmd" --client YOUR-CLIENT-ID
```

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings > Developer > Edit config), merging it with any servers already there, then restart Claude Desktop. Status: **Untested**.

```json
{
  "mcpServers": {
    "fabric-desktop": {
      "command": "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd",
      "args": [
        "--client",
        "YOUR-CLIENT-ID"
      ]
    }
  }
}
```

### OpenAI Codex CLI

Add this to `~/.codex/config.toml`. Status: **Untested**.

```toml
[mcp_servers.fabric-desktop]
command = "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd"
args = ["--client", "YOUR-CLIENT-ID"]
```

### Cursor

Add this to `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for every project. Status: **Untested**.

```json
{
  "mcpServers": {
    "fabric-desktop": {
      "command": "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd",
      "args": [
        "--client",
        "YOUR-CLIENT-ID"
      ]
    }
  }
}
```

### VS Code (GitHub Copilot agent mode)

After **Add a tool** with **VS Code** picked, select **Add to VS Code** (or **VS Code Insiders**). VS Code opens, shows you the KratosForge server and asks before it adds it to your user settings. Then open Copilot Chat in **Agent** mode. The button is also under **Edit** for a VS Code tool you added before. Status: **Untested**.

To set it up for one workspace instead, add this to `.vscode/mcp.json` in your workspace, then start the server from the file and use Copilot in agent mode. Status: **Untested**.

```json
{
  "servers": {
    "fabric-desktop": {
      "type": "stdio",
      "command": "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd",
      "args": [
        "--client",
        "YOUR-CLIENT-ID"
      ]
    }
  }
}
```

### Another MCP client

Any tool that starts MCP servers over stdio. Most use this shape; check your tool's documentation for where it goes. Status: **Untested** for any specific tool.

```json
{
  "mcpServers": {
    "fabric-desktop": {
      "command": "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd",
      "args": [
        "--client",
        "YOUR-CLIENT-ID"
      ]
    }
  }
}
```

### An HTTP client (REST API)

For scripts and tools that call HTTP instead of starting a command. Turn on **Local HTTP API** under **How tools connect**; it listens on this PC only (`127.0.0.1`). Each tool gets its own token, shown once when you add the tool (**Edit > New token** makes another). Status: **Tested** with httpx and the MCP Python SDK.

```text
Base URL: http://127.0.0.1:47615/api/v1
OpenAPI: http://127.0.0.1:47615/api/v1/openapi.json
MCP (Streamable HTTP): http://127.0.0.1:47615/mcp
Header: Authorization: Bearer <the token shown once>
```

Use `127.0.0.1` in the address, not `localhost`: on Windows, `localhost` tries the IPv6 address `::1` first, which the API doesn't listen on, so on a PC other people also sign in to, another account's program could answer there. On Windows the API answers only programs running as your own Windows account. Run the tool the way KratosForge runs, not as administrator: Windows may not say who runs an elevated program, and the API then refuses it.

## What a connected tool can and can't do

| It can, with the permission you gave it | It can't, whatever you give it |
| --- | --- |
| List workspaces, lakehouses, tables, notebooks and projects, and read schemas and checks | Get your sign-in tokens or passwords |
| Read rows, a few at a time (**Read data**) | Write tables through KratosForge, or change Fabric items |
| Run read-only SQL, and Python and notebooks on this PC, each after you approve it (**Run code on this PC**) | Use Git, or change settings and policies |
| Read Prove results and receipts, and with your approval compare tables or export a receipt | Read local files through SQL, or run anything without your approval |
| List an environment's semantic models, export one as Apache Ossie YAML, and preview what an Ossie import would change (Preview; importing stays in KratosForge, behind its confirm) | |

Code a tool runs gets its own process that's ended afterwards; it can read the attached lakehouse but table writes through KratosForge are refused. Approved Python runs as you and could still write files with the storage access it has, so read it before you allow it: the approval shows it in full. A SQL query a tool runs uses the memory share and threads set for the SQL page (**Settings › Advanced › SQL queries**), and is stopped when its time limit (`timeout_seconds`, 2 minutes unless the tool asks otherwise) runs out or the tool cancels it; the tool gets a timeout error and its next query can start straight away. One tool query runs at a time; another waits for it, and the wait counts towards its time limit. Text the tool reads from notebooks, tables and files is treated as data: it can't approve anything. Every request is recorded under **Recent activity**, and in an audit trail your IT team can collect (`%LOCALAPPDATA%\FabricDesktop\integrations\audit`).

These limits apply to what your tool does through KratosForge. A tool that can already run commands on your PC (as most coding agents can) can do anything you can, with or without this connection; see [security](../product/ai-integration-security.md#what-it-does-not-protect-against).

## If it doesn't work

| What you see | What to do |
| --- | --- |
| The tool says the server failed to start | Check the path in your setup is the one **Copy setup** gave you, and that KratosForge has started once since installing. |
| "External AI tools are turned off" | Turn on **Let AI tools connect**, or ask your IT team if it's locked by policy. |
| "This tool isn't connected" or "was disconnected" | The client id in your setup doesn't match a tool in **Connected tools**, or the tool was revoked. Add it again and copy the new setup. |
| No approval shows up | Open KratosForge; approvals only appear while it's open. They're dropped after 5 minutes. |
| HTTP calls get 401 | Send `Authorization: Bearer <token>`; make a new token under **Edit** if you've lost it. |
| HTTP calls get 403 | Call `127.0.0.1`, not a host name, and don't send an `Origin` header (browsers can't use the API). |
