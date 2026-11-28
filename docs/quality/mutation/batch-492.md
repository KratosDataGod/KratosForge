# Mutation testing, batch 492

One module not scored before, run with `--kinds all`:
- Generic credentials in Windows Credential Manager: save, read and remove one by its target name, kept for this Windows account on this PC, with errors that name the credential and never its secret. Off Windows reads find nothing and saves are refused (`credman.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 42 mutants, so all were run.

The test set was test_credman_dpapi_calls and test_platform_notify_service_uncapped.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`. Windows is stood in for on any OS: a fake advapi32 is set in `ctypes.WinDLL` and `sys.platform` is set for the test. No real credential is touched.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `credman.py` | test_credman_dpapi_calls, test_platform_notify_service_uncapped (+ test_credman_mutation_b) | 42 of 42 (40 + 2) | 34 / 8 | **81.0%** | 32/40 → 40/40 | 2/2 → 2/2 | 42 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **credman.py**:
  - The numbers Windows reads are its own: a generic credential is 1, kept on this PC is 2, and "not found" is 1168. The older tests compared with the module's own names, so a changed number passed.
  - A save has no flags and no attributes, and the save, read and remove calls pass 0 for their flags.
  - Only error 1168 reads as "nothing there"; 1169 is an error that names its number.
  - → `test_credman_mutation_b.py`.

### Left

None.
