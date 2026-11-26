# Mutation testing, batch 505

A re-score of one module, run with `--kinds all`:
- The OneLake write queue: uploads in the background while a cell runs, its memory cap, a cell's wait, and flush, cancel and busy (`write_queue.py`). It was last scored in batch 1 at 81.9% before and 96.8% after (point mutants only), and its boundary mutants in batch 160 at 25.0% before and 75.0% after (`--kinds boundary`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 100 mutants (96 point + 4 boundary), so all were run. Some killed mutants stop the queue's workers, so their runs end only at a test's own wait (about 300 seconds); they were still well under the timeout.

The test set was the files of batches 1 and 160: test_write_queue_edges, test_onelake_writes, test_write_queue_mutation and test_write_queue_boundary.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0. The "after" run reran the survivors with `--only`. Nothing is uploaded: the writes are plain functions.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `write_queue.py` | test_write_queue_edges, test_onelake_writes, test_write_queue_mutation, test_write_queue_boundary (+ test_write_queue_mutation_b) | 100 of 100 (96 + 4) | 95 / 5 | **95.0%** | 92/96 → 93/96 | 3/4 → 3/4 | 96 / 4 | **96.0%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **write_queue.py**:
  - A flush that is interrupted puts its writes back in front of the pending list. A write that came in while it waited stays there too, and nothing is lost. The older test had no write arrive during the wait, so replacing the first pending write instead of inserting passed.
  - → `test_write_queue_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `write_queue.py` | 30 | `if TYPE_CHECKING` negated | Equivalent, as batch 1 said: it only moves the pyarrow import to module load. |
| `write_queue.py` | 227 | `self._running = 0` to `1` | Equivalent, as batch 1 said: `_running` is never read. |
| `write_queue.py` | 245 | `return self._release(nbytes)` to `return None` | Equivalent, as batch 1 said: `_release` returns None. |
| `write_queue.py` | 197 | `b > end` to `>=` in `upload` | Equivalent, as batch 160 said: a span that ends where the last one ended adds 0. |
