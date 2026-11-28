# Mutation testing, batch 183

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- A metadata project itself: its layers file, entities and their summaries, exceptions, custom code, the save gate, the run order and what is downstream of an entity (`meta/project.py`).

Batch 14 sampled 150 of this module's 452 mutants then (56.7% before, 98.7% after). It now has 464, and this run ran all 464 of 464.

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 182: tests first passed on the unmutated copy, and paths were relative to `sidecar/`. Each pytest session got a fresh `--basetemp` folder of its own, from a small run-only plugin that isn't committed. A fixed per-lane `--basetemp` (batch 182) isn't enough: pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session. After the run, no folder in that tree was nested 40 levels deep. Three kills picked at random were checked by hand, as in batch 182, and each failed real tests (9, 2 and 5).

Every file that calls the module was timed. The set below runs in about 25 seconds on this busy machine. Seven slower files were left out of it to keep each mutant's run short: test_meta (about 35 s), test_keys (15 s), test_meta_engine_edges (10 s), test_schema_evolution, test_shared_keys, test_breaking_changes (about 7 s each) and test_batches (5 s). The score counts only the files given, as in earlier batches. A second run of the survivors with those seven files added was stopped: under the machine's load that set took more than the tool's 120-second limit, so survivors would have been scored as timeouts that weren't hangs. One already had been. No `-k` filter or `--deselect` was needed. `test_meta_atomic_save.py` reads `meta/project.py`'s source only to check that it never calls `.write_text(`, which no mutant adds, so it stays in, as in batch 14.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways.

Two mutants timed out, and they count as killed. Both are real hangs that batch 14 also found: in `impact`'s loop, negating its test of which entities to add, or swapping `and` for `or` in it (line 863), adds the same entities again on every round.

**Rechecked on 2026-11-24.** On a machine this loaded a mutant can time out without hanging, so both were applied again, alone, to `sidecar/` as it was at the batch's commit and the set run with a 1,500-second limit instead of 120. Both really never end, and the numbers don't change: each was stopped by the test suite's own 300-second limit for one test, with the stack in `impact`'s loop.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/project.py` | test_meta_project_edges, test_meta_project_mutation, test_meta_atomic_save, test_meta_compare_edges, test_meta_compare_totals, test_meta_compare_mutation, test_meta_source_systems_edges, test_project_version_walk, test_versioned_contracts, test_lineage, test_meta_lineage_mutation, test_model_date_table, test_platform_service_paths, test_meta_service_keys_edges, test_meta_keys_edges (+ test_meta_project_uncapped) | 464 of 464 (452 + 12), uncapped | 401 / 63 | **86.4%** | 391/452 → 447/452 | 10/12 → 12/12 | 459 / 5 | **98.9%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/project.py**:
  - A lookback in minutes, hours or days is that long, and one given as a whole number is that number (0 included). Max deletes given as a whole number is a count (0 included). True, False and negative numbers are refused.
  - A layer's lakehouse capitalises its first letter. An entity is in `dbo` unless its name has a schema, and its table is the last part of its name. An entity with no sections still has a logic hash.
  - The layers file writes a description and a workspace only when they are set.
  - An entity of a layer the project doesn't have sorts last (99).
  - Exceptions are read from their file, and there are none without it.
  - Purging code forgets `transforms` and its modules, not a module whose name only starts the same way, and puts the project folder first on the path.
  - An entity summary falls back to "custom step" and reads its criticality only from an `[objectives]` table. The project summary counts each layer's entities and names the entities that use each key table.
  - An expression names only the columns that aren't there. A custom function may take more than `(df, ctx)`.
  - `batch_by` must name a contract column. An entity name needs every part and no path characters; three parts are allowed and four aren't.
  - In the save gate: a lookback on a file source needs a Delta source; a watermark the source renames must be given by its new name; a not-null check alone isn't a unique check; a clean rule on an unknown column, an unknown `on_fail`, and `exists_in` on a missing column are each named; `exists_in` and a lookup on an entity that isn't managed are read problems only; a lookup without an `at` column is fine; custom code needs a good reference, an owner and a test file that exists; an exception needs both a reason and an approver.
  - New layers are kept when only problems that have nothing to do with layers remain.
  - → `test_meta_project_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/project.py` | 230, 369, 389 | `usedforsecurity=False` to `True` | Equivalent: the flag only says what the hash is for; SHA-1 gives the same digest either way. |
| `meta/project.py` | 710 | the default `overwrite_schema` of `False` to `True` | Equivalent: the default is only checked to be a boolean, and `True` is one too. |
| `meta/project.py` | 828 | `state[n] = 2` to `3` | Equivalent: `find_cycle` only asks whether a state is 1 (on the stack) or missing, so any other number means done. |

