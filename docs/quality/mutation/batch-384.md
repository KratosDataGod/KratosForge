# Mutation testing, batch 384

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The platform's two clients: the SQLite one on this PC and the one that calls UDF_Platform in Fabric, plus surrogate keys taken from the platform (`central/client.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 108 mutants, and the run ran all 108 of 108 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_central_client_cov3, test_http_session, test_sqlite_stores, test_platform_refusals, test_typed_errors, test_platform_retries, test_shared_keys and test_platform, about 70 seconds together. Many more test files reach the module through the platform's services; they were left out, and the score counts only the files given. UDF_Platform is a scripted session in every test: nothing reaches a tenant.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on a survivor, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (111, 31 and 99, errors included).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/client.py` | test_central_client_cov3, test_http_session, test_sqlite_stores, test_platform_refusals, test_typed_errors, test_platform_retries, test_shared_keys, test_platform (+ test_central_client_mutation_a) | 108 of 108 (106 + 2), uncapped | 101 / 7 | **93.5%** | 100/106 → 106/106 | 1/2 → 2/2 | 108 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/client.py**:
  - Business keys go to the platform in batches of 20000: 20001 keys are two calls, of 20000 and 1.
  - An answer without "inserted" counts no new keys.
  - `exists` and `ensure` say True or False, never None.
  - A key table that isn't there and can't be made is refused by name, and no key is issued.
  - Any 4xx answer with a JSON object and no errors is a refusal with the answer's text, from 400 on.
  - Every call to UDF_Platform waits up to 300 seconds.
  - → `test_central_client_mutation_a.py`.

### Left

None.
