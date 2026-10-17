# AI tool test checklist

How to try one AI tool with Fabric Desktop by KratosBI and record the result, so its row in [compatibility](ai-integration-compatibility.md) can change from **Untested** to **Tested**. Run it on a PC with a non-production Fabric workspace. A tool counts as tested only when every step below passes, with the tool's name and version, the Fabric Desktop by KratosBI build and the date written down.

What the automated tests already cover is listed at the end; this checklist covers what only a real tool can show: whether it reads the setup, shows the tools and prompts, waits for approvals and handles refusals.

## Before you start

1. Install the build you're testing and sign in.
2. **Settings > Integrations**: turn on **Let AI tools connect**, **Add a tool** for the tool you're testing, with **Read metadata** only, and **Copy setup**.
3. Paste the setup into the tool as [Connect your AI tool](../ai/README.md) says, and restart the tool.
4. Have a notebook and a lakehouse with a small table ready.

## Steps

| # | Do this in the AI tool | It passes when |
| --- | --- | --- |
| 1 | Open the tool's list of MCP servers or tools | `fabric-desktop` is listed and connected; **Test connection** in Settings also passes |
| 2 | Ask "What can you do in Fabric Desktop?" | It calls `fabric_desktop_get_status` or `fabric_desktop_list_capabilities` and lists tools that only read |
| 3 | Ask for the lakehouses in a workspace, then the tables in one | It calls `fabric_desktop_list_workspaces`, `fabric_desktop_list_artifacts` and `fabric_desktop_list_tables`, and the names match the app |
| 4 | Ask for a table's columns | It calls `fabric_desktop_get_table_schema` and shows no rows |
| 5 | Ask it to show rows from the table | It is refused (the tool has no **Read data**) and says so, without trying another way |
| 6 | In Settings, **Edit** the tool and give it **Read data** and **Run code on this PC**. Ask it to count the table's rows with SQL | An approval opens in the app showing the tool, the query and the lakehouse. **Allow once**: the answer comes back and is right |
| 7 | Ask the same again | A new approval opens (each approval is used once) |
| 8 | Ask it to run Python that prints 1. **Deny** the approval | It says you said no and doesn't retry in another form |
| 9 | Ask it to run Python that writes a table | After you allow it, the write is refused and nothing changes in OneLake |
| 10 | Ask it to review a notebook, using the `review_notebook` prompt if the tool shows prompts | It calls `fabric_desktop_validate_notebook`. Note whether the tool shows MCP prompts and how |
| 11 | Ask it to read the resource `fabric-desktop://capabilities`, if the tool shows resources | The capability list comes back. Note whether the tool shows resources |
| 12 | Ask it to start a long query, then cancel it in the tool | The execution shows **cancelled** in Recent activity |
| 13 | **Revoke** the tool in Settings, then ask anything | It is refused at once |
| 14 | Settings > Integrations > Recent activity, then **Check the trail** | Every request above is listed with the right decision, and the trail check passes |

For the **local HTTP API** (scripts and tools that call HTTP), do steps 2 to 9 and 13 with `curl` or the tool, using the token shown when you added it, and check that a request without the token gets 401.

## Record the result

Add or change the tool's row in [compatibility](ai-integration-compatibility.md): status **Tested** (or **Partly works** with what failed), transport, the tool's version, the Fabric Desktop by KratosBI build, the date, and the step numbers that failed, if any. Open an issue for each failure with what the tool did.

## What the automated tests cover

| Area | Automated test | Transports |
| --- | --- | --- |
| Initialize, protocol negotiation (2025-11-25 and 2026-07-28), server name, version, instructions, ping | `test_integrations_conformance.py` | In memory, stdio process, Streamable HTTP |
| Every tool's input and output schema is valid JSON Schema 2020-12; annotations match the permission | `test_integrations_conformance.py` | All three |
| Resources, templates, prompts; resource not found (-32002); prompt with a missing argument | `test_integrations_conformance.py` | All three |
| Results match their output schema; text mirrors the structured result | `test_integrations_conformance.py` | All three |
| One error shape (invalid input, unknown tool, refused, unknown argument); the session survives errors | `test_integrations_conformance.py` | All three |
| Concurrent calls | `test_integrations_conformance.py` | All three |
| Restart keeps permissions; revocation stops the next process | `test_integrations_conformance.py` | stdio |
| REST follows its OpenAPI document; errors have the documented shape and status | `test_integrations_conformance.py` | HTTP |
| Token, Host, Origin, body size, rate limit, client disconnect cancels the call | `test_integrations_http.py` | HTTP |
| Approvals bound to the exact request, single use, expiry, revocation, policy limits | `test_integrations_permissions.py` | In memory |
| Cancellation, timeouts, output limits, no OneLake writes or other tokens from code | `test_integrations_executions.py`, `test_integrations_mcp.py` | In memory, kernel process |
| Large results refused with a hint | `test_integrations_mcp.py`, `test_integrations_http.py` | In memory, HTTP |
| Audit line for every call, chain across processes | `test_integrations_audit.py` | In memory, HTTP, several processes |

## The MCP conformance suite

The MCP project's own suite, `@modelcontextprotocol/conformance` 0.1.16, was run by hand on 2026-10-17 against the local API's Streamable HTTP endpoint (`sidecar/tests/mcp_conformance_server.py` starts it with a token added, since the suite can't send one). It needs Node and a download, and most of its scenarios call tools, resources and prompts that only its own reference server has, so it isn't part of the automated build.

| Result | Scenarios |
| --- | --- |
| Passed | server-initialize, ping, tools-list, tools-call-simple-text, tools-call-error, resources-list, prompts-list, server-sse-multiple-streams, and the "rejects a non-local Host" half of dns-rebinding-protection |
| Not applicable: need the suite's reference tools, resources or prompts (`test_*`, `test://...`) | tools-call-image, -audio, -embedded-resource, -mixed-content, -with-logging, -with-progress, resources-read-text, -read-binary, resources-templates-read, prompts-get-simple, -with-args, -embedded-resource, -with-image |
| Not supported, on purpose | logging-set-level, completion-complete, resources-subscribe and -unsubscribe, tools-call-sampling, tools-call-elicitation and both elicitation scenarios. The server never asks the AI tool's model for anything (no sampling) and approvals happen in the app, not in the tool (no elicitation) |
| Stricter than the suite expects | dns-rebinding-protection, "accepts a localhost Origin": every request with an `Origin` header is refused, because only programs, never web pages, may call the local API |

Running every scenario against one server also hits the limit of 20 open MCP sessions, because the suite doesn't close its sessions; idle sessions close after 30 minutes. Each scenario was rerun on a fresh server.

To run it again:

```bash
cd sidecar
python tests/mcp_conformance_server.py 47677 &
npx @modelcontextprotocol/conformance@0.1.16 server --url http://127.0.0.1:47677/mcp --scenario server-initialize
```
