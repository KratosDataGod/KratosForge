# Mutation testing, batch 450

One module not scored before, run with `--kinds all`:
- Semantic parity: a model's structure and measures, read from model.bim, TMDL or an Apache Ossie file, compared level by level with another, with a reason for each object that can't be compared and notes on what each side's text means (`semantic_parity.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 1,088 mutants, so a sample of 150 was run. `main` has changed this module since (#3255, #3350, #3351); the new tests pass against today's version.

The test set was test_semantic_parity, test_semantic_parity_l2, test_semantic_parity_l3, test_semantic_parity_l3_cov32 and test_semantic_view.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semantic_parity.py` | test_semantic_parity, test_semantic_parity_l2, test_semantic_parity_l3, test_semantic_parity_l3_cov32, test_semantic_view (+ test_semantic_parity_mutation_b) | 150 of 1,088 (149 + 1) | 116 / 34 | **77.3%** | 116/149 → 146/149 | 0/1 → 1/1 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **semantic_parity.py**:
  - A DAX or SQL expression outside the subset says exactly why: no expression, the token it stopped at, a bare column, NULLIF with one argument.
  - Two different texts outside the subset don't say the text is the same. A single aggregate and a divide are shown as forms.
  - DAX against DAX division has no SQL caveat. A DAX SUM against a SQL SUM has no AVG caveat. At most five other dialects are named.
  - Two TMSL measures with a home table note nothing about it.
  - The same relationship has no changes. A many-to-one relationship turned round has changed endpoints, and a symmetric one turned round is the same.
  - Hierarchy levels without ordinals, or with equal ones, still compare. An empty Ossie data type is no data type.
  - The id of an object only the fidelity report knows is built from its kind and name. A lost object of a kind L1 doesn't compare is no row.
  - A comparison that raises is "Test failed", with both sides shown.
  - The tolerance note names what each level compares, and at most 20 ignored tolerances are listed.
  - A side keeps 50 warnings and counts them all. Redacted output holds back role warnings and counts role changes.
  - → `test_semantic_parity_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `semantic_parity.py` | 1203 | `return ""` dropped (returns None) at the end of the reason an SQL node is outside the subset | Equivalent: its one caller only tests the reason for truth, and None and "" are both false. |
| `semantic_parity.py` | 1136 | `lru_cache(maxsize=1)` to `maxsize=0` on loading the converter's SQL reader | Equivalent: it only stops the cache. The import is cached by Python anyway, and the same three objects come back. |
| `semantic_parity.py` | 208 | `cardinality[0]` to `cardinality[1]` in "X-to-Y relationship" | Equivalent: TMSL's cardinality is one or many, and one-to-many is turned round first. So a cardinality other than many-to-one is many-to-many or one-to-one, and both ends are the same. |
