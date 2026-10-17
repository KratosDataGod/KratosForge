# AI integration architecture

Fabric Desktop by KratosBI is **AI-ready, not AI-dependent**. It contains no AI model, no chat, no AI provider SDK and asks for no AI API key. Instead it exposes its own capabilities through standard interfaces (the Model Context Protocol and a documented local API) so a developer can point whichever AI tool they already use at it: Claude, Codex, GitHub Copilot, Cursor, or tools that don't exist yet.

The app works exactly the same with every integration switched off, which is the default.

This page is the architecture. The backlog and status of each pull request are in [ai-integration-backlog.md](../product/ai-integration-backlog.md), the decisions in [ai-integration-decisions.md](../product/ai-integration-decisions.md), the threat model in [ai-integration-security.md](../product/ai-integration-security.md) and what has been tried with which client in [ai-integration-compatibility.md](../product/ai-integration-compatibility.md).

## 1. What exists today (discovery, 2026-10-17)

| Area | What is there | Where |
| --- | --- | --- |
| Shell | Tauri 2 app (Rust) with two WebView2 views. Starts the engine and talks to it over stdin/stdout, one JSON line per request. Opens no network port. | `app/src-tauri/src/main.rs` |
| UI | Plain HTML and JavaScript, no build step. Pages registered in `features.js`; left rail full at 8 items. | `app/ui/` |
| Engine | Python package `fabric_desktop`. `Service` (in `service.py`, built from about 15 mixins) holds every operation the UI calls; `server.py` is the JSON-lines RPC with an allowlist (`METHODS`). The CLI (`cli.py`) builds its own `Service` for jobs, schedules and the agent. | `sidecar/fabric_desktop/` |
| Application services | Workspaces, items and lakehouses (`fabric_api.py`), OneLake files and tables (`onelake.py`), item previews (`preview_service.py`), notebooks store and kernel (`notebook.py`, `kernel_proc.py`), Spark SQL on DuckDB (`sql.py`), metadata framework (`meta_service.py`, `meta/`), Prove and receipts (`prove_service.py`), workloads (`workload_service.py`), schedules, Run in Fabric (`cloud_service.py`, `fabric_run.py`). | same |
| Authentication | MSAL public client signed in as the person (delegated), token cache in the Windows profile. No service principal on the desktop. The agent uses an app identity. | `auth.py` |
| Execution boundaries | Notebook cells run in the engine or in a process of their own (`kernel_proc.py`, Preview), with interrupt and stop. SQL runs in an in-memory DuckDB with each named table registered as an Arrow dataset. Prove runs in a fresh kernel with outputs redirected. | |
| Local network APIs | **None.** The engine is stdio only and the headless agent has no HTTP endpoint (`agent.py`, `agent_status.py` write files). | |
| MCP | **None.** No MCP code, experiment or dependency anywhere in the repository. | |
| Extension mechanism | **None yet.** Extensions (manifest, permissions, own process) are Phase 4 of the Git-native development program ([git-integration-plan.md](https://github.com/KratosDataGod/fabric-desktop/blob/claude/git-foundation-rmrdsg/docs/git-integration-plan.md)), not built. | |
| Audit, redaction, IT policy | In review, owned by the enterprise readiness program: hash-chained audit trail (PR #89, `audit.py`), secret redaction (PR #84, `redact.py`), policy keys `auto_update`, `allow_fabric_runs`, `allowed_tenants` (PR #90). The policy file itself (`%ProgramData%\FabricDesktop\policy.json`) already exists in `app_settings.py`. | |
| Tests | About 40 pytest modules in `sidecar/tests`, all run without Fabric; UI tests in `app/tests/ui.test.cjs`. | |

What this means: the engine already *is* a service layer. Every UI action is a named method with keyword arguments, returning JSON-ready data. What is missing is a contract an outside caller can rely on: schemas, permissions, consistent errors, cancellation and timeouts, and a transport an AI tool can speak.

## 2. Target architecture

```
External tools: Claude, Codex, Copilot, Cursor, any MCP client, any HTTP client
        |                       |                          |
   MCP over stdio        MCP over Streamable HTTP      REST + OpenAPI
   (fabric-desktop mcp)  (127.0.0.1, token)            (127.0.0.1, token)
        \_______________________|__________________________/
                                |
              Fabric Desktop integration layer  (fabric_desktop/integrations/)
              - capability registry: every operation, its schemas and permissions
              - invoke(): validate -> authorize -> approve -> run -> shape result
              - permissions and approvals (integrations.sqlite, decided in the app)
              - executions: ids, status, cancel, timeouts, output limits
              - audit: one line per external call (extends audit.py)
                                |
              Existing application services (Service and its mixins)
              workspaces, items, lakehouses, OneLake, notebooks, kernel, SQL,
              metadata framework, Prove, workloads
```

Rules that keep this an adapter and not a second application:

1. **One entry point.** MCP tools, MCP resources and REST endpoints all call `integrations.invoke(capability_id, arguments, caller)`. Nothing else in the integration layer touches `Service`. A capability's handler is a few lines that call the existing service method and shape its result.
2. **The registry is the contract.** Tool lists, resource lists, the OpenAPI document and the Integrations page are generated from the registry. A new capability is one registry entry; no transport code changes.
3. **Authorization happens in `invoke`, below every transport.** A request that reaches a capability by any route meets the same permission check, the same approval and the same audit line. The UI's own RPC path (`server.py`) is the person at the keyboard and is not changed.
4. **Unavailable means not advertised.** A capability whose service doesn't exist, whose permission category the person hasn't granted, or that IT has blocked is listed as unavailable with the reason, never simulated.

## 3. Processes and transports

| Mode | How it starts | Who can reach it | Notes |
| --- | --- | --- | --- |
| **MCP over stdio** (default) | The AI tool runs `fabric-desktop mcp --client <id>` itself, from the configuration the Integrations page copies. | Only the tool that started it (a pipe). | Works with every MCP client tested so far. Each run is its own process with its own `Service`, sharing the person's sign-in cache and data folder. |
| **Local HTTP** (off by default) | Switched on in **Settings > Integrations**; the engine starts `fabric-desktop integrations serve` as a child process and stops it with the app. | `127.0.0.1` only, with a bearer token per client. | Serves MCP Streamable HTTP at `/mcp` and the REST API at `/api/v1`, with the OpenAPI document at `/api/v1/openapi.json`. |

Why separate processes and not the app's own engine: the app's engine is owned by the shell over stdio and runs the person's notebook kernel. An external caller in the same process would share that kernel and block the UI. A separate process gives each external client its own kernel and queue, and a crash or a runaway query there can't take the app down. Both use the same code, data folder and sign-in.

The MCP server is built on the official MCP Python SDK (`mcp`, pinned in `constraints.txt`) using its low-level `Server`, because the tool list is generated from the registry at run time. It serves both protocol eras the SDK supports: the handshake era (2025-06-18 and 2025-11-25, which current desktop clients speak) and 2026-07-28.

## 4. The capability registry

Each capability carries:

| Field | Meaning |
| --- | --- |
| `id` | Stable identifier, also the MCP tool name, for example `fabric_desktop_list_workspaces` |
| `title`, `description` | Plain words for people and models |
| `input_schema`, `output_schema` | JSON Schema; inputs are validated before the service runs |
| `permission` | One permission category (below) |
| `read_only`, `destructive` | Classification; also sent as MCP tool annotations |
| `approval` | `never`, `ask` (the person approves in the app; per client this can be relaxed to pre-approved) or `always` (every call, can't be relaxed) |
| `locations` | `this_pc`, `fabric`, or both |
| `artifact_types` | Fabric item types it works on, where that applies |
| `status` | `available`, `preview`, `unavailable` (with `reason`) |
| `limitations` | Known limits, shown to clients |
| `version` | Bumped when the schema changes incompatibly |
| `source` | `core` or the extension id that registered it |

The registry is dynamic: `Registry.register()` adds a capability with validation of its schemas and permission, and `unregister()` removes it. Built-in capabilities are registered at import time; extensions register theirs when enabled (section 8).

This registry is separate from, and named differently to, the Git program's *artifact capability registry* (which Fabric item types support Git and deployment). Where that registry exists, `artifact_types` should be read from it.

## 5. Permissions and approvals

| Category | Allows | Default |
| --- | --- | --- |
| `READ_METADATA` | Names, structure, schemas, validation results, execution status | Granted to a client when the person adds it |
| `READ_DATA` | Rows: table previews, query results | Ask |
| `WRITE_LOCAL_FILES` | Creating and changing notebooks in the app's notebook folder | Ask |
| `EXECUTE_LOCAL_CODE` | Running Python and notebooks on this PC | Ask, every call |
| `WRITE_ONELAKE` | Writing tables or files in OneLake | Not offered in the first release |
| `MODIFY_FABRIC_ARTIFACTS` | Creating or changing Fabric items | Not offered in the first release |
| `DEPLOY_TO_FABRIC` | Promote, publish, deploy | Not offered in the first release |
| `MANAGE_CONNECTIONS` | Connections and credentials | Never offered; credentials never leave the app |

How a call is decided, in order (in `invoke`):

1. Integrations are on (the person's switch, and not turned off by IT policy `integrations`).
2. The client is known and not revoked. Unknown clients get nothing.
3. The capability is available and its category is granted to this client and allowed by policy (`integrations_max_permissions`).
4. Targets are in scope: approved folders for file work; workspaces in the client's list when the person set one.
5. If the capability needs approval, the request appears in the app (bell and **Settings > Integrations**) showing the client, the operation, the target and, for code, the code itself. The person approves or denies. An approval is single-use and bound to a hash of the exact arguments, so a client can't get approval for one thing and run another.
6. The service runs under the person's own Fabric and Entra permissions. Integration never adds access the person doesn't have.

Approvals are decided only through the app's own RPC, which external clients can't reach. No MCP tool or REST endpoint grants, approves or changes permissions.

## 6. Execution

External execution reuses the engine's own paths:

| Capability | Runs through | Boundary |
| --- | --- | --- |
| `fabric_desktop_execute_sql` | `sql.run` | Read-only statements only; DuckDB with external access off (no local files, no extensions, no network) so only lakehouse tables can be read |
| `fabric_desktop_execute_python` | `ProcessKernel` (`kernel_proc.py`) | Always in a process of its own; timeout, interrupt then stop; output capped; approval every call |
| `fabric_desktop_run_notebook` | Same kernel, cell by cell, as **Run all** does | Same as above; Fabric runs are not offered yet |

Every execution gets a stable id, a state (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`), logs, a capped result and timings. Calls wait a short time and otherwise return the id, so long work never blocks a client or the app. Cancellation works by id or by MCP's own cancel notification. Per-cell time and memory limits are being built by the Prove program; external executions will call them once merged.

## 7. Resources and prompts

MCP resources give context without moving data: `fabric-desktop://status`, `fabric-desktop://capabilities`, `fabric-desktop://notebooks/{name}`, `fabric-desktop://workspaces/{id}/items`, `fabric-desktop://lakehouses/{workspace}/{lakehouse}/tables/{table}/schema` and the metadata project. Resources are paged and size-limited, carry schemas only (rows need `READ_DATA` and go through a tool), and pass through the same `invoke`. MCP prompts carry the reusable workflow templates from the agent instructions (PR-AI-009).

## 8. Extensions

Extensions register capabilities with `registry.register(capability, source="<extension id>")` and remove them with `unregister_source()`. The registry validates schemas and requires an explicit permission category; an extension can't register a capability in a category the person can't grant, can't replace a core capability, and every call still passes through `invoke`. Listing changes are sent to connected MCP clients as `tools/list_changed`.

The extension framework itself (manifest, signing, own process) belongs to the Git-native program's Phase 4. This program supplies the registration interface and a sample built-in extension; the manifest loader will call it.

## 9. Security boundaries

The full threat model is in [ai-integration-security.md](../product/ai-integration-security.md). In short:

- External clients and everything they send are untrusted, and so is everything they read (notebook text, data, metadata). Nothing read is ever treated as an instruction or as authorization.
- No capability returns a token, secret, connection string or credential. Results and errors pass through secret redaction.
- The HTTP listener binds to `127.0.0.1`, requires a per-client bearer token, checks `Host` and `Origin` (DNS rebinding), limits request size, rate and concurrency, and stops with the app.
- **What this boundary is not:** it protects against an AI tool that only has Fabric Desktop's interfaces, including one misled by a prompt injection. It does not protect against a tool that already runs arbitrary commands as you on the PC: that tool can read your files and the sign-in cache directly. Approvals for `EXECUTE_LOCAL_CODE` exist because running code is equivalent to that level of access.

## 10. Compatibility and limits

- MCP clients differ in which transports and features they support (resources, prompts, list-changed notifications, Streamable HTTP). Only configurations listed in [ai-integration-compatibility.md](../product/ai-integration-compatibility.md) as tested are claimed.
- Nothing has been run against a live Fabric tenant by this program; capabilities that call Fabric inherit the Preview status of the features they wrap.
- The first release offers no OneLake writes, Fabric item changes, deployments or Fabric-side runs to external tools.

## 11. Dependencies on other programs

| Need | Owner | Status |
| --- | --- | --- |
| Audit trail to extend | Enterprise readiness (PR #89) | In review; PR-AI-010 builds on it |
| Secret redaction | Enterprise readiness (PR #84) | In review; the integration layer has a small redaction of its own until it merges |
| Policy file keys | Enterprise readiness (PR #90) | In review; integration keys use the same file |
| Per-cell timeouts and resource limits | Prove this notebook | In progress; PR-AI-007 uses timeouts and interrupt today |
| Extension manifest and loader | Git-native development, Phase 4 | Not started; PR-AI-012 supplies the interface |
| Artifact capability registry | Git-native development | Not started; would feed `artifact_types` |
