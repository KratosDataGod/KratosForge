# Mutation testing, batch 232

A re-score of `spark_hash.py`, which batch 30 left at **76.7%**, on today's `main`. Batch 30 ran before boundary mutants, and the module has gained `sha2` at 224, 384 and 512 bits since, so this run uses `--kinds all` and a new sample.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each test set was run with `-x`. The machine was shared with three other mutation runs, so the per-mutant timeout was raised from 120 to 600 seconds for this batch (by setting `mutate.TIMEOUT` from a wrapper, with the tool unchanged); no mutant's run timed out. The module now has 227 mutants, so 150 were sampled. `test_spark_hashing_values.py::test_spark_hashing_functions_answer_as_spark_3_5_or_are_refused_by_name` reads `docs/`, so it was deselected. That test is the only one that checked `sha2`'s digests, which is why the bit lengths survived.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_hash.py` | test_spark_hash, test_spark_hash_refused, test_spark_hash_mutation, test_spark_hashing_values (one test deselected) (+ test_spark_hash_rescore) | 150 of 227 (148 + 2) | 107 / 43 | **71.3%** | 107/148 → 109/148 | 0/2 → 0/2 | 109 / 41 | **72.7%** | 41 equivalent |

No bugs were found. The score stays low because most of the module is a table of hash steps in which a field the step never reads can change freely.

### What the survivors were, and the tests that now kill them

- **spark_hash.py**:
  - Line 317, the bit lengths 224 and 512 changed to 225 and 513: `sha2` gives the FIPS 180 digest of `abc` at 224, 384 and 512 bits, and NULL at the lengths next to them.
  - → `test_spark_hash_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_hash.py` | 132, 133, 138, 139, 163, 168, 169, 174, 180, 183, 187, 188, 201, 202, 203, 205, 307 (26) | `s` 0 to 1 in a `set`, `add` or `mul` step | Equivalent, as in batch 30: those steps read only `c`, never `s`. |
| `spark_hash.py` | 55 | six registers to seven | Equivalent, as in batch 30: the seventh is never read. |
| `spark_hash.py` | 58 | `_op`'s default `s` 0 to 1 | Equivalent, as in batch 30: `_ops` always passes all four fields. |
| `spark_hash.py` | 77 (2) | three steps to four, and `>=` to `>`, before steps are packed as text | Equivalent, as in batches 30 and 169: both ways give the same steps. |
| `spark_hash.py` | 83 (2) | a missing `s` or `c` padded with 1 | Equivalent, as in batch 30: only steps that don't read them leave them out. |
| `spark_hash.py` | 84 | the `isinstance(full[3], int)` test negated | Equivalent: steps with an expression never come three in a row, so they are still written one by one with `_op`, and each number step becomes a list of one, which `LIST_CONCAT` joins into the same list. |
| `spark_hash.py` | 194 (2) | the 64-bit mask `(1 << 64) - 1` changed | Equivalent, as in batch 30: the step adds modulo 2^64. |
| `spark_hash.py` | 238 (4) | a float's sign wrap at 2^31 or 2^32 changed | Equivalent, as in batch 30: both hashes read a float's bits as 32 bits. |
| `spark_hash.py` | 304 (2) | `count > 1` to `count > 0` and to `>=` | Equivalent, as in batch 169: `LIST_CONCAT` of one list is that list. |

