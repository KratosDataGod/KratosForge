# AI integration compatibility

Which external tools have been tried with KratosForge, how, and what didn't work. Only rows marked **Tested** are claimed to work. Architecture: [ai-integration.md](../architecture/ai-integration.md).

## Protocol

| Item | Supported |
| --- | --- |
| MCP protocol versions | 2025-06-18 and 2025-11-25 (handshake era) and 2026-07-28, negotiated by the MCP Python SDK |
| Transports | stdio (default); Streamable HTTP on `127.0.0.1` when switched on |
| Server features | Tools, resources and resource templates, prompts, tool and resource list-changed notifications (on the connection at 2025-11-25, on `subscriptions/listen` at 2026-07-28), cancellation |
| Not supported | SSE transport (deprecated by MCP), remote (non-loopback) HTTP, OAuth authorization server, progress notifications |

## Clients

| Client | Transport | Status | Notes |
| --- | --- | --- | --- |
| MCP Python SDK client 2.3.0 | stdio, in-memory, Streamable HTTP | **Tested** (automated, every build) | Protocol 2025-11-25 and 2026-07-28; over HTTP with a bearer token against a real server on `127.0.0.1` |
| Claude Code 2.1.314 | stdio | **Partly tested** (by hand, 2026-10-30) | On Linux, not yet on Windows, with no Fabric sign-in. Steps 1, 2, 5, 7, 8, 10, 11, 13 and 14 passed, and step 6 with Python. Steps 3, 4 and 9, and step 6 with SQL, need a Fabric sign-in. Step 12 needs someone at the keyboard. See [checklist runs](#checklist-runs) |
| Claude Desktop | stdio | Untested | |
| OpenAI Codex CLI 0.166.0 | stdio | Untested; setup checked (2026-10-30) | Codex reads the setup (`codex mcp get fabric-desktop` shows it, enabled) and starts the server before any sign-in. The steps need an OpenAI account. See [checklist runs](#checklist-runs) |
| Cursor | stdio | Untested | |
| VS Code with GitHub Copilot (agent mode) | stdio | Untested | |
| httpx (Python) | REST `/api/v1` | **Tested** (automated, every build) | OpenAPI 3.1 at `/api/v1/openapi.json`; any HTTP client should work but only this one is tested |
| curl 8.5.0 | REST `/api/v1` | **Partly tested** (by hand, 2026-10-30) | Steps 2, 5, 7, 8 and 13, step 6 with Python, and 401 without the token passed. Steps 3, 4 and 9 need a Fabric sign-in. See [checklist runs](#checklist-runs) |

To test a tool, follow the [AI tool test checklist](ai-integration-client-checklist.md) and record the result here. **Partly tested** means some steps were run and passed, and the rest are listed as not run yet, with why.

Untested means a configuration example exists ([setup for each tool](../ai/README.md)) but nobody has run it with that client yet. MCP prompts ([workflows](../ai/workflows.md)) are tested with the SDK client only; whether a client shows them, and how, is part of each client's test. To report a result, open an issue with the client name and version, the transport, and what happened.

## Conformance

Every build runs the same protocol checks through all three ways a tool can connect (in memory, the stdio process, Streamable HTTP) on both protocol eras, and checks the REST API against its OpenAPI document: `sidecar/tests/test_integrations_conformance.py`. The MCP project's own suite (`@modelcontextprotocol/conformance` 0.1.16) was run by hand on 2026-10-17: every scenario that applies to this server passed except one that is stricter on purpose (requests with any `Origin` header are refused). Details, and how to run it again, are in the [checklist](ai-integration-client-checklist.md#the-mcp-conformance-suite).

| Not supported | Why |
| --- | --- |
| Sampling and elicitation (the server asking the tool's model or user for something) | The server never asks an AI model for anything, and approvals happen in KratosForge, not in the tool |
| `logging/setLevel`, `completion/complete`, resource subscriptions | Not needed by any workflow yet; list-changed notifications cover capability changes |
| Requests from web pages (any `Origin` header) | Only programs on this PC may call the local API |

## Checklist runs

### 2026-10-30: Claude Code, Codex CLI and curl (PB-096)

Run on Linux from `main` at 6968f2f8 (KratosForge 0.20.0), with a fresh data folder and no Fabric sign-in, so nothing in a workspace could be read. The tool was set up from **Copy setup**'s text for that tool. Claude Code ran without a person at the keyboard (`claude -p`, with only the `fabric-desktop` tools allowed). Approvals were answered by a script standing in for the app (the same call **Allow once** and **Deny** make), so the app's approval dialog itself wasn't part of this run. The steps are the [checklist's](ai-integration-client-checklist.md#steps).

| Step | Claude Code 2.1.314 (stdio) | curl 8.5.0 (local HTTP API) |
| --- | --- | --- |
| 1 Listed and connected | Pass: `claude mcp list` says **Connected**. The app's **Test connection** wasn't part of this run | Not a step for HTTP |
| 2 What can you do | Pass: it called `get_status` and `list_capabilities` and listed what only reads | Pass |
| 3 Lakehouses and tables | Needs a Fabric sign-in. It passed on the server's answer, that the sign-in had expired, and didn't retry | Needs a Fabric sign-in. The same answer came back as an error (422) |
| 4 A table's columns | Needs a Fabric sign-in | Needs a Fabric sign-in |
| 5 Rows without **Read data** | Pass: no tool that reads rows was offered, and it said so without trying another way | Pass: 403, "hasn't been given READ_DATA" |
| 6 With **Read data** and **Run code**, allowed | Pass with Python: the approval showed the tool, the action and the code, and the answer was right. SQL needs a lakehouse, so a Fabric sign-in | Pass with Python |
| 7 The same again | Pass: a new approval was asked for | Pass |
| 8 Denied | Pass: it said you said no and didn't retry | Pass: 403 `approval_denied`, and the same call again stayed denied |
| 9 Python that writes a table | Needs a Fabric sign-in (a lakehouse to write to) | Needs a Fabric sign-in |
| 10 `review_notebook` prompt | Pass: Claude Code shows MCP prompts as slash commands (`/mcp__fabric-desktop__review_notebook`), and it called `validate_notebook` | Not a step for HTTP |
| 11 `fabric-desktop://capabilities` | Pass: it read the resource. Claude Code keeps results this large (about 60 to 90 KB) in a file rather than in the chat | Not a step for HTTP |
| 12 Cancel in the tool | Not run: it needs someone at the keyboard to press Esc. Closing Claude Code mid-run ended the run, and nothing was left running | Not run |
| 13 Revoked | Pass: every tool but `list_capabilities` went away, and that one said the tool isn't connected | Pass: 401 |
| 14 Trail check | Pass | Pass |
| Without the token | | Pass: 401 |

**Codex CLI 0.166.0:** `codex mcp list` and `codex mcp get fabric-desktop` read the setup as one enabled stdio server, and `codex exec` started the server before asking for a sign-in. The steps need an OpenAI account. Codex starts servers with only a short list of environment variables, and on Linux `LOCALAPPDATA` wasn't on it. So on Windows, if `LOCALAPPDATA` is ever missing, the launcher now puts it back (the folder its own `bin` is in), and the server finds the tool's id.

**Claude Desktop, Cursor and VS Code** weren't run: they are desktop apps, and this run was on Linux without them.

Changed after this run: an approval that was turned down now says "The person said no to this request (Run Python)." A tool that asks for Fabric while KratosForge isn't signed in now gets "KratosForge isn't signed in to Fabric, or the sign-in has expired. Open KratosForge and sign in, then try again." It used to say the sign-in had expired, and talk about scheduled runs.

Still to do: run every step on Windows with a Fabric sign-in in a non-production workspace, with each tool's own approval flow and a person answering in the app.
