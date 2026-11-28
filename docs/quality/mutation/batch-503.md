# Mutation testing, batch 503

One module not scored before, run with `--kinds all`:
- Full paths for the Windows programs the engine starts (taskkill, schtasks, cmd, explorer), under the Windows folder from `SystemRoot`, so a program of the same name in the current folder is never started instead (`winexe.py`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 4 mutants, so all were run.

The test set was test_winexe. Its tests set `SystemRoot` with `monkeypatch`, so they run on any OS and start nothing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `winexe.py` | test_winexe | 4 of 4 (4 + 0) | 4 / 0 | **100.0%** | 4/4 → 4/4 | 0/0 → 0/0 | 4 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

None: the existing tests killed every mutant. No new tests needed.

### Left

None.
