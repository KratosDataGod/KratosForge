# Mutation testing, batch 175

One module, run uncapped: every mutant, not a sample of 150. Batch 13 sampled 150 of its then 373 mutants (88.7% before, 100% after).
- The Databricks SQL warehouse engine: the REST client's retries and refusals, the hints by API, Catalog paging, workspace writes, statements followed to their end, Cancel, and whole results read chunk by chunk (`engines/databricks_sql.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 383 of 383 mutants ran (373 point + 10 boundary). A first run started earlier was stopped partway and isn't used. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did.

The set is batch 13's own files with test_databricks_sql_cancel added. The files that reach the engine through other services (test_cli_tenant_commands, test_databricks_billing, test_databricks_jobs, test_databricks_runs_service, test_databricks_sync_service, test_databricks_workspace_sync and _edges, test_dbconnect_env, test_engines_service and _edges) and test_engine_conformance (about 10 s) were left out to keep each mutant's run short; in batch 13 they killed only 2 more. test_databricks_sql_parity tests another module. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. Three timed out in the first run, each a loop or a wait that never ends: the `raise` removed at the time limit (line 488) and after a failed poll (line 496), and the lock's non-blocking try made blocking, so a second cell waits for the first forever (line 533). In the "after" run a fourth times out: `e is None or id(e) in seen` to `and` (line 121), which the new test's two errors that hold each other turn into a walk that never ends.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_sql.py` | test_databricks_sql_mutation, test_databricks_sql, test_databricks_sql_errors, test_databricks_sql_robust, test_databricks_sql_cancel, test_credential_expiry, test_databricks_discovery, test_paging, test_remote_text_lines, test_reply_text_one_line (+ test_databricks_sql_uncapped) | 383 of 383 (373 + 10) | 357 / 26 | **93.2%** | 350/373 → 373/373 | 7/10 → 10/10 | 383 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_sql.py**:
  - A Catalog list stops at 1000 items, and one of exactly 1000 asks for no further page.
  - A whole result stops after 10,000 chunks.
  - The hints: a 404 or 403 from an API without hints of its own; a 403 from the metastore summary, a 404 from Unity Catalog, and a 403 or 404 from SCIM; a missing metastore named from its error code.
  - A refusal keeps the workspace's own message, and a reply that isn't an object adds none. A 400 is a refusal.
  - A client on its own (outside a session) tries a throttled read again. A read sends its parameters.
  - A workspace import or delete that may have landed (a 503) isn't sent again; making a folder is. A delete never deletes a folder's contents.
  - A submit answered with 500 may have started; one answered with 499 didn't.
  - The walk through an error's causes ends when two errors hold each other.
  - A session says whether it is closed, and has no variables to list.
  - A statement cancelled from here says "Cancelled."; a failed statement's message is kept to 1000 characters.
  - A statement without a result, and a whole result too large, say how long they took.
  - → `test_databricks_sql_uncapped.py`.

### Left

None: every mutant is killed.

