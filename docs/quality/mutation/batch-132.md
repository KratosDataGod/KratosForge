# Mutation testing, batch 132

One engine module not scored before, run with `--kinds all`:
- Databricks Connect: the runtime lines and their Python, the worker's environment and sign-in, gRPC's root certificates, frame output and what a session refuses (`engines/dbconnect.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 113 mutants, so all of them ran. No `-k` filter or `--deselect` was needed.

The first run used test_dbconnect, test_dbconnect_env and test_databricks_profile_host. Each survivor was run again with test_tls_inspection, test_engine_conformance and test_engines added, and the "before" figures are from that run. No mutant's run timed out. The new tests passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/dbconnect.py` | test_dbconnect, test_dbconnect_env, test_databricks_profile_host, test_tls_inspection, test_engine_conformance, test_engines (+ test_dbconnect_mutation) | 113 of 113 (112 + 1) | 106 / 7 | **93.8%** | 106/112 → 111/112 | 0/1 → 0/1 | 111 / 2 | **98.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/dbconnect.py**:
  - Connecting waits up to 15 minutes for a terminated cluster to start.
  - Runtimes 13 and 14 say they need Python 3.10, and 15 says 3.11.
  - Runtime 19 is known, and 20 is newer than this version knows.
  - → `test_dbconnect_mutation.py`, which also pins, for the first run's survivors that the wider run killed, gRPC's root certificates (written only when a folder is given, nothing is set already and Windows has roots), what a session refuses with the descriptor's own notes, and the adapter's descriptor.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/dbconnect.py` | 112 | `major > max(RUNTIME_PYTHON)` to `>=` | Equivalent: the branch is reached only when the runtime isn't in `RUNTIME_PYTHON`, so `major` is never equal to its largest key. |
| `engines/dbconnect.py` | 223 | `zip(strict=True)` to `False` | Equivalent: the worker builds each row from the same frame as its columns, so they always have the same length. |

