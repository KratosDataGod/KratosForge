# Mutation testing, batch 249

A re-score of two modules on today's `main`:
- `workloads/classify.py`, which batch 134 left at **93.8%** with 1 equivalent mutant.
- `orchestration/mashup.py`, which batch 56 left at **94.7%** with 8 equivalent mutants.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `workloads/classify.py` has 16 mutants, so all were run; `orchestration/mashup.py` has 168, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Six `orchestration/mashup.py` mutants time out, because they stop the scanner moving past a comment or a string, and a timeout counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/classify.py` | test_incidents, test_retrying, test_workload_classify_mutation | 16 of 16 (16 + 0) | 15 / 1 | **93.8%** | 15/16 → 15/16 | none | 15 / 1 | **93.8%** | 1 equivalent |
| `orchestration/mashup.py` | test_workspace_dataflows, test_mashup_mutation | 150 of 168 (140 + 10) | 142 / 8 | **94.7%** | 136/140 → 136/140 | 6/10 → 6/10 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found. No new tests were needed: each survivor is one that batch 134 or batch 56 found equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/classify.py` | 16 | `429` to `430` in the statuses left out of the network rule | Equivalent, as in batch 134: 429 is matched by the throttled rule, which comes first, so the network rule never decides it. |
| `orchestration/mashup.py` | 51 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent, as in batch 56: a line comment starts with `//` at `i`, so the newline found from `i` is never at 0. |
| `orchestration/mashup.py` | 54 | `end < 0` to `end <= 0` | Equivalent, as in batch 56: the close is searched from `i + 2`, so it is never found at 0. |
| `orchestration/mashup.py` | 73 | `end <= 0` to `end <= 1` | Equivalent, as in batch 56: the text starts with `/`, so a newline or `*/` is never found at 0, and `end` is never 1. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i >= 1`, to `i > 0`, and to `i < 0` | Equivalent, as in batch 56: the closing quote is searched from after the opening one, so it is never found at 0; without the doubled-quote loop, the second quote of a pair opens a new string at once, and the scan ends at the same place. |
| `orchestration/mashup.py` | 88 | `i < 0` to `i <= 0` | Equivalent, as in batch 56: as on line 86, a found quote is never at 0. |

