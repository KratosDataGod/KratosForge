# Mutation testing, batch 402

One module not scored before, run with `--kinds all`:
- The warehouse live check: each documented local T-SQL case run in a test warehouse's scratch schema and compared with this PC's answer and the documented one (`warehouse_livecheck.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 182 mutants, so 150 were sampled. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which hasn't changed it since ea988e99. The "after" figures come from a `mutate.py --only` run of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_livecheck.py` | test_warehouse_livecheck, test_warehouse_livecheck_cov2 (+ test_warehouse_livecheck_mutation_b) | 150 of 182 (149 + 1) | 134 / 16 | **89.3%** | 133/149 → 147/149 | 1/1 → 1/1 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse_livecheck.py**:
  - A case is frozen, and runs in a transaction unless it says otherwise.
  - Draining a batch stops after 20 errors by default.
  - Two error answers are compared from just after "error:", so "error:ab" and "error:xb" differ.
  - A run without its own id gets 12 hex characters.
  - A refused run still writes its two results files, also into a folder that is already there, and counts each case once.
  - The summary names the cleanup only when there is one, cuts each answer to 200 characters, and shows the documented and warehouse answers, or "not documented" and "-".
  - → `test_warehouse_livecheck_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `warehouse_livecheck.py` | 288 | `a.startswith("error:") or b.startswith(...)` to `and` | Equivalent: when only one answer is an error, the mutant falls through to comparing them as numbers and then as text, which also gives False. |
| `warehouse_livecheck.py` | 499 | JSON `indent=2` to `3` | Equivalent, as in earlier batches: only the layout of a JSON file that is read back as JSON changes. |
