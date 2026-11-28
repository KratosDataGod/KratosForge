# Mutation testing, batch 479

One module not scored before, run with `--kinds all`:
- The cross-platform compatibility matrix (OSSIE-015a): one semantic definition (a model.bim, a TMDL folder or an Ossie document) against Power BI, Ossie 0.2, a Fabric Semantic View and Snowflake's Ossie 0.1.1. Each row has a status and a result no better than "Structurally compatible but behavior unverified", the fidelity counts, what the target drops, the L2 parity report, and what happens to every security role and calculation group (`semantic_matrix.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 255 mutants, so a sample of 150 was run.

The test set was test_semantic_matrix.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Where the fixture model doesn't reach a case (an object the converter couldn't read, a target that lacks the model object, an object Snowflake lacks), the new tests change the real parity report by hand before the matrix reads it. Nothing reaches Fabric or Snowflake.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semantic_matrix.py` | test_semantic_matrix (+ test_semantic_matrix_mutation_b) | 150 of 255 (148 + 2) | 113 / 37 | **75.3%** | 113/148 → 147/148 | 0/2 → 2/2 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **semantic_matrix.py**:
  - A kind given in any case is read; an unknown kind, or a Semantic View as the source, is refused with its reason. Bytes that aren't UTF-8 are named.
  - Ossie JSON text is read up to its 20 MB cap and refused one character past it, with the size in MB. A model.bim past that size isn't held to the Ossie cap.
  - A refused row has nothing dropped and names each role and calculation group. A source row says where its roles are: as the converter read them, or only in the POWER_BI extension.
  - A failed parity report gives its error as the row's reason.
  - Objects the converter couldn't read are counted in a note; one that is only unverified isn't counted.
  - The model object is never judged, even when a target lacks it.
  - The view's note counts only the kept measures L2 can't read. Every warning on a role or group is prominent. The summary counts security warnings only.
  - A lost object from a fidelity report is listed once by its key, with its reason, then its remediation, then the default reason. A converter or Power BI import report's object without a key is listed. A fidelity report that can't be built notes its error.
  - The listing shows 50 objects and counts the rest.
  - Snowflake's skipped metric is a measure and a skipped field a column. A path to a dataset that isn't there has no key. An object Snowflake lacks is dropped unless the export kept it in the extension.
  - Redacting keeps the reason on a dropped object that isn't a role.
  - → `test_semantic_matrix_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `semantic_matrix.py` | 510 | the summary's judged results: `non-source rows or all rows` to `and` | Equivalent: at most one of the four rows is the source, so the first list is never empty, and `and` then gives all rows. That adds the source row, whose result is the ceiling, the best rank, so the worst result is the same. |

**Main changed after this run (2026-11-28):** #3370 (Power BI-only roles kept through extensions, and Semantic View relationships reaching TMDL) changed `semantic_matrix.py` after the run. The score above is for the module at the run's `main`, as named on this page, and it was not run again on the new module. The new tests pass unchanged on today's main.
