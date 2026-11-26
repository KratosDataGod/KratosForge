# Mutation testing, batch 256: dataops_service.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 197 sampled 150 of its then 320 mutants (74.7% before, 99.3% after).
- The service side of Data health and day-to-day data engineering (`dataops_service.py`): the tables each PC watches and the alerts acknowledged or snoozed there, one table's health from its Delta log, compare before a promote, meaningful diffs, schema drift, quarantine replay, alert settings and Teams cards.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 320 of 320 mutants ran (313 point + 7 boundary). The mutants were split into shards with `--only`, run side by side, and merged. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The first 48 mutants scored ran with `--basetemp` in a folder of this run's own, which was checked afterwards: nothing in it was nested 40 or more folders deep. The rest, and the "after" run, gave each pytest session a new temporary folder of its own (a small plugin, not committed), removed when the session ended. In each shard the last survivors still took about a minute to run the whole set, so no streak of false kills came from a broken folder, and two late kills (indexes 316 and 318) were run again by hand: each fails a real assertion.

The set is batch 197's files with test_dataops_service_cov3, the quick files first so most mutants are killed in seconds. The tests that reach the hung-check path lower the list's time limit (`DATA_HEALTH_LIST_SECONDS`, 120 s), so no mutant waited on it. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dataops_service.py` | test_dataops_service_mutation, test_dataops_service_cov3, test_data_health_mutation, test_dataops_edges, test_dataops, test_data_health (+ test_dataops_service_uncapped) | 320 of 320 (313 + 7) | 304 / 16 | **95.0%** | 299/313 → 310/313 | 5/7 → 7/7 | 317 / 3 | **99.1%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **dataops_service.py**:
  - Default page sizes: a table's versions are its last 15, quarantine shows its first 500 rows, drift shows that entity's last 10 schema changes (and only that entity's), and freshness reads up to 100000 runs.
  - The deployed entities are those of the last promote to that environment, not the first.
  - A quarantined row held before ids existed gets `old-` and 16 hex digits of its values.
  - A table whose file statistics add up to 0 rows has 0 rows, not 1.
  - A committed entity file is named by the name inside it, not by its file name.
  - Email alerts are refused until the company mail server is set.
  - A reset note is said until exactly a week has passed, and a snooze that ends now is over.
  - → `test_dataops_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `dataops_service.py` | 49 | `get_add_actions(flatten=True)` to `False` | Equivalent (as in batch 197): `num_records` is a top-level column either way; flattening only splits the per-column statistics, which aren't read. |
| `dataops_service.py` | 265 | `hashlib.sha1(..., usedforsecurity=False)` to `True` | Equivalent: the flag only matters on a Python built to refuse SHA-1 for security (FIPS); the digest is the same. |
| `dataops_service.py` | 471 | `zip(watched, futures, strict=True)` to `False` | Equivalent: the two lists are built in the same loop, one future per watched table, so they are always the same length. |

