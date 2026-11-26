# Mutation testing, batch 495

One module not scored before, run with `--kinds all`:
- Windows DPAPI for files that hold secrets, such as the sign-in token cache: `protect` and `unprotect` with this Windows account's key, a purpose kept with the data, and never a prompt (`dpapi.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 11 mutants, so all were run.

The test set was test_credman_dpapi_calls, test_token_cache_dpapi, test_sealed_settings and test_sealed_settings_mutation.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`. Windows is stood in for on any OS: fake crypt32 and kernel32 are set in `ctypes.WinDLL`. Nothing is sealed for real.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dpapi.py` | test_credman_dpapi_calls, test_token_cache_dpapi, test_sealed_settings, test_sealed_settings_mutation (+ test_dpapi_mutation_b) | 11 of 11 (11 + 0) | 10 / 1 | **90.9%** | 10/11 → 11/11 | 0/0 → 0/0 | 11 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **dpapi.py**:
  - The "never show a prompt" flag is Windows' own CRYPTPROTECT_UI_FORBIDDEN, 0x1, on both calls. The older test compared with the module's own name, so a changed number passed.
  - → `test_dpapi_mutation_b.py`.

### Left

None.
