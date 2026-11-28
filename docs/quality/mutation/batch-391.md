# Mutation testing, batch 391

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The Academy validators that grade what a learner made: schema, data quality, settings, artifacts, Delta logs, pipeline runs and unchanged tables (`academy/validators.py`, ACD-004).

The run was at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 499 of 499 mutants ran (478 point + 21 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed). Grading here runs in the engine's own process on files in a temporary workspace; nothing needs the exercise sandbox.

The set is test_academy_validators and test_academy_configlab, about 8 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out. Near the end of the "after" run the machine reported a process killed for lack of memory, so mutants 470 to 498 were run again on their own: all 29 were killed again.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/validators.py` | test_academy_validators, test_academy_configlab (+ test_academy_validators_mutation) | 499 of 499 (478 + 21) | 382 / 117 | **76.6%** | 380/478 → 478/478 | 2/21 → 21/21 | 499 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/validators.py**:
  - Exact messages, which the other tests read by a word: each names the column, not its type, and the first missing key column.
  - Every bound at its value: row counts, ranges with one end or both, count_min and count_max, min and max, min_bytes and max_bytes, versions, data files and average file size.
  - Defaults: other columns, any column order and any operation order are fine unless asked; an added file with no size counts as 0 bytes.
  - Rules that can't run (unknown rule, unknown column, a range with no bounds, accepted with no values) say why.
  - A list position past the end isn't set. How a value is said: an object, non-ASCII text, 40 and 41 characters, a list of 8 and of 9, a list holding null.
  - Delta logs: 2,000 commits are read and 2,001 refused, a table folder named "checkpoint" is fine, a commit that isn't UTF-8 JSON objects is refused, names are cut at 40, eight names are shown and the rest counted, and the pass says version and file in the singular and plural. `_safe_data_path` returns False, not just something falsy, for each unsafe path.
  - unchanged names eight tables and counts the rest, in its pass and its fail.
  - → `test_academy_validators_mutation.py`.
