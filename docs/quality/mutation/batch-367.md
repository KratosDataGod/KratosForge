# Mutation testing, batch 367

One module not scored before, run with `--kinds all`:
- The MCP server that lets other AI apps use the app: tool and resource results and their 1,000 KB limit, the JSON text it sends, the descriptions and hints of its tools, the client's name, list-changed notices with at most 64 subscriptions, cancelled requests, and resource errors with their codes and hints (`integrations/mcp_server.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because several runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Five tests that read `docs/` or the app were deselected, because the run copies only `sidecar/`: in test_integrations_prompts, `test_workflows_page_matches_the_prompts`, `test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page`, `test_connect_page_carries_every_setup_from_the_app` and `test_agent_instructions_name_every_prompt_and_only_real_tools`; and in test_integrations_conformance, `test_capability_ids_in_docs_are_real`. The module has 104 mutants, so all were run. The container restarted twice during the batch; the before-run was resumed with `--only` on indexes 94 to 104, and the after-run on the indexes it had not reached, which keeps the same numbering.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. `main` has changed this module since ea988e99 (#3175 records refused resource reads); the new tests pass against it.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/mcp_server.py` | test_cli_local_commands, test_integrations_audit, test_integrations_conformance, test_integrations_extensions, test_integrations_limits, test_integrations_mcp, test_integrations_mcp_edges, test_integrations_multi_client, test_integrations_permissions, test_integrations_prompts, test_integrations_resources, docs tests deselected (+ test_integrations_mcp_server_mutation) | 104 of 104 (102 + 2) | 80 / 24 | **76.9%** | 80/102 → 102/102 | 0/2 → 2/2 | 104 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/mcp_server.py**:
  - A tool result of 1,000,001 bytes is refused as "too_large" with its size in KB, and one of exactly 1,000,000 is sent; the same for a resource.
  - Results are JSON indented by one, with non-ASCII text kept.
  - A preview tool's description ends with its note, and only the Fabric tools are marked open-world; list-changed notices are offered, with at most 64 subscriptions.
  - The client's name is recorded with or without a version.
  - A cancelled tool call stops at once and marks the request cancelled, and a cancelled resource read returns at once.
  - Resource errors: an unknown resource is code -32002, a bad one is invalid params with its hint ("No such page. What to do: Use a number."), and a broken one is an internal error.
  - → `test_integrations_mcp_server_mutation.py`.

### Left

None.
