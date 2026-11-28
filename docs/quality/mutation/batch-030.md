# Mutation testing, batch 30

Three modules, none touched by an open pull request:
- Spark's `hash()` and `xxhash64()` on this PC: the result types (INTEGER and BIGINT, as in Spark), the same call always building the same SQL, builds at the same time each keeping their own lambda names, and the seed for a NULL (`spark_hash.py`).
- The known differences between a pipeline that passes here and one Fabric accepts: a notebook timeout one second over 12 hours, the parameter a global-parameter warning names, where a Key Vault secret is read, a Key Vault call by an expression URL, gotchas being fixed once made, the containers walk ending at the top, at a missing parent or after 100 steps, and a Set Variable in a sequential ForEach inside a parallel one (`orchestration/gotchas.py`).
- The App Studio specification: every text and count limit at and one past its value, required texts, control characters, the twelve-column grid's edges, `Table[Column]` fields, each target's own components and interactions, references by kind, an early problem not stopping the later checks, writeback for an unknown target, and `find`, `page_of`, `dependencies`, `dependents` and `copy_of` (`appstudio/spec.py`).

Batch 29's modules (`appstudio/patch.py`, `meta/diff.py` and `meta/drift.py`) were left to batch 29, which ran at the same time.

The run was at `main` 21897a615 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 29 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_spark_coverage.py` reads `docs/`, so it was left out of the `spark_hash.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_hash.py` | test_spark_hash, test_spark_hash_refused (+ test_spark_hash_mutation) | 150 of 216 | 114 / 36 | **76.0%** | 115 / 35 | **76.7%** | 35 equivalent |
| `orchestration/gotchas.py` | test_pipeline_gotchas, test_notebook_formats (+ test_pipeline_gotchas_mutation) | 125 of 125 | 115 / 10 | **92.0%** | 125 / 0 | **100%** | none |
| `appstudio/spec.py` | test_appstudio, test_appstudio_spec_edges, test_appstudio_generate(_edges), test_appstudio_edit(_edges), test_appstudio_pbir(_edges) (+ test_appstudio_spec_mutation) | 150 of 168 | 122 / 28 | **81.3%** | 150 / 0 | **100%** | none |

`spark_hash.py` was already checked value for value against Apache Spark 3.5.3. Its survivors are almost all fields a step never reads (below), so its score stays low while nothing is left untested. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_hash.py**:
  - `hash()` being cast to INTEGER, not BIGINT, so a mutant that changed 32 to 33 in the width check gave the right numbers as the wrong type.
  - → `test_spark_hash_mutation.py`.
- **orchestration/gotchas.py**:
  - The 12-hour notebook limit at 43,200 seconds, so 12:00:01 is warned about and 12:00:00 isn't.
  - The global-parameter warning naming the parameter, not the whole match.
  - A Key Vault warning's path pointing at the secret reference or at the expression that reads it.
  - `Gotcha` being frozen.
  - The containers walk stopping after 100 steps on a definition that contains itself, and at the top or a missing parent.
  - → `test_pipeline_gotchas_mutation.py`.
- **appstudio/spec.py**:
  - Each limit at and one past its value: name 120, purpose 2,000, audience entries 1 to 120, source texts 256, measure name 128 and expression 8,000, titles 120 and 200, font 80, a colour key cut at 40, 50 pages and 400 components.
  - Required texts that are missing, not just empty.
  - The grid: `x + w` at 12 allowed and at 13 refused, and `w` or `h` of 0 refused.
  - A problem found before the ids are checked not stopping the rest, while a collection that isn't a list of objects does.
  - Components that aren't objects being refused by page.
  - An unknown target having no writeback.
  - → `test_appstudio_spec_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_hash.py` | 131–210, 305 (24) | `s` 0 → 1 in a `set`, `add` or `mul` step | Equivalent: those steps read only `c`, never `s`. |
| `spark_hash.py` | 54 | six registers → seven | Equivalent: the seventh is never read. |
| `spark_hash.py` | 57 (2) | `_op`'s default `s` or `c` 0 → 1 | Equivalent: `_ops` always passes all four fields. |
| `spark_hash.py` | 76 | three steps → four before steps are packed as text | Equivalent: both ways give the same steps. |
| `spark_hash.py` | 82 | a missing `c` padded with 1 | Equivalent: only steps that don't read `c` leave it out. |
| `spark_hash.py` | 89 | one part → none before `LIST_CONCAT` | Equivalent: `LIST_CONCAT` of one list is that list. |
| `spark_hash.py` | 193 | the 64-bit mask `(1 << 64) - 1` → `(0 << 64) - 1` | Equivalent: the step adds modulo 2^64, so `-P1` and its 64-bit form give the same lane. |
| `spark_hash.py` | 237 (4) | a float's sign wrap at 2^31 or 2^32 changed | Equivalent: both hashes read a float's bits as 32 bits (masked), so the wrap doesn't change them. `-0.1` is in the Spark fixture. |

### Not in this batch

Batch 29's modules.

