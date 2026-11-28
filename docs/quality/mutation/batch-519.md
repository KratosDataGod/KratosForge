# Mutation testing, batch 519

A re-score of `orchestration/mashup.py`, which reads a Dataflow Gen2 definition (its `mashup.pq` queries, destinations and connections) for the workspace page. Batch 56 left it at **94.7%**, and batch 249 at **94.7%** (kinds all), each with 8 equivalent mutants. Since then a definition part that Fabric didn't send as whole base64 text is named as a problem, so the module has grown from 168 mutants to 173.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The test set takes about 2 seconds, so the tool's own 120-second limit was kept. Six mutants time out, as in batch 249, because they stop the scanner moving past a comment or a string. Each was run again alone with a 300-second limit, and each timed out again, so a timeout counts as killed. Each pytest session got a fresh temporary folder. The module has 173 mutants, so a sample of 150 was run. The test set was batch 249's: test_workspace_dataflows and test_mashup_mutation. They first passed once on the unmutated copy, and none of them reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/mashup.py` | test_workspace_dataflows, test_mashup_mutation (+ test_mashup_mutation_b) | 150 of 173 (141 + 9) | 140 / 10 (6 timeouts) | **93.3%** | 135/141 → 137/141 | 5/9 → 5/9 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/mashup.py**:
  - A part Fabric didn't send as whole base64 text is named by the first 100 characters of its path (99 and "…").
  - A part whose path has no printable character is named "A part".
  - → `test_mashup_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/mashup.py` | 51 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent, as batches 56 and 249 said: a line comment starts with `//` at `i`, so the newline found from `i` is never at 0. |
| `orchestration/mashup.py` | 54 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent, as batches 56 and 249 said: the close is searched from `i + 2`, so it is never found at 0 or 1. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i > 0`, and to `i < 0` | Equivalent, as batches 56 and 249 said: the closing quote is searched from after the opening one, so it is never found at 0; without the doubled-quote loop, the second quote of a pair opens a new string at once, and the scan ends at the same place. |
| `orchestration/mashup.py` | 88 | `i < 0` to `i < 1`, and to `i <= 0` | Equivalent, as batches 56 and 249 said: as on line 86, a found quote is never at 0. |
