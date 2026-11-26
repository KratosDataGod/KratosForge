# Mutation testing, batch 257: meta/data_health.py (every mutant)

One module, run uncapped: every mutant. Batch 95 ran all of its 146 mutants already (81.5% before, 99.3% after); this run checks the score on the module as it is now.
- Data health from the Delta log: freshness, rows written per load against the usual, schema changes and the alerts they raise (`meta/data_health.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 146 of 146 mutants ran (140 point + 6 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own, and that folder was checked afterwards: nothing in it was nested 40 or more folders deep. The last mutant scored was the survivor, which ran the whole set, so the set still worked at the end. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/data_health.py` | test_data_health_mutation, test_data_health | 146 of 146 (140 + 6) | 145 / 1 | **99.3%** | no change needed | no change needed | 145 / 1 | **99.3%** | 1 equivalent |

No bugs were found. No new tests were needed: the one survivor is equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/data_health.py` | 163 | `distance > 0` to `>=` for high or low | Equivalent (as in batch 95): it is reached only for an unusual load, whose distance is beyond the band (at least 0), so it is never 0. |

