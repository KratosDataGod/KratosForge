# Mutation testing, batch 89

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Procedures, functions and `sp_rename` in local T-SQL: the token scan that refuses a routine by name before its body is read, a call written without `EXEC`, and the rename itself (`localdata/tsql_routines.py`).
- Local T-SQL's table-valued forms: `GENERATE_SERIES`, `TOP (n) PERCENT` and `(VALUES ...) AS v (a, b)` (`localdata/tsql_tables.py`).
- Spark's regular expressions and string results on this PC: Java's pattern and replacement syntax as RE2 reads it, and the string functions whose results differ on DuckDB (`spark_strings.py`).

The run was at `main` 8838a247c (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839 and #2902, so each mutant ran without a bytecode cache and each test set was first run once on the unmutated copy, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `localdata/tsql_tables.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. Three tests of test_tsql_loud_gaps that read `docs/local-data.md` (the refused-list checks) and test_spark_strings's `test_what_still_differs_is_listed_in_the_compatibility_docs`, which reads `docs/sql-compatibility.md`, were left out with `--deselect`; no other test in these sets reads `docs/`, `examples/` or `app/`. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. One `localdata/tsql_routines.py` mutant times out before and after (line 239, the name scan's `end += 1` to `end += 0`, which never ends), and two `spark_strings.py` mutants timed out in the "before" run and one in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_routines.py` | test_tsql_routines, test_local_tsql, test_tsql_loud_gaps (+ test_tsql_routines_mutation) | 150 of 276 (144 + 6) | 124 / 26 | **82.7%** | 121/144 → 143/144 | 3/6 → 6/6 | 149 / 1 | **99.3%** | 1 equivalent |
| `localdata/tsql_tables.py` | test_tsql_table_forms, test_tsql_windows, test_local_tsql_more_edges, test_tsql_loud_gaps (+ test_tsql_tables_mutation) | 148 of 148 (141 + 7) | 122 / 26 | **82.4%** | 117/141 → 136/141 | 5/7 → 7/7 | 143 / 5 | **96.6%** | 5 equivalent |
| `spark_strings.py` | test_spark_strings, test_spark_missing_functions, test_spark_function_names, test_spark_sql_names, test_spark_views_text (+ test_spark_strings_mutation) | 150 of 458 (139 + 11) | 108 / 42 | **72.0%** | 103/139 → 131/139 | 5/11 → 9/11 | 140 / 10 | **93.3%** | 10 equivalent |

One bug was found, in `localdata/tsql_tables.py`, and is fixed; its test in test_tsql_tables_mutation now passes:
- `TOP ('150') PERCENT` and `TOP ((ABS(-150))) PERCENT` returned every row. A percent written as text, or in a second pair of parentheses, was checked against 0 to 100 neither when the query was read nor when it ran, where a warehouse stops with "Percent values must be between 0 and 100." Any percent that isn't a number written out is now checked, as a number, when it runs.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_routines.py**:
  - The token scan names a routine as written up to its parenthesis or semicolon (`CREATE FUNCTION dbo.f(@x INT)` is `dbo.f`), and takes `IF` with no `EXISTS` after it as the name.
  - The scan refuses `EXEC (...)` of a string first in a batch or later, and leaves `exec` after a dot (a function's name), and `EXEC` or `DROP` as a batch's last word, for the parser.
  - A system procedure written alone, or a name with a string after it, gets `EXEC` written in; another name alone doesn't.
  - Behind the scan, `statement()` refuses `ALTER PROCEDURE` (a command to sqlglot), with or without a name, and `DROP PROCEDURE` and `DROP FUNCTION IF EXISTS` by their names, and `EXEC` of a string. It leaves another command and `DROP TABLE` alone.
  - A rename `statement()` has made runs through it again unchanged, not taken for `ALTER TABLE ... RENAME`.
  - A new table name with its schema is refused with the name alone to write instead.
  - → `test_tsql_routines_mutation.py`.
- **localdata/tsql_tables.py**:
  - A whole-number start in parentheses is still whole against a decimal stop, so the two are refused as different types. A column start has no literal kind, so a decimal stop runs.
  - A whole-number series is `int` from -2,147,483,648 to 2,147,483,647, and `bigint` one past either end.
  - A series without a stop, and `TOP ... PERCENT` on anything but a `SELECT`, are refused by name. sqlglot's parser stops on both first, so they are tested on trees made by hand.
  - `TOP 100 PERCENT` keeps every row of a table of 150 rows.
  - A percent worked out per query runs from 0 to 100 (0.5, 25 and 100) and stops outside it (-0.5, 100.5 and 150).
  - → `test_tsql_tables_mutation.py`.
- **spark_strings.py**:
  - `\Q...\E` quotes up to `\E` and the pattern goes on after it. A character past ASCII is kept as it is, and the last ASCII character is escaped.
  - A one-letter property (`\pL`, `\PL`) takes just that letter.
  - `\h` and `\v`, and `\H` and `\V`, are a class outside `[ ]` and its characters inside. `\H` and `\V` inside `[ ]` are refused.
  - A `]` first in a class is a character, so the class runs to the next `]`, and a `(` in a class is no group.
  - A flag group that turns a flag off keeps its `-`.
  - `\p{` without its brace, `\0` without an octal number, `\G`, `\c` at the end and `(?<` without a name are refused by name. A refusal with a hint ends "Or run it in Fabric.", and one without says "Run it in Fabric."
  - An escaped character in a replacement is that character, and an escaped backslash stays one. A replacement group past 9 is refused.
  - The DataFrame wrapper rewrites a replacement unless the function reads Java's itself, and passes a call without a pattern straight on. It takes groups 0 to 9 the pattern has, and refuses one past the pattern's groups or past 9, saying which groups to use.
  - `LIKE` reads a backslash as its escape, also in a pattern from an expression, unless another escape is given.
  - → `test_spark_strings_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql_routines.py` | 47 | `tree.this.this if isinstance(tree.this, exp.Schema) else tree.this` negated for a `CREATE` of a routine | Equivalent in practice: the token scan refuses every `CREATE` of a procedure, function or trigger before the statement is parsed, so this branch of `statement()` is never reached through the engine, and sqlglot gives a routine's name as a procedure or function node, never a `Schema`; the mutant only drops the parameters from a name no one sees. |
| `localdata/tsql_tables.py` | 50 | `return ""` to `return None` for a start or stop that isn't a number literal | Equivalent: the kind is only compared with `"float"`, `{"whole", "decimal"}` and `{"whole"}`, which `""` and `None` both fail. |
| `localdata/tsql_tables.py` | 97 | one more step than needed, `+ 2` to `+ 3` | Equivalent: each value is compared with stop, so the extra step is filtered out. |
| `localdata/tsql_tables.py` | 108 | `GREATEST(count, 0)` to `GREATEST(count, 1)` | Equivalent: a count below 1 comes from a step pointing away from stop, and the one value then made is outside stop, so it is filtered out. |
| `localdata/tsql_tables.py` | 186 | `values.expressions and isinstance(...)` to `or`, for a VALUES list's width | Equivalent: sqlglot reads every VALUES row as a tuple, also `(VALUES 1)`, so both give the tuple's width. |
| `localdata/tsql_tables.py` | 187 | a row that isn't a tuple is 1 wide, to 0 | Equivalent: as above, no row reaches it. |
| `spark_strings.py` | 80 | `end < 0` to `end < 1`, and `end < 0` to `end <= 0`, for `\Q` without `\E` | Equivalent: the search for `\E` starts after `\Q`, so it finds nothing (-1) or a position of 2 or more, never 0. |
| `spark_strings.py` | 196 | `c == "?"` to `c != "?"` for a lazy quantifier | Equivalent in effect: it differs only for a quantifier after a lazy one (`a*?+`), which RE2 refuses too ("bad repetition operator"), so both stop. |
| `spark_strings.py` | 199 | `quantified = False` after a lazy quantifier to `True` | Equivalent in effect: as above. |
| `spark_strings.py` | 242 | `end > 0` to `end >= 0` for `${` | Equivalent: `}` is searched for from the `$`, so it is found at a later position or not at all (-1), never at 0. |
| `spark_strings.py` | 256 | `i + 1` to `i + 0` while reading a replacement group's digits | Equivalent: a group of two digits is past 9 and refused, and the loop still stops on the same group number, so the refusal is the same. |
| `spark_strings.py` | 306 | `_around`'s `return new` to `return None` | Equivalent: only `locate` reads it, to choose what to cast to `INT`; without it the inner call is cast instead, and the result is the same `int`. |
| `spark_strings.py` | 344 | `replacement is not None and not NULL` to `or`, for a replacement from the data | Equivalent: a NULL replacement then gets the check for `$` and `\`, which is NULL, so the result is NULL either way, and Spark has no `regexp_replace` without a replacement. |
| `spark_strings.py` | 416 | `len(parts) == 1` to `== 0` in `concat_ws` with one array | Equivalent: DuckDB's `LIST_CONCAT` of one list is that list. |
| `spark_strings.py` | 457 | the NULL arguments of `||` cast to text, negated | Equivalent: the whole expression is then cast to text anyway, so a bare NULL and `CAST(NULL AS INT)` both give a NULL string. |

