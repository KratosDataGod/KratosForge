# Mutation testing, batch 489

One module not scored before, run with `--kinds all`:
- The guard that keeps SQL text holding a NUL character from reaching DuckDB, which on 1.x reads only up to it: a NUL in a plain string is written as CHR(0), any other NUL is refused, and DuckDB's own entry points are wrapped so no path skips it (`duckdb_nul.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 92 mutants, so all were run. The "after" figure is a run of the survivors again (`--only`) with the new test file added. The module is installed when the package is imported, so a mutant of `install()` runs in every test process. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

The test set was test_sql_nul_guard.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `duckdb_nul.py` | test_sql_nul_guard (+ test_duckdb_nul_mutation_b) | 92 of 92 (85 + 7) | 72 / 20 | **78.3%** | 71/85 → 81/85 | 1/7 → 4/7 | 85 / 7 | **92.4%** | 7 equivalent on DuckDB 1.5.6 |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **duckdb_nul.py**:
  - A NUL first in the text, in an unclosed string, or in a dollar-quoted string is refused.
  - Text that holds every stand-in character (U+E000 to U+F8FF) is refused. Text holding all of them but the first still uses the first.
  - Text the tokenizer can't read is refused with the guard's own message.
  - A NUL in a string that is the first token is kept as CHR(0).
  - SQL text passed by keyword (`execute(query=...)`) is guarded too.
  - Installing again wraps nothing twice and adds no entry point that DuckDB lacks.
  - → `test_duckdb_nul_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `duckdb_nul.py` | 72 | `_literal_end`'s `return -1` to `return None` | Can't be reached on DuckDB 1.5.6: its tokenizer gives no token for an unclosed string, so the scan always finds the closing quote. On DuckDB 2.0.0 it can, and the new unclosed-string test kills it there (checked by hand). |
| `duckdb_nul.py` | 65 | `while i < len(data)` to `<=` in `_literal_end` | As above: reached only by an unclosed string, so not on DuckDB 1.5.6. Killed by the same test on DuckDB 2.0.0. |
| `duckdb_nul.py` | 72 | `return -1` to `return -0` | Equivalent: the result is only used in `start < at < end`, and at is never below 0, so 0 refuses just as -1 does. |
| `duckdb_nul.py` | 94 | `else -1` to `else -0` for the start before the first token | Equivalent: there is then no kind, so end is -1 and the NUL is refused either way. |
| `duckdb_nul.py` | 96 | `else -1` to `else -0` for the end of a token that isn't a plain string | Equivalent, as for line 72. |
| `duckdb_nul.py` | 97 | `start < at` to `start <= at` | Equivalent: a plain string's start is its opening quote, never the NUL's stand-in. |
| `duckdb_nul.py` | 97 | `at < end` to `at <= end` | Equivalent: the end is the closing quote, never the stand-in. |
