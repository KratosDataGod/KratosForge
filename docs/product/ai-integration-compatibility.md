# AI integration compatibility

Which external tools have been tried with Fabric Desktop by KratosBI, how, and what didn't work. Only rows marked **Tested** are claimed to work. Architecture: [ai-integration.md](../architecture/ai-integration.md).

## Protocol

| Item | Supported |
| --- | --- |
| MCP protocol versions | 2025-06-18 and 2025-11-25 (handshake era) and 2026-07-28, negotiated by the MCP Python SDK |
| Transports | stdio (default); Streamable HTTP on `127.0.0.1` when switched on |
| Server features | Tools, resources and resource templates, prompts, list-changed notifications, cancellation, progress |
| Not supported | SSE transport (deprecated by MCP), remote (non-loopback) HTTP, OAuth authorization server |

## Clients

| Client | Transport | Status | Notes |
| --- | --- | --- | --- |
| MCP Python SDK client | stdio, in-memory, Streamable HTTP | Planned (automated tests) | Run on every build |
| Claude Code | stdio | Untested | |
| Claude Desktop | stdio | Untested | |
| OpenAI Codex CLI | stdio | Untested | |
| Cursor | stdio | Untested | |
| VS Code with GitHub Copilot (agent mode) | stdio | Untested | |
| Any HTTP client | REST `/api/v1` | Planned (automated tests) | OpenAPI at `/api/v1/openapi.json` |

Untested means a configuration example exists but nobody has run it with that client yet. To report a result, open an issue with the client name and version, the transport, and what happened.
