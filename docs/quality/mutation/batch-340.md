# Mutation testing, batch 340

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Learn labs on this PC: start, run, check, hint and reset a module's exercises (`learn/labs.py`, LX-03).

The run was at `main` 062d120bb (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 106 of 106 mutants ran (105 point + 1 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_learn_labs, test_learn_labs_edges, test_learn_service and test_learn_service_mutation, about 30 s a session. test_academy_limits (15 s), which lowers the module's `TIME_LIMIT` for an Academy query, was left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/labs.py` | test_learn_labs, test_learn_labs_edges, test_learn_service, test_learn_service_mutation (+ test_learn_labs_mutation) | 106 of 106 (105 + 1) | 89 / 17 | **84.0%** | 89/105 → 104/105 | 0/1 → 1/1 | 105 / 1 | **99.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **learn/labs.py**:
  - A lab starts and resets with seed 7, and a lab folder that is already there (without its lab.json) is used.
  - A run gives at most 1,000 rows and at least 1, and `more` is false when the rows are exactly the limit.
  - A run's time is in milliseconds.
  - Hint 0 is the first hint.
  - The time limit's timer is a daemon thread.
  - A table check whose SQL fails doesn't pass and says so; a practices check looks for the rules it names; a speed check runs 3 times for twice as fast by default.
  - → `test_learn_labs_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `learn/labs.py` | 43 | the second "Not a learning module" raise → `pass` | Equivalent: an id `content.is_id` takes (`LM-` or `LAB-` and two digits) always resolves to a folder directly under `learn`. |
