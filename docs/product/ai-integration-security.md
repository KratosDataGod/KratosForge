# AI integration security

Threat model and controls for external AI tools that use Fabric Desktop by KratosBI through MCP or the local API. Architecture: [ai-integration.md](../architecture/ai-integration.md).

## Assumptions

- An external AI client can be compromised, misconfigured, or steered by text it has read (prompt injection), including text inside notebooks, tables, file names and metadata that Fabric Desktop returns.
- Everything a client sends is untrusted input. Everything a client reads is untrusted content and is never treated as an instruction or as permission.
- The person using the PC is trusted, and so is the app's own UI, which talks to its engine over a private pipe.

## What the boundary protects against

A client whose only access is Fabric Desktop's MCP server or local API. That covers AI tools running in a sandbox, IDE assistants limited to MCP, and any agent being manipulated into doing something the person didn't ask for.

## What it does not protect against

A program that can already run commands as you on this PC. It can read your files, the sign-in cache and Fabric Desktop's own settings directly, whatever Fabric Desktop allows. Granting `EXECUTE_LOCAL_CODE` gives a client that level of access for the code you approve, which is why every such call is shown to you first.

## Threats and controls

| Threat | Control | Where |
| --- | --- | --- |
| Client reads a token or secret | No capability returns tokens, connection strings or credentials; results and errors pass through secret redaction; Python output is scanned for token-shaped strings | Registry, `invoke` |
| Client runs code it shouldn't | `EXECUTE_LOCAL_CODE` not granted by default; every execution needs approval in the app showing the code; runs in a process kernel with a timeout | Permissions, executions |
| Client reads local files through SQL | External SQL is read-only and runs with DuckDB external access off and configuration locked | Executions |
| Client reads data it shouldn't | Rows need `READ_DATA`; resources are schema-only; Fabric enforces the person's own access on every OneLake read | Permissions, resources |
| Client writes to OneLake or changes Fabric | Not offered in the first release | Registry |
| Client approves its own request | Approvals are decided only through the app's RPC; no MCP tool or REST endpoint changes permissions or approvals | Permissions |
| Approval reused for other arguments | Single-use, bound to a SHA-256 of the canonical arguments, expires | Permissions |
| Another website talks to the local API (DNS rebinding, CSRF) | `127.0.0.1` only; `Host` must be a loopback name; browser `Origin`s refused; bearer token required | HTTP server |
| Another local process uses the API | Per-client bearer token (stored only as a hash), revocable | HTTP server |
| Flooding or huge requests | Body size limit, per-client rate limit, concurrency limit, output caps | HTTP server, executions |
| A revoked client keeps going | Every call checks the client record; revoking stops the next call | Permissions |
| Lower-privilege interface changes policy | IT policy keys are read only from the policy file; integrations can't change settings | Policy |
| Untraceable actions | One audit line per external call with client, channel, capability, decision and outcome; arguments redacted | Audit |
| Large responses exhaust memory or context | Paging, response size limits, row limits | Resources, executions |

## IT controls

In `%ProgramData%\FabricDesktop\policy.json`:

| Key | Effect |
| --- | --- |
| `integrations` | `false` turns external AI integration off on this PC; the switch in Settings shows "Set by your organization" |
| `integrations_max_permissions` | The permission categories clients may be granted, for example `["READ_METADATA", "READ_DATA"]`; anything else can't be granted |
| `integrations_http` | `false` stops the local HTTP API being switched on |

A value of the wrong type fails closed: integrations off, or only `READ_METADATA`, or no HTTP.

## Residual risks

- A person can approve a harmful request. The approval screen shows the client, operation, target and code, but it can't judge intent.
- Token redaction recognises known shapes (JWTs, bearer headers, SAS signatures, key-value secrets); a secret in an unusual format can pass through.
- Integration settings and approvals are files in your Windows profile; anything running as you can change them (see "What it does not protect against").
