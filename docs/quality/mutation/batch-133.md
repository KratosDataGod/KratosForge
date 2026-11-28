# Mutation testing, batch 133

One engine module not scored before, run with `--kinds all`:
- The process a separate-runtime engine runs cells in: the protocol's lines on Windows and elsewhere, the session's start and its secrets, a cell's outputs and errors, interrupting, and the main loop (`engines/runtime_worker.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 59 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. The worker runs as a process of its own in the main tests, from the temporary copy, so those runs used the mutant too.

The first run used test_runtimes and test_tls_inspection. Each survivor was run again with test_dbconnect added, and the "before" figures are from that run. Two mutants of the main loop's `exec` branch (line 206) timed out in the first run and count as killed. The new tests run the worker in the test's own process against fakes. They passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/runtime_worker.py` | test_runtimes, test_tls_inspection, test_dbconnect (+ test_runtime_worker_mutation) | 59 of 59 (58 + 1) | 40 / 19 | **67.8%** | 40/58 → 57/58 | 0/1 → 1/1 | 58 / 1 | **98.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/runtime_worker.py**:
  - On Windows, the protocol's lines come from what PeekNamedPipe says has arrived: it waits 0.02 s when nothing has, joins a line sent in two parts, reads bytes that aren't UTF-8 as a replacement character, ends when the engine closes its end, and opens kernel32 with its last error kept. A peek reads nothing itself.
  - A version that can't be read is empty.
  - A Databricks Connect session's client is the installed distribution's version, or the module's own when that is empty.
  - A new worker isn't running, and a cell's end always sets it back to not running.
  - An empty cell runs and shows nothing.
  - Interrupt reaches the session only while a cell runs, and a failure to stop is not the cell's error.
  - Windows trust that fails keeps Python's own list.
  - The main loop puts the runtime's folder first, skips a line that isn't JSON, starts each cell in a daemon thread with 100 preview rows unless told otherwise, passes on interrupt, stops at quit, reports a start that fails, and runs Databricks Connect when no kind is named.
  - → `test_runtime_worker_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/runtime_worker.py` | 34 | `if TYPE_CHECKING:` negated | Equivalent: it only imports `collections.abc` names used in annotations, which `from __future__ import annotations` never evaluates. |

