# Mutation testing, batch 322: agent_status.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 56 ran all of its 40 mutants (52.5% before, 100% after).
- What each agent publishes about itself, `agents/<name>.json` in each environment's control area (`agent_status.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`, so all 40 of 40 mutants ran (40 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 320.

The set is the status tests and the agent tests that read the published record: test_agent_status, test_agent_status_mutation, test_agent_fleet, test_agent_silent_failures and test_agent_takeover. The score counts only the files given. No `-k` filter was used. No mutant's run timed out. 13 mutants took about 68 s each: a test waiting for the agent's status file fails at its own deadline, well inside the mutator's 120 s. Four killed mutants (lines 47, 71, 71 and 93, three of them among those 13) were run again by hand, and each was killed again.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent_status.py` | test_agent_status, test_agent_status_mutation, test_agent_fleet, test_agent_silent_failures, test_agent_takeover | 40 of 40 (40 + 0) | 40 / 0 | **100%** | 40/40 → 40/40 | none | 40 / 0 | **100%** | none |

No bugs were found. No new tests were needed: every mutant was killed by the existing tests.
