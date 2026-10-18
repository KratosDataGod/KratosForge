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
| Extension mechanism | **None yet** at discovery. Extensions (manifest, permissions, own process) are Phase 4 of the Git-native development program ([git-integration-plan.md](https://github.com/KratosDataGod/fabric-desktop/blob/claude/git-foundation-rmrdsg/docs/git-integration-plan.md)), not built. | |
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
              - audit: one line per external call (integrations/audit.py)
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

### Trying the MCP server from source

Until **Settings > Integrations** writes the configuration for you, a developer with the engine installed from source (`pip install -e sidecar`) can point any MCP client at:

```
fabric-desktop mcp --client my-laptop
```

`fabric-desktop capabilities` prints every capability with its schemas as JSON. The server signs in with your existing Fabric Desktop sign-in and never opens a sign-in window; sign in in the app first. On a release install the engine's folder changes with every update, so the Integrations page will write a launcher with a fixed path rather than asking you to copy one.

### The local HTTP API

Off by default. When it is switched on (engine RPC `integrations_set_http`, the Integrations page's switch), the engine runs `fabric-desktop integrations serve --port 47615 --parent-pid <engine>` as a child process; it stops when the engine does. A tool connected with transport `http` gets a bearer token once (`fdt_…`, stored only as a hash) and calls:

| Endpoint | What |
| --- | --- |
| `GET /api/v1/health` | Open: is it up |
| `GET /api/v1/openapi.json` | Open: the OpenAPI 3.1 document, one operation per capability |
| `GET /api/v1/capabilities` | Capabilities with their availability for this token |
| `POST /api/v1/capabilities/{id}/invoke` | Body `{"arguments": {...}}`; result `{"ok": true, "result": ...}` or `{"ok": false, "error": {...}}` |
| `/mcp` | MCP over Streamable HTTP, the same tools as stdio |

```
curl -H "Authorization: Bearer fdt_..." -X POST http://127.0.0.1:47615/api/v1/capabilities/fabric_desktop_list_projects/invoke
```

A call that needs the person's approval answers `428` with `details.approval_id`; call again after they approve it in the app. A client that hangs up cancels its call.

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
| `workspaces` | How a tool limited to some workspaces is kept inside them (`integrations/scope.py`): `none` (reaches no workspace), `argument` (its `workspace_id` names it), `filtered` (a list that leaves out the rest), `unknown` (can't be confined, so a limited tool can't use it), or a function that reads which workspaces the call reaches. Unset is treated as `unknown` |

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
4. Targets are in scope: approved folders for file work; workspaces in the client's list when the person set one. Every capability is checked, not only those with a `workspace_id` argument: one that names a notebook, proof, receipt, side-by-side run, execution or the metadata project is checked against the workspaces that thing points at (its lakehouse, where it publishes, where it ran); lists (workspaces, notebooks, proofs, receipts, side-by-side runs, executions, projects) leave out what is elsewhere, and their totals count only what is left. A capability that can't tell which workspace it would reach (the run-risk check, an extension that doesn't declare `workspaces`, a record saved without workspace ids) is refused to a limited tool and listed as unavailable to it. The check runs before any approval is asked for.
5. If the capability needs approval, the request appears in the app (bell and **Settings > Integrations**) showing the client, the operation, the target and, for code, the code itself. The person approves or denies. An approval is single-use and bound to a hash of the exact arguments, so a client can't get approval for one thing and run another.
6. The service runs under the person's own Fabric and Entra permissions. Integration never adds access the person doesn't have.

Approvals are decided only through the app's own RPC, which external clients can't reach. No MCP tool or REST endpoint grants, approves or changes permissions.

## 6. Execution

External execution reuses the engine's own paths:

| Capability | Runs through | Boundary |
| --- | --- | --- |
| `fabric_desktop_execute_sql` | `sql.run` | Read-only statements only; DuckDB with external access off (no local files, no extensions, no network) so only lakehouse tables can be read |
| `fabric_desktop_execute_python` | `ProcessKernel` (`kernel_proc.py`) | Always in a process of its own; timeout, interrupt then stop; output capped; approval every call |
| `fabric_desktop_run_notebook` | Same kernel, cell by cell, as **Run all** does | Same as above; the code cells are read before asking (`Capability.bind`), shown in the approval and bound to it by their SHA-256, and exactly those run; a notebook changed since is refused; `%run` and `notebookutils.notebook.run` are refused; Fabric runs are not offered yet |

Every execution gets a stable id (`ex_...`), a state (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`), logs, a capped result and timings, kept in `<data>/integrations/executions/` (the last 200). A call waits up to `wait_seconds` (default 30) and otherwise returns the id; `fabric_desktop_get_execution_status` follows it and can wait for it. Past `timeout_seconds` an execution is interrupted, then its process is ended; `fabric_desktop_cancel_execution` and MCP's own cancel notification do the same. An execution is visible only to the tool that started it.

Code an external tool runs is limited on purpose (`integrations/executions.py`):

- It runs in a process kernel started for that execution and ended after it, never in the app's kernel. Nothing is kept between runs.
- The kernel gets a storage token for the attached lakehouse and no other token; `notebookutils.credentials.getToken` for anything else is refused.
- Writing lakehouse tables through the engine is refused, because OneLake writes aren't offered to external tools yet. A notebook that writes tables stops at that cell.
- Output is capped at 100,000 characters, tables at 100 rows, images and HTML are left out, and every string has secrets removed.
- A tool limited to some workspaces can't run code at all, because code runs with the person's sign-in, which reaches every workspace they can open.

Prove this notebook's actions are registered with the permission they really need. Verifying a table again and exporting a receipt need `READ_DATA` and ask. Cancelling a proof needs `EXECUTE_LOCAL_CODE` and always asks. Starting a proof publishes a copy to Fabric (`MODIFY_FABRIC_ARTIFACTS`) and deleting a proof's outputs deletes OneLake files (`WRITE_ONELAKE`); neither category is offered in this release, so both are listed as unavailable with that reason. Receipt exports leave out the parts IT lists in `receipt_redactions`.

## 7. Resources and prompts

MCP resources give context without moving data. A read-only capability that doesn't ask for approval can also be a resource: its registry entry carries a `fabric-desktop://` URI template, and reading the URI is the same `invoke` as calling the tool, with the same permission check. Paging and options go in the query, for example `fabric-desktop://notebooks?offset=100&limit=50`. Resources with no placeholders are listed by `resources/list`; the others by `resources/templates/list`. Over the local API they are at `GET /api/v1/resources` and `GET /api/v1/resources/read?uri=...`.

| URI | What |
| --- | --- |
| `fabric-desktop://status` | Version, sign-in, how this client is connected |
| `fabric-desktop://capabilities` | Every capability with schemas and availability |
| `fabric-desktop://projects`, `.../projects/{id}`, `.../projects/metadata/validation` | Notebooks folder and metadata project |
| `fabric-desktop://workspaces`, `.../workspaces/{workspace_id}/items`, `.../items/{item_id}` | Fabric workspaces and items |
| `fabric-desktop://workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables`, `.../tables/{table}/schema` | Tables and their columns |
| `fabric-desktop://notebooks`, `.../notebooks/{name}` | Notebooks and their cells (secrets removed, no outputs) |
| `fabric-desktop://notebooks/{name}/validation`, `/dependencies`, `/environment`, `/proof-plan` | Checks that run no code |
| `fabric-desktop://proofs`, `.../proofs/{id}`, `fabric-desktop://receipts`, `.../receipts/{id}`, `fabric-desktop://side-by-side`, `.../side-by-side/{id}` | Prove this notebook, without cell outputs or example rows |

Every resource needs only `READ_METADATA`. Rows of data need `READ_DATA` and go through a tool. Results are size-limited (1 MB) and every string in every result passes through secret redaction. MCP prompts carry the reusable workflow templates ([workflows](../ai/workflows.md), PR-AI-009): text only, each step a tool call with the usual checks.

## 7a. Audit

Every call through `Registry.invoke` gets one audit line, whatever happened to it, and so does every request the HTTP server refuses before it reaches `invoke` (no token, a browser `Origin`, a bad `Host`, rate limited). Refusals are written at most once per reason and caller every 10 seconds, with a count of the ones in between, so a flood can't fill the disk.

| Field | What it holds |
| --- | --- |
| `at`, `seconds` | When the call arrived (UTC) and how long it took, including any wait for approval |
| `channel`, `client_id`, `client` | `mcp-stdio`, `mcp-http` or `api`, and the tool as added in Settings |
| `capability`, `permission` | What was asked for (`http_request` for a refused HTTP request) |
| `decision` | `allowed`, `approved`, `denied`, `approval_denied`, `approval_pending` (nobody answered in time) or `invalid` |
| `outcome`, `error` | `ok` or the error code, and the message with secrets removed |
| `execution_id` | For calls that start or read an execution |
| `arguments` | Ids, names and numbers. Code, SQL and long text show as `(given)`; anything credential-shaped is removed |
| `host`, `os_user`, `version`, `prev`, `hash` | Where it ran, and a SHA-256 chain linking each line to the one before |

Lines go to `%LOCALAPPDATA%\FabricDesktop\integrations\audit\audit-YYYY-MM.jsonl` (for log shippers) and to `integrations.sqlite` (the newest 50,000, for **Recent activity**). One MCP server runs per connected tool and the HTTP server is another process, so each line is written inside a store transaction: one writer at a time, and the chain stays whole. The format and chain are the same as the app's audit trail (PR #89), so `fabric-desktop audit --folder "%LOCALAPPDATA%\FabricDesktop\integrations\audit" --verify` checks it once that merges; Settings > Integrations > **Check the trail** does the same today. An audit failure (a full disk) never fails the call; it is reported on the engine's error output.

## 8. Extensions

An extension is a named, versioned set of capabilities with the permission categories it declares (`integrations/extensions.py`). `ExtensionHost` is the only way one reaches the registry, so its capabilities appear in MCP, the local API and Settings like core ones, and every call still passes through `invoke` with the same permissions, approvals, limits and audit line.

| Rule, checked when an extension is added and again when it is turned on | Why |
| --- | --- |
| Its id is a valid extension id, not `core`, and doesn't start with `fabric_desktop` | Core capabilities can't be replaced or imitated |
| Its capability ids start with its own prefix (`notebook-outline` gives `notebook_outline_...`) | Two extensions can't collide, and a tool can see where a capability comes from |
| Each capability's permission is one the extension declared, and its `source` is the extension | What the person sees before turning it on is what it can ask for |
| A resource is under `fabric-desktop://ext/<extension id>/` | It can't stand in for a core resource |
| Everything the registry checks for any capability (schemas, read-only means a read permission, destructive means approval, a valid `workspaces` declaration) | No second, weaker path |
| A capability that doesn't declare `workspaces` still loads, but is refused to a tool limited to some workspaces | It can't reach a workspace by not saying which one it reaches |
| All of its capabilities register, or none do | A half-loaded extension never shows up |

Extensions are off until the person turns each on in **Settings > Integrations > Extensions**. The policy key `integrations_extensions` (a list of ids) limits which ones can be turned on; `[]` or an unreadable value allows none, and one already on is taken away at the next sync. The switches live in the integration store, and each server process's `ExtensionHost.sync()` runs every 2 seconds, so a change reaches every MCP and HTTP server without a restart.

When capabilities change, connected clients are told: `notifications/tools/list_changed` and `notifications/resources/list_changed` on the connection for clients on the 2025-11-25 protocol (advertised as `listChanged` at initialize), and the same events on `subscriptions/listen` streams for clients on 2026-07-28. Prompts don't change at run time.

The built-in **Notebook outline** extension is the sample: one read-only capability, `notebook_outline_get`, which returns a notebook's headings and the functions and classes each cell defines, never its code, using the notebook service the app already has.

What this program leaves to the Git-native program's Phase 4: the manifest, where extensions are installed from, signing, and running an extension in its own process. Its loader calls `ExtensionHost.add(extension)`; until then only built-in extensions exist, and an extension runs inside the engine with the engine's rights, so only code shipped with the app can be one.

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
| Audit trail format | Enterprise readiness (PR #89) | In review; PR-AI-010 writes its own files in the same line format and hash chain, so `fabric-desktop audit --verify` reads both once #89 merges |
| Secret redaction | Enterprise readiness (PR #84) | In review; the integration layer has a small redaction of its own until it merges |
| Policy file keys | Enterprise readiness (PR #90) | In review; integration keys use the same file |
| Per-cell timeouts and resource limits | Prove this notebook | In progress; PR-AI-007 uses timeouts and interrupt today |
| Extension manifest and loader | Git-native development, Phase 4 | Not started; PR-AI-012 supplies `ExtensionHost.add`, the checks, the switch and list-change notices |
| Artifact capability registry | Git-native development | Not started; would feed `artifact_types` |
