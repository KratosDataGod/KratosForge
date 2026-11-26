# Mutation testing, batch 373

One module not scored before, run with `--kinds all`:
- Talking to the platform: the SQLite platform on this PC with its "refused" rows, UDF_Platform in Fabric with how each answer is read, and issuing, checking and exporting surrogate keys in batches (`central/client.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 108 mutants, so all were run. `main` hasn't changed this module since.

The first run used test_central_client_cov3, test_request_retries, test_platform_retries, test_typed_errors and test_reply_text_one_line, and scored 71 / 108 (65.7%). Each survivor was run again with test_shared_keys, test_platform, test_platformtest, test_versioned_contracts and test_central_service_cov3 added, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/client.py` | test_central_client_cov3, test_request_retries, test_platform_retries, test_typed_errors, test_reply_text_one_line, test_shared_keys, test_platform, test_platformtest, test_versioned_contracts, test_central_service_cov3 (+ test_central_client_mutation) | 108 of 108 (106 + 2) | 100 / 8 | **92.6%** | 99/106 → 106/106 | 1/2 → 2/2 | 108 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/client.py**:
  - Keys are issued 20,000 to a call by default: 20,001 keys take two calls.
  - A missing key table that the person can't make is refused with the way to request it.
  - `exists` is what the platform's key statistics say.
  - A batch without a count of new keys adds none.
  - A refused change gets its "refused" row, and a refused read doesn't.
  - Each call is posted with a five-minute timeout.
  - An answer of exactly 400 with a JSON object and no errors is still the platform's refusal, with the answer's text.
  - → `test_central_client_mutation.py`, with stand-in answers and a stand-in key service.
