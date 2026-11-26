# Mutation testing, batch 55

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The setup an AI tool needs: the launcher names and scripts, and the configuration snippets for each tool (`integrations/config.py`).
- The scheduled shortcut health check: probing each shortcut, scanning an environment's workspaces, and which agent posts (`shortcut_watch.py`).
- The contract every execution engine adapter meets: its limits and the request, config and result shapes (`engines/contract.py`).

The run was at `main` babc8ef32 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Five tests in `test_integrations_prompts.py` and `test_vscode_setup.py` compare against pages in `docs/`, so the `integrations/config.py` runs leave them out with `-k "not (agent_instructions_name or connect_page_carries or kratosforge_skill_names or workflows_page_matches or copilot_instructions_match)"`. The new tests check the snippets directly instead.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/config.py` | test_integrations_permissions, test_integrations_prompts, test_vscode_setup, with the `-k` above (+ test_integrations_config_mutation) | 30 of 30 (30 + 0) | 19 / 11 | **63.3%** | 19/30 → 30/30 | none | 30 / 0 | **100%** | none |
| `shortcut_watch.py` | test_shortcut_watch, test_agent_mutation (+ test_shortcut_watch_mutation) | 32 of 32 (31 + 1) | 25 / 7 | **78.1%** | 25/31 → 31/31 | 0/1 → 0/1 | 31 / 1 | **96.9%** | 1 equivalent |
| `engines/contract.py` | test_engines, test_engine_conformance, test_engine_compare (+ test_engine_contract_mutation) | 14 of 14 (14 + 0) | 5 / 9 | **35.7%** | 5/14 → 14/14 | none | 14 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/config.py**:
  - The launchers are `fabric-desktop-mcp.cmd` and `kratosforge.cmd` on Windows, and the same names without `.cmd` elsewhere.
  - The script form follows the system it runs on unless asked for the other.
  - A launcher folder that doesn't exist yet is made.
  - The JSON snippets are two-space JSON naming the client, and the HTTP snippet is there only when an address is given.
  - → `test_integrations_config_mutation.py`.
- **shortcut_watch.py**:
  - The check uses a pool of 8 workers.
  - A folder is listed once and its answer reused for each shortcut in it.
  - A folder that answers 401 or 403 checks none of its shortcuts, and each is marked not checked.
  - A lakehouse whose shortcuts can't be listed is named in the problem, by display name or else id.
  - An environment's own workspace is kept beside its designations.
  - → `test_shortcut_watch_mutation.py`.
- **engines/contract.py**:
  - The preview is 1000 rows and the largest result table 256 MiB.
  - A session config and a cell request can't be changed once made.
  - A result's table is left out of its text, its comparisons and what goes to the app.
  - Seconds are given to four places.
  - → `test_engine_contract_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `shortcut_watch.py` | 120 | `o < me` to `o <= me` | Equivalent: the names compared leave out every record naming `me`, so no name in the list can equal it. |

