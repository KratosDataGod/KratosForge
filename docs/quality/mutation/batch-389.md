# Mutation testing, batch 389

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Prove this notebook: the check that sorts what a notebook does into blockers, warnings and notes before a proof, whether it runs as Spark, and the cells that make the Fabric run a proof (`proof.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 92 mutants, and the run ran all 92 of 92 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_prove_spark, test_compute_check_ast, test_fabric_step, test_spark_refusals, test_proof_spark_uncapped, test_prove_spark_install, test_dbutils, test_notebook_magics and test_prove, about 50 seconds together. Other test files reach the module through the proof service; they were left out, and the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (22, 6 and 10).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof.py` | test_prove_spark, test_compute_check_ast, test_fabric_step, test_spark_refusals, test_proof_spark_uncapped, test_prove_spark_install, test_dbutils, test_notebook_magics, test_prove (+ test_proof_mutation) | 92 of 92 (91 + 1), uncapped | 69 / 23 | **75.0%** | 69/91 → 91/91 | 0/1 → 1/1 | 92 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof.py**:
  - A finding names its cell counted from 1, its line, and its code. A cell that starts with a blank line puts the finding on line 2.
  - Code of 80 characters is kept, and longer code is cut to 77 characters and "...".
  - A markdown cell is never read as code.
  - A %sql line that only reads is no blocker. One that changes tables names its own line, with lines after it.
  - A reason from compute_check without a cell is said again, except Fabric's when the missing lakehouse was already said. A reason in a cell names that cell.
  - A notebook runs as Spark when it has SQL (a %%sql cell or a %sql line, in any code cell) and not the lakehouse module. A markdown cell's SQL doesn't count.
  - A Spark proof reads schemas unless told not to.
  - → `test_proof_mutation.py`.

### Left

None.
