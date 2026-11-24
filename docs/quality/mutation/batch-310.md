# Mutation testing, batch 310: workloads/agents.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 134 ran its 45 mutants (all of them, 88.9% before, 97.8% after).
- Reading the status records agents publish, when an agent is live, offline or stopped, and the fleet list built from every environment's records (`workloads/agents.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 45 of 45 mutants ran (42 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did: no folder 40 or more levels deep was left, the kills show no late streak of identical times, and kills checked by hand failed on a real assertion. No `-k` filter or `--deselect` was used, and no mutant's run timed out.

The set is batch 134's files and every other test file that imports the module: test_agent_fleet, test_incidents, test_workload_agents_mutation. The score counts only the files given.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/agents.py` | test_agent_fleet, test_incidents, test_workload_agents_mutation | 45 of 45 (42 + 3) | 44 / 1 | **97.8%** | 42/42 → 42/42 | 2/3 → 2/3 | 44 / 1 | **97.8%** | 1 equivalent |

No bugs were found, and no new tests were needed: every survivor is equivalent, so the module has no new test file.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/agents.py` | 56 | `a < b` to `<=` for older or newer | Equivalent, as in batch 134: equal versions are answered as `same` just before, so `a` and `b` are never equal here. |

