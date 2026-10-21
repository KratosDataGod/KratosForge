# AI integration security

Threat model and controls for external AI tools that use KratosForge through MCP or the local API. Architecture: [ai-integration.md](../architecture/ai-integration.md).

## Assumptions

- An external AI client can be compromised, misconfigured, or steered by text it has read (prompt injection), including text inside notebooks, tables, file names and metadata that KratosForge returns.
- Everything a client sends is untrusted input. Everything a client reads is untrusted content and is never treated as an instruction or as permission.
- The person using the PC is trusted, and so is the app's own UI, which talks to its engine over a private pipe.

## What the boundary protects against

A client whose only access is KratosForge's MCP server or local API. That covers AI tools running in a sandbox, IDE assistants limited to MCP, and any agent being manipulated into doing something the person didn't ask for.

## What it does not protect against

A program that can already run commands as you on this PC. It can read your files, the sign-in cache and KratosForge's own settings directly, whatever KratosForge allows. Granting `EXECUTE_LOCAL_CODE` gives a client that level of access for the code you approve, which is why every such call is shown to you first.

## Threats and controls

| Threat | Control | Where |
| --- | --- | --- |
| Client reads a token or secret | No capability returns tokens, connection strings or credentials; results and errors pass through secret redaction; Python output is scanned for token-shaped strings | Registry, `invoke` |
| Client runs code it shouldn't | `EXECUTE_LOCAL_CODE` not granted by default; every execution needs approval in the app showing the code; runs in a process kernel with a timeout and only an allowlisted environment (no secrets from environment variables; still not a sandbox: it can read the sign-in cache) | Permissions, executions |
| Client reads local files through SQL | External SQL is one read-only statement and runs with DuckDB external access off and configuration locked | Executions |
| Code reaches Fabric or writes OneLake | Code gets only a storage token; table writes through the engine are refused; workspace-limited tools can't run code | Executions, permissions |
| Client reads data it shouldn't | Rows need `READ_DATA`; resources are schema-only; Fabric enforces the person's own access on every OneLake read | Permissions, resources |
| Client writes to OneLake or changes Fabric | Not offered in the first release | Registry |
| Client approves its own request | Approvals are decided only through the app's RPC; no MCP tool or REST endpoint changes permissions or approvals | Permissions |
| Approval reused for other arguments | Single-use, bound to a SHA-256 of the canonical arguments, expires; a notebook run's arguments include its code cells and their SHA-256, read before asking | Permissions |
| Another website talks to the local API (DNS rebinding, CSRF) | `127.0.0.1` only; `Host` must be a loopback name; browser `Origin`s refused; bearer token required | HTTP server |
| Another local process uses the API | Per-client bearer token (stored only as a hash), revocable | HTTP server |
| Flooding or huge requests | Body size limit, per-client rate limit, concurrency limit, output caps | HTTP server, executions |
| A revoked client keeps going | Every call checks the client record; revoking stops the next call | Permissions |
| Lower-privilege interface changes policy | IT policy keys are read only from the policy file; integrations can't change settings | Policy |
| An extension widens what tools can do, or imitates a core capability | Extensions are off until the person turns each on and can be limited by policy; ids, permissions and resources are checked against what the extension declares; core ids and URIs are reserved; every call goes through the same `invoke`, permissions, approvals and audit; registration is all or nothing | Extensions (`integrations/extensions.py`) |
| Untraceable actions | One hash-chained audit line per external call and per refused HTTP request, with client, channel, capability, decision and outcome; code and queries not recorded, secrets removed | Audit (`integrations/audit.py`) |
| Large responses exhaust memory or context | Paging, response size limits, row limits | Resources, executions |

## IT controls

In `%ProgramData%\FabricDesktop\policy.json`:

| Key | Effect |
| --- | --- |
| `integrations` | `false` turns external AI integration off on this PC; the switch in Settings shows "Set by your organization" |
| `integrations_max_permissions` | The permission categories clients may be granted, for example `["READ_METADATA", "READ_DATA"]`; anything else can't be granted |
| `integrations_http` | `false` stops the local HTTP API being switched on |
| `integrations_extensions` | The extensions that may be turned on, by id, for example `["notebook-outline"]`; `[]` allows none |

A value of the wrong type fails closed: integrations off, or no permission categories, or no HTTP, or no extensions, or every receipt part left out. A policy file that exists but can't be read is replaced by the last copy that could be read, or, with no copy, applies all of those at once, and both the integrations and the rest of KratosForge read it through the same loader (`app_settings.read_policy()`).

## Residual risks

- A person can approve a harmful request. The approval screen shows the client, operation, target and code, but it can't judge intent.
- Approved code runs as you. It gets a storage token, and code written to use that token directly (not through the engine) could write OneLake files. The approval screen shows the code in full; approve only what you've read. Code, a query or a notebook too long to show in full (over 20,000 characters) is refused before you're asked, never cut.
- Approving a notebook run shows the notebook's name, its lakehouse and every code cell that will run (markdown, empty and frozen cells are left out), read from the saved notebook when the tool asks. The approval is bound to a SHA-256 of those cells: exactly those cells run, and if the saved notebook changes before the run starts it is refused with "The notebook changed after it was approved. Ask again." and nothing runs. Asking again shows the new code. A notebook run by an external tool can't run other notebooks: `%run` and `notebookutils.notebook.run` are refused, because their code wasn't shown. Notebooks you run in the app are unchanged. A notebook whose code cells are too long to show (over 20,000 characters) is refused with "This notebook is too long to show for approval (N characters, limit 20,000). Run it in KratosForge instead."
- Token redaction recognises known shapes (JWTs, bearer headers, SAS signatures, key-value secrets); a secret in an unusual format can pass through.
- The audit trail is evidence, not a lock: someone who controls the PC can delete the whole folder, which the chain check can't see. Ship `integrations\audit` to a log store if that matters.
- An extension's code runs inside the engine with the engine's rights. Only extensions shipped with the app exist in this release; installing others, signing and isolating them is the Git-native program's Phase 4.
- Integration settings and approvals are files in your Windows profile; anything running as you can change them (see "What it does not protect against").
