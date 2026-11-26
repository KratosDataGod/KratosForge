# Mutation testing, batch 134

Two small workload modules not covered before, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Reading the status records agents publish, when an agent is live, offline or stopped, comparing its version with this app's, and the fleet list built from every environment's records (`workloads/agents.py`).
- The likely category of a failed run's error message, and whether retrying alone helps (`workloads/classify.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/agents.py` | test_agent_fleet, test_incidents (+ test_workload_agents_mutation) | 45 of 45 (42 + 3) | 40 / 5 | **88.9%** | 40/42 → 42/42 | 0/3 → 2/3 | 44 / 1 | **97.8%** | 1 equivalent |
| `workloads/classify.py` | test_incidents, test_retrying (+ test_workload_classify_mutation) | 16 of 16 (16 + 0) | 12 / 4 | **75.0%** | 12/16 → 15/16 | none | 15 / 1 | **93.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/agents.py**:
  - Only status records that are JSON objects of kind `agent_status` are read; another kind, a list, text that isn't JSON and a missing file are skipped.
  - An agent is offline from exactly its stale time, and live one second before.
  - An older heartbeat read later doesn't speak for the agent, and of two equal heartbeats the first record read does.
  - → `test_workload_agents_mutation.py`.
- **workloads/classify.py**:
  - Each category says whether retrying alone helps: throttled, network and table busy do; access, source missing, schema, data quality and code don't; an unrecognized message doesn't say.
  - → `test_workload_classify_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/agents.py` | 56 | `a < b` to `<=` for older or newer | Equivalent: equal versions are answered as `same` just before, so `a` and `b` are never equal here. |
| `workloads/classify.py` | 16 | `429` to `430` in the statuses left out of the network rule | Equivalent: 429 is matched by the throttled rule, which comes first, so the network rule never decides it. |

