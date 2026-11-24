# Mutation testing, batch 146

One list of running, queued and next work across this PC, each agent and Fabric (`workloads/queue.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 105 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/queue.py` | test_workload_queue (+ test_workload_queue_mutation) | 105 of 105 (101 + 4) | 81 / 24 | **77.1%** | 81/101 → 99/101 | 0/4 → 4/4 | 103 / 2 | **98.1%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/queue.py**:
  - A live agent's schedule that is due is next, with no note; an offline agent's at exactly now is missed, and a later one says it is from the last report.
  - The window ends at exactly 24 hours by default, for agents and for this PC.
  - A schedule on this PC that doesn't say whether it is enabled runs, and keeps its environment and empty text.
  - Fabric rows without names say what they are.
  - A shared schedule held by one agent is one row. One past due with a live agent is next, and one at exactly now with no agent reporting is missed.
  - Rows sort by state (running, unknown, queued, missed, next), then time, then name.
  - → `test_workload_queue_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/queue.py` | 153 | `'next': 4` to `5` | Equivalent: next still sorts after every other state. |
| `workloads/queue.py` | 163 | the default order `9` to `10` | Equivalent: every row has one of the five states, so the default is never used. |

