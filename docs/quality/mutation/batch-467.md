# Mutation testing, batch 467

One module not scored before, run with `--kinds all`:
- The Excellence assessment's controls on table layout, from each Delta table's log: many small files (FEA-PERF-003), a VACUUM backlog (FEA-PERF-004) and a Delta feature the SQL analytics endpoint or Direct Lake can't read (FEA-PERF-005), with the evidence record they read (`excellence/catalog_performance.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 128 mutants, so all were run.

The test set was test_excellence_performance, test_excellence_catalog and test_excellence_hardening.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor was left by the first "after" run. It got a test and was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_performance.py` | test_excellence_performance, test_excellence_catalog, test_excellence_hardening (+ test_excellence_catalog_performance_mutation_b) | 128 of 128 (124 + 4) | 80 / 48 | **62.5%** | 80/124 → 124/124 | 0/4 → 4/4 | 128 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_performance.py**:
  - A table with an error is unread even when its health came back, and one with no health says its log wasn't read.
  - A log without numbers counts 0 files, bytes and versions, not 1, and the numbers it has are kept.
  - Every message is worded for one table and for several: "1 table has", "2 tables have", "None of the 1 table", "1 table couldn't be read", "1 table past the read budget" and so on, for all three controls and the not-evaluated text.
  - A message lists five names in full. A sixth is "and 1 more", a seventh "and 2 more", and exactly five has no "and 0 more".
  - The thresholds at their edge: 20 files is many and 19 isn't; an average of exactly 32 MB isn't small and one byte less is; 30 versions since VACUUM is a backlog and 29 isn't.
  - Sizes are in MiB (1024 × 1024), and the pass text and description say "32 MB".
  - → `test_excellence_catalog_performance_mutation_b.py`.

### Left

None. Every mutant is killed.
