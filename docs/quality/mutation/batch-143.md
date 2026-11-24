# Mutation testing, batch 143

Tables scheduled in two places (`workloads/overlap.py`): an agent and this PC, or two agents, running the same tables.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 53 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/overlap.py` | test_schedule_overlap (+ test_schedule_overlap_mutation) | 53 of 53 (52 + 1) | 46 / 7 | **86.8%** | 46/52 → 51/52 | 0/1 → 1/1 | 52 / 1 | **98.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/overlap.py**:
  - A schedule that can't be described has empty text.
  - An agent's tables against every table on this PC are the agent's tables.
  - A schedule on this PC that doesn't say whether it is enabled counts, and keeps its text, or empty text when it has none.
  - A record without an agent name is left out.
  - Of two records with the same heartbeat, the later one speaks.
  - → `test_schedule_overlap_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/overlap.py` | 80 | `known[i + 1:]` to `known[i:]` | Equivalent: the extra pair is a schedule with itself, which is skipped as the same place. |

