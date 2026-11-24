# Mutation testing, batch 31

Three modules, none touched by an open pull request:
- Warehouse compare and deploy: where a built-in type's name (`Date`, `Count`) folds and where it stays a column's name, quoted names with doubled quotes, the side-by-side view's 3,000-row cut and changed blocks of uneven length, changed rows listed first, the order a deploy runs its steps in, the one-definition check for views, functions, tables and schemas, and the preview's precondition (`warehouse_compare.py`).
- Reading pipeline expressions: whole values and interpolations at the start of a string, a real `@{` right after an escaped `@@{`, quotes inside an interpolation, a value typed as something other than `Expression`, member chains with `?.`, `[..]` and a trailing dot, calls with nothing after them, and `pipeline().parameters` with no name (`orchestration/expressions.py`).
- T-SQL's `FORMAT` on the local engine: backslashes inside and outside quoted text, a `%` that isn't before a letter, and the digits and places a number format may have (`localdata/tsql_format.py`).

Batch 30's modules (`spark_hash.py`, `orchestration/gotchas.py` and `appstudio/spec.py`) were left to batch 30, which ran at the same time.

The run was at `main` 46d790e27 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 30 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Three tests in `test_tsql_loud_gaps.py` read `docs/` or the module's source, so they were left out of the `tsql_format.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_compare.py` | test_warehouse_compare_more_edges, test_warehouse_precondition, test_warehouse_project (+ test_warehouse_compare_mutation) | 150 of 333 | 121 / 29 | **80.7%** | 147 / 3 | **98.0%** | 3 equivalent |
| `orchestration/expressions.py` | test_orchestration_expressions_edges, test_pipeline_definitions, test_pipeline_expression_numbers, test_pipeline_gotchas, test_orchestration_definition_edges, test_orchestration_definition_mutation (+ test_orchestration_expressions_mutation) | 150 of 251 | 116 / 34 | **77.3%** | 143 / 7 | **95.3%** | 7 equivalent |
| `localdata/tsql_format.py` | test_tsql_loud_gaps, test_local_tsql, test_local_tsql_more_edges, test_sql_editor (+ test_tsql_format_mutation) | 150 of 206 | 127 / 23 | **84.7%** | 141 / 9 | **94.0%** | 9 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse_compare.py**:
  - A built-in type's name after `AS` folding only before `)`, and after `(` only before `,`, so an alias or a column called `Date` keeps its case. A soft word second in the script, or last.
  - `"a""b"` and `[a]]b]` read as one name each.
  - The side-by-side view cut at 3,000 rows, and a changed block of two lines against one (or one against two) leaving a side empty.
  - Changed rows before rows only in the project, whatever their names.
  - Each deploy step's place in the order (schemas, tables, functions, views, procedures, then drops in reverse), with names chosen so that sorting by name would give another order.
  - The one-definition check: a view is cut at its first `;` even when its text has the word `begin`, a function without a body likewise, a table or schema file has to start with its own `CREATE`, a schema may have only `AUTHORIZATION` and its owner after its name, and a table needs a column list.
  - The preview's precondition having columns only for a table that exists.
  - Why a step is offered or not: "new", "new table, empty", "definition changes", and an alter that isn't one definition.
  - Three more, recorded with the security notes.
  - → `test_warehouse_compare_mutation.py`.
- **orchestration/expressions.py**:
  - `@concat(...)` not interpolated, and `@{...}` at the very start of a string read.
  - `x@@{@{b}}` reading `b`; `@{'}'}` keeping its quoted brace; a quote closing before the next interpolation; a quote at the very end of an open one.
  - Only a value typed `Expression` read as one, an `Expression` value starting with `@{` read as its parts, and a whole one not interpolated.
  - A doubled quote in a string read as one quote.
  - Member chains: `?.output?['rows']`, `['rows'][0].x`, `['x'].y`, and chains that stop at `.`, `?` or `[`.
  - `activity('A')` and `item()` with nothing after them, `utcnow().x` giving no reference, and `pipeline().parameters` with no name read as a system reference.
  - → `test_orchestration_expressions_mutation.py`.
- **localdata/tsql_format.py**:
  - `'a\'b'` in a date format giving `a'b`, and quoted text that ends in a backslash refused as not closed.
  - `\d` outside quotes keeping one character, and a backslash at the end refused.
  - `d%` and `%%d` refused.
  - `N123`, `Nx`, `N2x` and `F100` refused, `N39` and `F99` refused for more than 38 places, and `F37` run.
  - → `test_tsql_format_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `warehouse_compare.py` | 66 | `t[:2]` → `t[:3]` in the `N'...'` check | Equivalent: a plain `'` string is caught first, and an `N'...'` token is never a keyword, so it doesn't fold either way. |
| `warehouse_compare.py` | 67 | a string marked as a word | Equivalent: a quoted string is never in `RESERVED` or `SOFT`, so it doesn't fold. |
| `warehouse_compare.py` | 322 | the default order 10 → 11 | Equivalent: only a changed schema uses the default, so nothing ties with it. |
| `orchestration/expressions.py` | 191, 194, 197 (4) | `toks[i][0]` → `toks[i][1]` | Equivalent: a punctuation token's kind and value are the same text. |
| `orchestration/expressions.py` | 223, 229 (2) | `toks[i + 3][0]` or `arg[0]` → `[1]` | Equivalent: the same, for `)`. |
| `orchestration/expressions.py` | 250 | `references()` reading as interpolated | Equivalent: it returns only the references, which don't depend on that flag. |
| `localdata/tsql_format.py` | 22 | `culture` marked required | Equivalent: `build` makes the node directly, so sqlglot never checks its arguments. |
| `localdata/tsql_format.py` | 159 | a run counted from 0 | Equivalent: the loop counts the first letter itself, so the run's length is the same. |
| `localdata/tsql_format.py` | 177 | `n == 1` → `n == 0` for `y` | Equivalent: padding one digit to a width of one leaves it as it is. |
| `localdata/tsql_format.py` | 192 | the empty-format return dropped | Equivalent: unreachable, since an empty format is refused first and every format character adds a part. |
| `localdata/tsql_format.py` | 195, 227, 239, 243, 244 (5) | `||` made NULL-safe | Equivalent: DuckDB writes it as `||` either way, and the value is never NULL there (a NULL is answered before). |

### Not in this batch

Batch 30's modules.

