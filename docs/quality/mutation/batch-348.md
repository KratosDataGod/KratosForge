# Mutation testing, batch 348

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The notebook kernel's own process: the JSON lines it reads and writes, the calls it makes back to the engine, and how it starts (`kernel_host.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 116 mutants, and the run ran all 116 of 116 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_kernel_host_coverage, test_kernel_process_edges and test_debugger_robust, about 22 seconds together. test_kernel_host_coverage drives the module in this process; the other two also start it as a process of its own. test_kernel_process (about 55 seconds) and test_capacity_blocked_every_route (about 22 seconds, one use of the module) also reach it, and were left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (41, 1 and 2).

Two mutants timed out, and were counted as killed: both turn the check in `Channel.answer` around, so an answer from the engine never wakes the call waiting for it.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kernel_host.py` | test_kernel_host_coverage, test_kernel_process_edges, test_debugger_robust (+ test_kernel_host_mutation) | 116 of 116 (115 + 1), uncapped | 103 / 13 (2 of the 103 timed out) | **88.8%** | 103/115 → 113/115 | 0/1 → 1/1 | 114 / 2 | **98.3%** | 2, not equivalent (see Left) |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **kernel_host.py**:
  - The longest line the process reads is 64 MB: a line of exactly 64 MB is read, and one character more is refused as "longer than 64 MB", with its length on stderr.
  - A refused request names only the first 40 characters of its op.
  - A request runs on a daemon thread named "request-" and the first 40 characters of its op.
  - → `test_kernel_host_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `kernel_host.py` | 293 | `if __name__ == "__main__"` negated | Not equivalent, and not caught only because of how the score is read. With this mutant, importing the module runs `main()`: it takes over stdin and stdout and ends the process with exit code 0 when stdin closes. Each test file imports the module, so pytest ends with exit code 0 while collecting, before any test runs, and `mutate.py` reads exit code 0 as "survived". Checked by hand: pytest printed nothing and returned 0. The new test that importing the module does not start the protocol fails on this mutant when run in a process of its own, but in the scored run pytest ends first. |
| `kernel_host.py` | 293 | `==` to `!=` in the same line | The same as the line above. |
