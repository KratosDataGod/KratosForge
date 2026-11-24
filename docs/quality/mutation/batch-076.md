# Mutation testing, batch 76

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Query parameters on the SQL page: finding `:name` and `{{name}}` outside strings, comments and quoted names, and reading each value as its type (`sql_params.py`).
- Opening and saving `.sql` files on this PC from the SQL page (`sql_files.py`).
- Reading base64 strictly, the same on every Python 3.12 patch release (`b64.py`).

The run was at `main` e67fa8480 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. Two `sql_params.py` mutants loop for ever and are stopped at the time limit; as in earlier batches, they count as killed, before and after.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_shell_commands.py` also reaches `sql_files.py` but reads `docs/` and `app/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_params.py` | test_sql_params (+ test_sql_params_mutation) | 122 of 122 (113 + 9) | 83 / 39 | **68.0%** | 82/113 → 111/113 | 1/9 → 6/9 | 117 / 5 | **95.9%** | 5 equivalent |
| `sql_files.py` | test_sql_files (+ test_sql_files_mutation) | 75 of 75 (73 + 2) | 55 / 20 | **73.3%** | 55/73 → 72/73 | 0/2 → 2/2 | 74 / 1 | **98.7%** | 1 equivalent |
| `b64.py` | test_b64 (+ test_b64_mutation) | 17 of 17 (16 + 1) | 14 / 3 | **82.4%** | 13/16 → 15/16 | 1/1 → 1/1 | 16 / 1 | **94.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sql_params.py**:
  - At most 50 parameters of at most 4,000 characters each: 50 and 4,000 are taken, 51 and 4,001 refused.
  - Each malformed parameter list is refused by name: not a list, an entry that isn't `{name, type, value}`, a name that isn't text or isn't letters, digits and `_`, a value that isn't text.
  - Finding names and writing `{{name}}` as `:name` read Spark SQL by default, where `[...]` isn't a quoted name; a positional query reads T-SQL by default, where it is.
  - Strings and comments end exactly where they end: a string at the very start, `/*/` opening a comment, the shortest comment, a parameter just before or just after a comment ends, a string that never ends, `''` and `\'` inside a string, a doubled quote right before the closing one, a string closing on the last character, and a parameter right after a string.
  - A backslash is an ordinary character in a T-SQL string.
  - → `test_sql_params_mutation.py`.
- **sql_files.py**:
  - The limit is 5 MB: a file or a save of exactly 5 MB is taken, one byte more is refused, and the refusal names 5 MB.
  - A folder named `.sql` isn't a file.
  - A save with text that isn't text, another encoding or another line ending is refused and writes nothing.
  - A Windows reparse point (a junction, a mount point) is refused as a link; a stat result without file attributes, or a Python without the reparse-point constant, reads nothing as one.
  - → `test_sql_files_mutation.py`.
- **b64.py**:
  - A character outside the alphabet is refused even where skipping it would decode.
  - Padding followed by data is refused without the standard library's help, and no padding at all is fine.
  - → `test_b64_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_params.py` | 34 | `j < 0` to `j < 1`, and to `j <= 0` | Equivalent: `j` is where the line break after a `--` is found, searching from the `--` itself, so it is at least 2 when found and -1 when not; it is never 0. |
| `sql_params.py` | 37 | `j < 0` to `j < 1`, and to `j <= 0` | Equivalent: the same for the `*/` after a `/*`, searched from 2 past it. |
| `sql_params.py` | 65 | `a <= at` to `a < at` | Equivalent: a span starts at a quote, `-`, `/` or `[`, and a parameter starts at `:` or `{`, so a parameter never starts where a span does. |
| `sql_files.py` | 50 | `return False` dropped (returns `None`) | Equivalent: the result is only used as a condition, where `None` and `False` act the same. |
| `b64.py` | 20 | `pad < len(text) - 2` to `- 3` | Equivalent: the two differ only when `=` is the third character from the end, and then what follows it is three characters, never `=` or `==`, so the next test refuses it either way. |

