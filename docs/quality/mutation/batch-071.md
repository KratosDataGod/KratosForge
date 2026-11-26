# Mutation testing, batch 71

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Dataflow Gen2 from a Fabric workspace: the list, the queries and the refresh history (`workspace_dataflows_service.py`).
- Where Windows keeps the Downloads and Documents folders (`known_folders.py`).
- The record `agent run` leaves when it stops before or as it starts (`agent_start.py`).

The run was at `main` 570e1faf6 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_agent_runbook.py` reaches `agent_start.py` but reads `docs/`, so it was left out, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workspace_dataflows_service.py` | test_workspace_dataflows (+ test_workspace_dataflows_mutation) | 13 of 13 (13 + 0) | 12 / 1 | **92.3%** | 12/13 → 13/13 | none | 13 / 0 | **100%** | none |
| `known_folders.py` | test_windows_folders_and_app_control_edges, test_notebook_formats_service_edges, test_prove_service_mutation (+ test_known_folders_mutation) | 24 of 24 (24 + 0) | 21 / 3 | **87.5%** | 21/24 → 24/24 | none | 24 / 0 | **100%** | none |
| `agent_start.py` | test_agent, test_agent_clock_and_folder (+ test_agent_start_mutation) | 26 of 26 (25 + 1) | 18 / 8 | **69.2%** | 18/25 → 25/25 | 0/1 → 1/1 | 26 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workspace_dataflows_service.py**:
  - The newest 50 refreshes are listed: 49 and 50 are listed whole, and 51 or 52 are cut to the first 50.
  - → `test_workspace_dataflows_mutation.py`.
- **known_folders.py**:
  - Windows is asked only where a folder is, never to create or check it, with no token.
  - Windows is asked for the whole folder id, all 16 bytes of it.
  - An answer Windows marks as failed is no answer, even when a path came back, and so is an empty one.
  - → `test_known_folders_mutation.py`.
- **agent_start.py**:
  - The record is kept under 256 KB.
  - A reason given as text keeps 2000 characters; a longer one is cut to 1999 and an ellipsis.
  - Only a crash lists its frames, a crash given as text lists none, and a deep crash lists its last 12.
  - A new entry that brings the file exactly to the limit is added with nothing dropped; one byte more drops the older half.
  - The trim keeps the newer half from the first whole line after its middle.
  - → `test_agent_start_mutation.py`.

### Left

None.

