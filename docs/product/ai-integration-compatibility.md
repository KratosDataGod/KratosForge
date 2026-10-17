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
| MCP Python SDK client 2.3.0 | stdio, in-memory, Streamable HTTP | **Tested** (automated, every build) | Protocol 2025-11-25 and 2026-07-28; over HTTP with a bearer token against a real server on `127.0.0.1` |
| Claude Code | stdio | Untested | |
| Claude Desktop | stdio | Untested | |
| OpenAI Codex CLI | stdio | Untested | |
| Cursor | stdio | Untested | |
| VS Code with GitHub Copilot (agent mode) | stdio | Untested | |
| httpx (Python) | REST `/api/v1` | **Tested** (automated, every build) | OpenAPI 3.1 at `/api/v1/openapi.json`; any HTTP client should work but only this one is tested |

Untested means a configuration example exists ([setup for each tool](../ai/README.md)) but nobody has run it with that client yet. MCP prompts ([workflows](../ai/workflows.md)) are tested with the SDK client only; whether a client shows them, and how, is part of each client's test. To report a result, open an issue with the client name and version, the transport, and what happened.
