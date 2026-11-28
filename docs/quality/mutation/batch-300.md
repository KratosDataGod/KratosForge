# Mutation testing, batch 300

One engine module not scored before: `engines/fabric_spark.py`. It was planned for batch 20 but left out then, because a pull request was changing it. It ran with `--kinds all`, so the score includes the boundary mutants (`<` to `<=`, `>` to `>=`) along with the earlier kinds, and the table shows both parts.
- Fabric Spark sessions over the Livy API: the start and cell time limits and how they are worded, Livy's message length, the request timeout, which calls are sent again after a 429 or a 5xx even once Cancel is pressed, the hint for each refusal, error tracebacks, the whole-result row limit and its preview, and the time a cell reports.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py`, its baseline check and no bytecode cache, as in earlier batches (sample cap 150, seed 7, `--kinds all`). The module has 263 mutants, and 150 were sampled. Test files were given to the tool as paths relative to `sidecar/`.

Before the "after" run, its tests passed both in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%. No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected.

Four mutants time out both before and after, and they count as killed. Three remove a `raise`, so a refusal or a failed poll doesn't stop a wait loop (lines 180, 357 and 416). The fourth makes the lock wait for a cell already running (line 389).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/fabric_spark.py` | test_fabric_spark, test_fabric_spark_replies, test_engine_conformance, test_engines_service_edges, test_engine_compare (+ test_fabric_spark_mut300) | 150 of 263 (145 + 5) | 127 / 23 | **84.7%** | 126/145 → 143/145 | 1/5 → 5/5 | 148 / 2 | **98.7%** | 2 equivalent |

No defects were found.

### What the survivors were, and the tests that now kill them

- **fabric_spark.py**:
  - A session may take 15 minutes to start and a cell may run for 2 hours. Each limit is given in whole minutes in its message.
  - A cell is stopped only once its time limit has passed, not when it is reached.
  - Livy's message is kept to 300 characters. Exactly 300 are kept whole, and 301 are cut to 299 plus an ellipsis.
  - Each call waits 60 seconds for Fabric.
  - A delete is sent again after a 429 even once Cancel is pressed, but a read stops trying.
  - A cancel is sent again after a 503.
  - A 429 says the capacity is busy and how many times it was tried. A 430 says the Spark limit is reached. A refusal sent once doesn't count its tries.
  - An error keeps its traceback lines.
  - A whole result of exactly the rows asked for is read, and its preview starts at the first row.
  - A session that is already idle when it is created is not polled.
  - A cell reports how long it took.
  - These tests are in `test_fabric_spark_mut300.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/fabric_spark.py` | 41 | `if TYPE_CHECKING:` negated | Equivalent: with `from __future__ import annotations`, the `pyarrow` import there is used only in annotations, so importing it at run time changes nothing. |
| `engines/fabric_spark.py` | 349 | `self._closed = True` to `False` when Fabric gives no session id | Equivalent: the constructor raises straight after, so no caller ever holds that session to read or close it. |
