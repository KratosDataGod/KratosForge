# Mutation testing, batch 498

One module not scored before, run with `--kinds all`:
- Embed Studio's engine calls: a Power BI token for the embed frame with at least 10 minutes left, and what the frame needs for a report, paginated report or dashboard, with a refused token retried once with a new one (`powerbi_embed_service.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 5 mutants, so all were run.

The test set was test_powerbi_embed, test_powerbi_embed_service_edges and test_powerbi_embed_mutation.

Every mutant was killed, so no new tests were needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `powerbi_embed_service.py` | test_powerbi_embed, test_powerbi_embed_service_edges and test_powerbi_embed_mutation | 5 of 5 (5 + 0) | 5 / 0 | **100.0%** | 5/5 → 5/5 | 0/0 → 0/0 | 5 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **powerbi_embed_service.py**: there were none; no new tests needed.

### Left

None.
