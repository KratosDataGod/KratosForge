# Mutation testing, batch 222

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The OneLake write queue: the memory cap, each table's writes in order, the workers, a flush and a cancelled one, spills and the cell's write stats (`write_queue.py`).

Batch 1 ran 94 of this module's mutants then (81.9% before, 96.8% after), and batch 160 re-scored its 4 boundary mutants (25.0% before, 75.0% after). It now has 100, and this run ran all 100 of 100.

The run was at `main` 293fb38daa (2026-11-23), with the method of batch 218: tests first passed on the unmutated copy, paths were relative to `sidecar/`, and each pytest session got a fresh `--basetemp` folder of its own from a small run-only plugin that isn't committed and that removed the folder when the session ended. A fixed per-lane `--basetemp` isn't enough, because pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session and score each later mutant as killed. The plugin's note of any folder nested 40 levels deep was cleared by the start of the "after" run before it was read, as in batch 221, so that check is missing here; the last kills of the run don't have near-identical times. Three kills picked at random were checked by hand, as in batch 182: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (5, 1 and 2).

The set is every test file that uses the module, about 20 seconds in all, so no file was left out. No `-k` filter or `--deselect` was needed.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways, with DuckDB 1.5.6 and 2.0.0.

Eight mutants timed out, and they count as killed. Each was read, and each is a real hang: with no worker started (lines 270 twice), a worker that stops at once or takes a write that isn't there (277 twice, 283), a write that never finishes (244), or a write that waits for memory that is never given back (236 twice), a flush waits for ever.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `write_queue.py` | test_job_v_order, test_onelake_writes, test_retention, test_write_queue_boundary, test_write_queue_edges, test_write_queue_mutation (+ test_write_queue_uncapped) | 100 of 100 (96 + 4), uncapped | 95 / 5 | **95.0%** | 92/96 → 95/96 | 3/4 → 3/4 | 98 / 2 | **98.0%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **write_queue.py**:
  - Importing the queue doesn't load pyarrow: every cell makes a queue and few write. Batch 1 left this mutant as nothing a test can see; a test in a new process sees it.
  - A cancelled write gives back the memory it held, so the next write doesn't wait for it. Batch 1 left line 245 as equivalent because `_release` returns None; the mutant drops the call to `_release` too.
  - A flush stopped part way puts its writes back before newer ones.
  - → `test_write_queue_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `write_queue.py` | 227 | `self._running = 0` to `1` | Equivalent, as batch 1 found: `_running` is never read. |
| `write_queue.py` | 197 | `b > end` to `b >= end` in `Stats.upload` | Equivalent: a span ending exactly where the last one ended adds `b - max(a, end)`, which is 0, and leaves `end` as it was. |
