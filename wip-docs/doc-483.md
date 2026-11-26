# Mutation testing, batch 483

One module not scored before, run with `--kinds all`:
- The Snowflake export profile (OSSIE-010a): an Ossie 0.2.0.dev0 document written as one Snowflake Ossie 0.1.1 file, with a report. Extension data that isn't text is written as JSON text, a field or metric with no SNOWFLAKE or ANSI_SQL expression is named because Snowflake skips it, and an OSSIE_SQL_2026 expression is translated to Snowflake SQL by sqlglot when it can be (`ossie_snowflake.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 117 mutants, so all were run.

The test set was test_ossie_snowflake and test_semantic_matrix (which makes its Snowflake row with this module).

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Three reasons can't be reached with real input on this PC (sqlglot reading an expression as nothing, writing it as nothing, or hitting Python's recursion limit while writing it), nor can the reader's TypeError; the new tests stand in for sqlglot or the reader there. Nothing calls Snowflake.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ossie_snowflake.py` | test_ossie_snowflake, test_semantic_matrix (+ test_ossie_snowflake_mutation_b) | 117 of 117 (115 + 2) | 96 / 21 | **82.1%** | 95/115 → 114/115 | 1/2 → 2/2 | 116 / 1 | **99.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ossie_snowflake.py**:
  - A text exactly at its limit isn't cut.
  - Three unknown functions name two and count the third ("and 1 more"). Long function names are cut to 24 characters and the reason to 80.
  - No text, an expression sqlglot reads as nothing, and one it writes as nothing are "is empty". One too deep to write says so.
  - Bytes that aren't UTF-8 are refused, and so is a reader's TypeError, as a plain ValueError with its text.
  - An owner is named by the objects in its path only, not by the keys between them.
  - The whole-file row comes first. A translated row points at the SNOWFLAKE entry it added. A skipped field names its first three dialects.
  - The YAML is block style and keeps non-ASCII text, and extension data written as JSON keeps it too.
  - → `test_ossie_snowflake_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `ossie_snowflake.py` | 237 | `lru_cache(maxsize=1)` to `maxsize=0` on the dumper | Equivalent: the dumper class is then built again on each call, and it writes the same text. |
