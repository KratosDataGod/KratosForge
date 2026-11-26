# Mutation testing, batch 147

Who runs each workload and with what credential (`workloads/run_as.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 88 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/run_as.py` | test_run_as (+ test_run_as_mutation) | 88 of 88 (85 + 3) | 69 / 19 | **78.4%** | 69/85 → 84/85 | 0/3 → 3/3 | 87 / 1 | **98.9%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/run_as.py**:
  - A certificate expiring at exactly now is expired, at exactly 30 days is expiring, and later is ok.
  - An expiry with an offset keeps it, and one without is read as UTC.
  - A certificate whose dates couldn't be read is unknown, and so is a secret without a token; a secret with one is ok.
  - An older agent's credential kind follows its identity.
  - The newest record speaks whichever comes first, and on a tie the first one.
  - An agent in one environment names it.
  - This PC, Fabric jobs and Fabric schedules are live, and a schedule without names says so.
  - → `test_run_as_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/run_as.py` | 23 | `'expiring': 2` to `3` | Equivalent: expiring still sorts after expired and failed, the only other states listed. |

