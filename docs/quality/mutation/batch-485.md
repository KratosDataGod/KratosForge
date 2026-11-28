# Mutation testing, batch 485

One module not scored before, run with `--kinds all`:
- The warehouse and SQL analytics endpoint controls (FEA-303), and the evidence they read from item properties: whether a lakehouse's SQL endpoint is provisioned (FEA-WH-001), and whether a workspace's warehouses share one collation (FEA-WH-002) (`excellence/catalog_warehouse.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 100 mutants, so all were run. The "after" figure is a run of the survivors again (`--only`) with the new test file added. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

The test set was test_excellence_warehouse, test_excellence_catalog.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_warehouse.py` | test_excellence_warehouse, test_excellence_catalog (+ test_excellence_catalog_warehouse_mutation_b) | 100 of 100 (98 + 2) | 69 / 31 | **69.0%** | 68/98 → 96/98 | 1/2 → 2/2 | 98 / 2 | **98.0%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_warehouse.py**:
  - FEA-WH-002's text, word for word: the only warehouse; the same collation as 1 other warehouse, or as 2 other warehouses; 1 other warehouse that uses another collation, or 2 that use one; 1 other warehouse that wasn't read (it), or 2 that weren't (they).
  - At most five different warehouses are named. Exactly five adds nothing, six adds " and 1 more", and eight adds " and 3 more".
  - The `workspace.warehouses` record lists at most 50 warehouses, and still counts all 51.
  - Only a lakehouse names an endpoint, and only an SQLEndpoint item is one: a lakehouse that names a warehouse's id gives no record, and neither does a notebook read as if it were a lakehouse.
  - An endpoint status that is empty or not text is kept as None.
  - → `test_excellence_catalog_warehouse_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/catalog_warehouse.py` | 173 | `assert own is not None and ws is not None` to `or` | Equivalent: the control runs only when both declared kinds of evidence are there, so both are always set. |
| `excellence/catalog_warehouse.py` | 187 | `int(p.get("count") or 0)` to `or 1` | Equivalent: a `workspace.warehouses` record is written only when at least one warehouse was read, so its count is never 0 or missing. |
