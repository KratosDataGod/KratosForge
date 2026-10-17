# AI integration compatibility

Which external tools have been tried with Fabric Desktop by KratosBI, how, and what didn't work. Only rows marked **Tested** are claimed to work. Architecture: [ai-integration.md](../architecture/ai-integration.md).

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
| Claude Code | stdio | Untested | |
| Claude Desktop | stdio | Untested | |
| OpenAI Codex CLI | stdio | Untested | |
| Cursor | stdio | Untested | |
| VS Code with GitHub Copilot (agent mode) | stdio | Untested | |
| httpx (Python) | REST `/api/v1` | **Tested** (automated, every build) | OpenAPI 3.1 at `/api/v1/openapi.json`; any HTTP client should work but only this one is tested |

To test a tool, follow the [AI tool test checklist](ai-integration-client-checklist.md) and record the result here.

Untested means a configuration example exists ([setup for each tool](../ai/README.md)) but nobody has run it with that client yet. MCP prompts ([workflows](../ai/workflows.md)) are tested with the SDK client only; whether a client shows them, and how, is part of each client's test. To report a result, open an issue with the client name and version, the transport, and what happened.

## Conformance

Every build runs the same protocol checks through all three ways a tool can connect (in memory, the stdio process, Streamable HTTP) on both protocol eras, and checks the REST API against its OpenAPI document: `sidecar/tests/test_integrations_conformance.py`. The MCP project's own suite (`@modelcontextprotocol/conformance` 0.1.16) was run by hand on 2026-10-17: every scenario that applies to this server passed except one that is stricter on purpose (requests with any `Origin` header are refused). Details, and how to run it again, are in the [checklist](ai-integration-client-checklist.md#the-mcp-conformance-suite).

| Not supported | Why |
| --- | --- |
| Sampling and elicitation (the server asking the tool's model or user for something) | The server never asks an AI model for anything, and approvals happen in Fabric Desktop by KratosBI, not in the tool |
| `logging/setLevel`, `completion/complete`, resource subscriptions | Not needed by any workflow yet; list-changed notifications cover capability changes |
| Requests from web pages (any `Origin` header) | Only programs on this PC may call the local API |
