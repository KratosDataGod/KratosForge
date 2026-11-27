# Mutation testing, batch 372

One module not scored before, run with `--kinds all`:
- The kernel process's side of the protocol: the channel to the engine, the host that runs each request against the kernel, and taking or refusing each line the engine sends (`kernel_host.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 115 mutants, so all were run. `main` has changed this module since this run (the Prod guard passes on whether a write came from a cell); the new tests pass against today's version too.

The first run used test_kernel_host_coverage and test_secret_leaks, and scored 93 / 115 (80.9%). Each survivor was run again with test_debugger_robust added, which drives `dispatch` with malformed lines, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kernel_host.py` | test_kernel_host_coverage, test_secret_leaks, test_debugger_robust (+ test_kernel_host_mutation) | 115 of 115 (114 + 1) | 102 / 13 | **88.7%** | 102/114 → 112/114 | 0/1 → 1/1 | 113 / 2 | **98.3%** | 2 can't be seen from a test |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **kernel_host.py**:
  - A protocol message is at most 64 MB, and a line of exactly the limit is read.
  - The refusal of a longer line names the limit in megabytes.
  - Each request runs on a daemon thread named for its op, cut to 40 characters, and the notice of a skipped line names at most 40 characters of the op.
  - → `test_kernel_host_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `kernel_host.py` | 293 | `if __name__ == "__main__"` negated, and `==` to `!=` | Can't be seen from a test: importing the module then runs `main()`, which reads the test process's stdin to its end and calls `os._exit(0)`, so the pytest process ends with exit code 0 and the run counts as passed. The kernel process itself is started with `-m`, where the guard is true. |
