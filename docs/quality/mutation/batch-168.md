# Mutation testing, batch 168

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- The metadata project's store: commits since a version, and compacting the meta tables (`meta/store.py`, batch 14).
- The notebook wizard's templates (`wizard.py`, batch 18).
- Surrogate keys (`meta/keys.py`, batch 20).
- Spark proofs (`proof_spark.py`, batch 20).
- The services external tools call (`integrations/services.py`, batch 21).
- Costs (`costs.py`, batch 33).

The run was at `main` 4ddd3662 (2026-11-18) with batches 163 to 167's tests added, and the same method as batch 163. For `meta/store.py` only its own test files and `test_meta_compaction.py` were run: with every file that uses it, the unmutated run took over the 120 s limit, so no mutant could be scored.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/store.py` | test_meta_store_edges, test_meta_store_mutation, test_store_crash_kills, test_meta_compaction (+ test_meta_store_boundary) | 2 of 2 | 0 / 2 | **0.0%** | 1 / 1 | **50.0%** | 1 equivalent |
| `wizard.py` | test_append_callers, test_wizard, test_wizard_mutation | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |
| `meta/keys.py` | the 10 test files that use it (+ test_meta_keys_boundary) | 2 of 2 | 1 / 1 | **50.0%** | 2 / 0 | **100%** | none |
| `proof_spark.py` | test_proof_spark_mutation, test_prove_spark, test_prove_spark_install | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |
| `integrations/services.py` | the 6 test files that use it (+ test_integrations_services_boundary) | 2 of 2 | 1 / 1 | **50.0%** | 2 / 0 | **100%** | none |
| `costs.py` | test_costs_mutation, test_estate, test_measurement (+ test_costs_boundary) | 3 of 3 | 0 / 3 | **0.0%** | 3 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/store.py**: version 0 of an existing meta table doesn't compact. → `test_meta_store_boundary.py`.
- **meta/keys.py**: exactly `KEY_LOOKUP_LIMIT` business keys are looked up, not read with the whole table. → `test_meta_keys_boundary.py`.
- **integrations/services.py**: a quality check with no failed rows isn't counted as failed. → `test_integrations_services_boundary.py`.
- **costs.py**: a run of zero seconds has a known time, as a metadata run and as a notebook write; a run exactly the report's period ago is in the report. → `test_costs_boundary.py`.

`wizard.py` and `proof_spark.py` had no survivors.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/store.py` | 640 | `t.version() <= since` to `<` in `has_commit` | Equivalent: at `since` equal to the version, the mutant asks for `history(0)`, which is empty, so the answer is False either way. |

