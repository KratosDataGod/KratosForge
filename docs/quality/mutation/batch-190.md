# Mutation testing, batch 190

One module, run uncapped: every one of its mutants, not a sample of 150.
- The proof's Spark side: the names and paths it accepts, the versions it reads, the SQL it rewrites, and the readers and writers it patches so that a proof only changes its own folder (`proof_spark.py`).

Batch 20 sampled 150 of this module's 397 mutants then (93.3% before, 99.3% after). This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 399 of 399, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` ad1ac4cf8 (2026-11-18); the module is unchanged on `main` bdf2c1775, where the new tests pass too. It used `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own (`--basetemp`), and none was left too deep to remove, so another run's leftover files could not fail a mutant. A few "killed" mutants were run again by hand to see a test really fail.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. `test_prove.py` also uses `proof_spark.py`, but it takes about 30 s, so it was left out, as in batch 20; the score counts only the files given. A mutant whose run times out counts as killed; none timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_spark.py` | test_prove_spark, test_prove_spark_install, test_proof_spark_mutation (+ test_proof_spark_uncapped) | 399 of 399 (397 + 2), uncapped | 383 / 16 | **96.0%** | 381/397 → 393/397 | 2/2 → 2/2 | 395 / 4 | **99.0%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof_spark.py**:
  - The file runs on its own, outside any package, as it does in the first cell of Fabric's copy: it doesn't import `proof_runtime` when it runs.
  - Opening a pinned table with `DeltaTable.forName` copies its pinned version in first.
  - A writer's `option` and `options`, a write of files outside OneLake and making a temp view hand back Spark's own answer.
  - A table opened and changed since stays a write when the run finishes.
  - A safe command (`SHOW TABLES`, `EXPLAIN`) runs as written.
  - The lakehouse's own name is dropped only before a full table name (`LH_Proof.dbo.orders`, or `LH_Proof.orders` without schemas); with schemas, `LH_Proof.orders` is a schema called `LH_Proof`.
  - `INSERT OVERWRITE` and `INSERT INTO` are kept as their own modes.
  - A path under the proof's own mount is the lakehouse, and without a mount Fabric's `/lakehouse/default` is.
  - A table's folder read as CSV is refused, as one read as Parquet was.
  - → `test_proof_spark_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `proof_spark.py` | 196 | `[tree.this] if tree.this is not None else []` negated, and `is not None` → `is None` | Equivalent: `dropped` is read only for a DROP, and sqlglot always sets `tables` on a DROP, so the fallback after `or` is never read (as batch 20 found). |
| `proof_spark.py` | 287 | `"start" not in first or "end" not in last` → `and` | Equivalent: sqlglot sets both `start` and `end` on every identifier it reads from the text, so a table name's first and last parts either both have them or are not from the text at all. |
| `proof_spark.py` | 339 | `seen is not None and not isinstance(seen, (list, tuple))` → `or` | Equivalent: a list's text starts with `[`, and `None` is the text `None`, so neither is ever read as a table or a Files path; both reach Spark's own `load` unchanged. |

