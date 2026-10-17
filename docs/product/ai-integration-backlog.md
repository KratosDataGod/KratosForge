# AI integration backlog

The universal AI integration program: twelve pull requests that let any external AI tool use Fabric Desktop by KratosBI through MCP or a local API, with no AI inside the app. Architecture: [ai-integration.md](../architecture/ai-integration.md).

Status values: COMPLETE (already in the code), PARTIAL (some of it exists), MISSING, BLOCKED, DUPLICATE, UNVERIFIED. Discovery classified each item against `main` at `882438f` on 2026-10-17. The PR column is filled in as each pull request opens.

## Order

Built in the requested order, with one change: the capability registry (003) comes before the MCP server (002), because the server's tool list is generated from it. Every PR targets `main` and builds on the one before; merge them in this order.

| # | Item | Priority | Discovery | Depends on | PR | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | Stable application service interfaces | P0 | PARTIAL | | [#99](https://github.com/KratosDataGod/fabric-desktop/pull/99) | Draft PR; 42 tests |
| 003 | Capability registry | P0 | MISSING | 001 | [#100](https://github.com/KratosDataGod/fabric-desktop/pull/100) | Draft PR; 24 tests |
| 002 | MCP server (read-only) | P0 | MISSING | 003 | [#101](https://github.com/KratosDataGod/fabric-desktop/pull/101) | Draft PR; 12 protocol tests (in-memory both eras, real stdio process) |
| 005 | Permissions and approval framework | P0 | MISSING | 002 | [#102](https://github.com/KratosDataGod/fabric-desktop/pull/102) | Draft PR; 17 tests |
| 004 | Local API (REST + MCP over HTTP) | P0 | MISSING | 005 | [#103](https://github.com/KratosDataGod/fabric-desktop/pull/103) | Draft PR; 39 tests, including the SDK client over Streamable HTTP against a real server and the engine starting the child process |
| 006 | Project context and metadata resources | P1 | PARTIAL | 005 | [#104](https://github.com/KratosDataGod/fabric-desktop/pull/104) | Draft PR; 23 tests |
| 007 | External execution interface | P1 | PARTIAL | 005 | [#105](https://github.com/KratosDataGod/fabric-desktop/pull/105) | Draft PR; 31 tests |
| 008 | Connection experience (Settings > Integrations) | P1 | MISSING | 005, mockup | [#106](https://github.com/KratosDataGod/fabric-desktop/pull/106) | Draft PR; 6 engine tests, 3 UI checks, checked in a browser against a real engine |
| 009 | Reusable agent instructions | P1 | MISSING | 002 | (this PR) | Draft PR; 8 tests (prompts over MCP, docs kept the same as the code) |
| 010 | Audit and observability | P1 | PARTIAL | PR #89 | | Planned |
| 011 | Compatibility and conformance testing | P1 | MISSING | 004 | | Planned |
| 012 | Extension integration | P2 | BLOCKED (framework) | 003, Git program Phase 4 | | Interface only |

## Items

### PR-AI-001: Stable application service interfaces

- **Evidence.** `Service` (`service.py`) already gathers every operation behind named methods with keyword arguments, used by the UI (`server.py`) and the CLI. What's missing: input schemas and validation, one error shape (errors are free text today: `"ValueError: ..."`), timeouts and cancellation for calls that aren't kernel runs, and structured results for an outside caller.
- **Plan.** `fabric_desktop/integrations/core.py`: `IntegrationError` with codes (`invalid_input`, `not_found`, `permission_denied`, `approval_required`, `conflict`, `timeout`, `cancelled`, `unavailable`, `upstream`, `internal`) and an `error_from()` that maps the engine's existing exceptions; a small JSON Schema validator; `CallContext` with a deadline and a cancel flag; `run_call()` that runs a handler on a worker thread with the timeout. No change to `Service` or the UI.
- **Acceptance.** Services callable without the UI; existing UI behaviour unchanged (no edits to `service.py` or `server.py`); consistent error structure; tests for validation, errors, timeout and cancellation.

### PR-AI-003: Capability registry

- **Evidence.** Nothing describes capabilities today; `server.py`'s `METHODS` is a flat allowlist.
- **Plan.** `integrations/registry.py` with the fields in the architecture page; built-in capabilities in `integrations/capabilities.py` as thin adapters over existing service methods; dynamic `register`/`unregister`; availability from the policy, permissions and the service itself; `describe()` for clients.
- **First capabilities** (each wraps an existing method): status (`environment`, `kernel_info`), capabilities, projects (notebooks folder and `meta_project`), workspaces (`list_workspaces`), artifacts (`list_items`), artifact metadata (`preview_notebook` and item fields), lakehouse tables (`list_tables`) and columns (`table_columns`), notebooks (`list_notebooks`, `open_notebook`), validate notebook (`compute_check`, `proof.check`, `notebook_dependencies`), validate project (`meta_project` and entity checks), execution status, cancel and logs.
- **Prove capabilities offered by the Prove program** (registered in 006 and 007): read-only `notebook_dependencies`, `notebook_environment`, `prove_check`, `prove_status`, `prove_list`, `receipts_list`, `receipt_get`, `side_by_side_status`, `side_by_side_list`; approval-gated `prove_start`, `prove_cancel`, `prove_verify`, `prove_delete_outputs`, `receipt_export`. `side_by_side_start` overwrites real tables and needs the person's own confirmation list, so it is not offered to external tools.

### PR-AI-002: MCP server

- **Evidence.** No MCP code or dependency.
- **Plan.** `fabric-desktop mcp` (stdio) on the official MCP Python SDK low-level server: tools and resources from the registry, structured results and errors, cancellation, protocol negotiation by the SDK. Read-only capabilities only; execution and mutation tools are listed as unavailable until 005.
- **Acceptance.** A standard client connects (tested with the SDK's own client, both protocol eras); tools discoverable; read-only calls work through existing services; unauthorized calls rejected; protocol tests.

### PR-AI-005: Permissions and approval framework

- **Evidence.** No notion of a caller other than the person; IT policy file exists (`app_settings.py`).
- **Plan.** `integrations/permissions.py` and `integrations.sqlite`: clients, grants, scopes, revocation, single-use approvals bound to an argument hash, policy keys `integrations` and `integrations_max_permissions` in the existing policy file. Engine RPC methods for the app to list and decide approvals. Approvals can't be granted through MCP or the API.

### PR-AI-004: Local API

- **Evidence.** The engine opens no port today (by design).
- **Plan.** `fabric-desktop integrations serve`: one Starlette app on `127.0.0.1` with `/api/v1` (REST, OpenAPI generated from the registry) and `/mcp` (Streamable HTTP). Off by default; per-client bearer tokens stored hashed; Host and Origin checks; body size, rate and concurrency limits; started and stopped by the engine; token revocation.
- **Built.** `integrations/http_server.py` (guard, rate limiter, OpenAPI generator, Starlette app, `serve`), `fabric-desktop integrations serve --port --parent-pid`, engine RPC `integrations_set_http` and `integrations_new_token`, the engine starts the API at launch when it is switched on. Status codes: 401 no or bad token, 403 not allowed, 428 waiting for approval, 429 rate limited, 413 too large, 503 switched off.
- **Limitations.** Loopback only, no TLS (traffic never leaves the PC). The SDK's own Streamable HTTP client is the only client tested over HTTP. Rate and concurrency limits are fixed (5 per second with bursts of 30 per tool, 4 calls at once).

### PR-AI-006: Project context and metadata resources

- **Evidence.** The services exist (catalog, tables, columns, notebooks, metadata project, Prove plans) but return everything at once and include data in previews.
- **Plan.** MCP resources and resource templates with stable `fabric-desktop://` URIs, paging, size limits, schema-only by default, redaction of secrets in notebook text.
- **Built.** A `resource` URI template on registry entries (checked when registered: read-only, no approval, every required input in the URI, no two alike), `Registry.resolve`, MCP `resources/list`, `resources/templates/list` and `resources/read` generated from it, REST `/api/v1/resources`. 22 resources. Nine Prove capabilities (notebook dependencies, environment, proof plan; proofs, receipts, side-by-side runs, list and get) that leave out cell outputs and example rows. Every result now passes through secret redaction in `invoke`, not only errors.
- **Limitations.** `side_by_side_writes` (PR #81) is added once that merges. Resource subscriptions and change notifications are not offered. A receipt read through a tool always hides example rows, even for a tool with `READ_DATA`.

### PR-AI-007: External execution interface

- **Evidence.** Kernel with start, poll, interrupt and stop (`notebook.py`, `kernel_proc.py`); SQL engine; Prove's own status pattern. No execution ids outside Prove.
- **Plan.** `integrations/executions.py`: ids, states, logs, capped output, timeouts, cancellation; SQL read-only and sandboxed (DuckDB `enable_external_access = false`); Python and notebooks in a process kernel only; approval every call.
- **Built.** `integrations/executions.py` (ExecutionManager, records on disk, wait/timeout/cancel), `sql.run(sandbox=True)` and `Service.run_sql(sandbox=True)`, seven capabilities (execute SQL, execute Python, run notebook, status, logs, list, cancel) and Prove's five actions with their real permissions. Code gets only a storage token and can't write tables through the engine; workspace-limited tools can't run code.
- **Limitations.** Notebooks run on this PC only. Per-cell memory limits wait for the Prove program's work. No state is kept between Python runs. The approval for a notebook run names the notebook rather than showing its code.

### PR-AI-008: Connection experience

- **Plan.** A section in **Settings** (the left rail is full): on/off switches, server status, clients with their permissions, copyable configuration for stdio and HTTP, connection test, pending approvals, recent external operations, troubleshooting. Mockup first. No chat, no model picker, no API keys for AI services.
- **Built.** `app/ui/integrations.js`: an **Integrations** tab (Preview) in Settings next to **General**, following the mockup: on/off switch (policy-locked variant), how tools connect (MCP launcher, local HTTP API switch, capability counts and list), connected tools (Test, Edit, Revoke, new token), the two-step **Add a tool** with ready-to-paste setup and **Test connection** (starts the real MCP server through the launcher), an approval prompt that opens over any page, recent activity and troubleshooting. Engine RPC `integrations_pending`, `integrations_activity`, `integrations_test`. Command bar: **Connect an AI tool**. User guide section **External AI tools**.
- **Limitations.** No Windows notification when the app is in the background (the window title shows the count); that needs a shell change. Recent activity shows approvals and executions; every call arrives with the audit log (PR-AI-010).

### PR-AI-009: Reusable agent instructions

- **Plan.** `docs/ai/`: vendor-neutral instructions and workflow templates (also served as MCP prompts), and configuration examples for Claude Code, Claude Desktop, Codex, Cursor, VS Code with GitHub Copilot, and any MCP client, each marked tested or untested.
- **Built.** Six workflows in `integrations/prompts.py` (explore a lakehouse, review a notebook, prepare a proof, investigate a proof, answer with SQL, check the metadata project), served as MCP prompts and written out in [docs/ai/workflows.md](../ai/workflows.md); [docs/ai/README.md](../ai/README.md) with the setup for each tool, generated from the same code as the app's **Copy setup**; [docs/ai/agent-instructions.md](../ai/agent-instructions.md) to paste into `AGENTS.md`, `CLAUDE.md`, Cursor rules or Copilot instructions. Prompts only describe tool calls; they read nothing, run nothing and need no permission.
- **Tests.** Prompts render and refuse missing or unknown arguments; every tool a prompt or the instructions name exists; prompts don't suggest actions the tools can't take; `prompts/list` and `prompts/get` through the SDK client; the docs carry each prompt and each setup exactly as the code has them.
- **Limitations.** Every AI tool's setup is **Untested**: no tool has been run against it. Prompt arguments are free text (capped at 500 characters) placed into the steps; a prompt is a suggestion to the AI tool, not a control.

### PR-AI-010: Audit and observability

- **Evidence.** PR #89 adds the audit trail for UI calls.
- **Plan.** Every external call adds an audit line with channel (`mcp-stdio`, `mcp-http`, `api`), client id, capability, execution id, permission decision, outcome and error class, with redacted arguments; the Integrations page shows the recent ones.

### PR-AI-011: Compatibility and conformance testing

- **Plan.** Protocol tests through the SDK client over stdio, in-memory and Streamable HTTP (initialize, negotiation, discovery, schemas, auth, approvals, cancellation, errors, restart, disconnect, large responses, invalid input, concurrency); REST tests against the OpenAPI document; a manual test checklist per client. Uses the MCP conformance suite if it can run offline in CI.

### PR-AI-012: Extension integration

- **Evidence.** No extension framework yet (Git program Phase 4).
- **Plan.** The registration interface with validation, enable and disable, list-changed notifications, and a sample built-in extension; the manifest loader is left to the Git program.

## Remaining risks

- Nothing in this program has run against a live Fabric tenant or a real Windows desktop with each AI client; compatibility is claimed only where tested.
- Permissions protect against tools limited to these interfaces, not against tools that already run commands as you (see the security page).
- The MCP SDK and its dependencies add about 20 packages to the engine.
