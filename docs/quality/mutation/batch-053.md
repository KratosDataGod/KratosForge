# Mutation testing, batch 53

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Notebook maintenance statements: how a notebook's `OPTIMIZE`, `VACUUM` and `RESTORE` are read on this PC, and Spark's answer for each (`spark_maint.py`).
- Tenant settings history: hashing a setting's value, the newest snapshot per setting, the change rows a read adds, and the read row (`tenant_history.py`).
- App Studio PBIR: compiling a Power BI report specification to the PBIR files (`appstudio/pbir.py`).

The run was at `main` 7893f86a8 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_maint.py` | test_spark_maint, test_spark_merge, test_spark_refusals, test_time_travel (+ test_spark_maint_mutation) | 52 of 52 (52 + 0) | 36 / 16 | **69.2%** | 36/52 → 51/52 | none | 51 / 1 | **98.1%** | 1 equivalent |
| `tenant_history.py` | test_tenant_history, test_capacity_history (+ test_tenant_history_mutation) | 34 of 34 (33 + 1) | 28 / 6 | **82.4%** | 28/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `appstudio/pbir.py` | test_appstudio_pbir, test_appstudio_pbir_edges, test_appstudio_publish, test_appstudio_publish_edges (+ test_appstudio_pbir_mutation) | 63 of 63 (61 + 2) | 51 / 12 | **81.0%** | 51/61 → 60/61 | 0/2 → 2/2 | 62 / 1 | **98.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_maint.py**:
  - `RESTORE ... TIMESTAMP AS OF` gives the timestamp, in UTC when it names no offset, with either quote.
  - A timestamp with its own offset keeps it, and one RESTORE can't read is refused, alone and inside a statement.
  - `OPTIMIZE ... WHERE` takes only partition column = value joined with AND: a comparison, a column on the right, an OR and a bare column are each refused.
  - Spark's answers count a missing metric as 0 and carry the ones Delta reported; a dry-run `VACUUM` lists the files, a real one answers with the table.
  - → `test_spark_maint_mutation.py`.
- **tenant_history.py**:
  - The canonical text sorts keys and keeps non-ASCII text as written.
  - Security groups and properties are put in a fixed order, including an empty group entry.
  - Of two snapshots taken at the same time, the first is kept.
  - A read with no tenant is recorded with "-", and its reason is cut to 500 characters.
  - → `test_tenant_history_mutation.py`.
- **appstudio/pbir.py**:
  - A report may have up to 1000 pages and 1000 visuals per page, and exactly the limit compiles.
  - Parts are two-space JSON with text kept as written.
  - Visuals with data cross-filter the others.
  - Positions are grid columns rounded to two places.
  - A visual name with other characters is refused.
  - → `test_appstudio_pbir_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `spark_maint.py` | 67 | `.replace(" ", "T", 1)` to `.replace(" ", "T", 0)` | Equivalent on Python 3.12 and later (the floor): `datetime.fromisoformat` takes a space between date and time as well as `T`, so replacing the first space or none reads the same value. |
| `appstudio/pbir.py` | 113 | `max(..., default=0)` to `default=1` | Equivalent: it only matters for a page with no visuals, where the height is `max(720, 0 * 60)` or `max(720, 1 * 60)`, 720 either way. |

