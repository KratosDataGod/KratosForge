# Mutation testing, batch 150

Local T-SQL's translator, and the two checks it runs on each statement before any rewrite, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The main translator: what it refuses by name before parsing (DBCC and the rest), decimal precision and scale, views, `#temp` tables and `IDENTITY`, `STR`, `ISJSON`, `STRING_SPLIT` and the `CONVERT` notes (`localdata/tsql.py`). Batch 3 scored it at 100% when it had 694 mutation points; it has 1,885 now, and this run samples them afresh.
- Common table expressions: recursion refused by name, nested CTEs with a note, and an UPDATE or DELETE through a CTE refused (`localdata/tsql_ctes.py`, not covered before).
- Hints, query labels and statistics statements: the text pass that rewrites what sqlglot can't read, and the tree pass that leaves out with a note what changes no rows or refuses the rest by name (`localdata/tsql_hints.py`, not covered before).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `localdata/tsql_ctes.py` has fewer than 150 mutants, so all of them ran; the other two were sampled to 150. Each test set was run with `-x`, so a mutant's run stops at its first failing test. The runs of `tsql.py` and `tsql_hints.py` were given `--basetemp` (a folder of their own), so they never shared pytest's temporary folders with another run on the same machine.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Three tests of `test_tsql_loud_gaps.py` read `docs/local-data.md` or the module's source (the refused-list checks: `test_the_refused_list_in_the_docs_names_every_refused_function`, `test_the_docs_refused_list_names_exactly_the_refusals_checked_here` and `test_every_function_refused_whole_is_in_the_docs_refused_list`), so they were left out of the `tsql.py` run with `--deselect`. `tsql.py`'s set leaves out `test_tsql_contract`, `test_tsql_coverage` and `test_tsql_rules_differential`, which take the longest and check the translator against recorded answers rather than one behavior at a time. An open pull request (#2999) rewrites the string-edge part of `localdata/tsql.py`: `_string_edges`, `_repeat_count` and `_charindex` (SUBSTRING, STUFF, LEFT and RIGHT, TRANSLATE, REPLICATE, SPACE and CHARINDEX at their edges). Five of the 150 sampled mutants are in those functions (lines 2405, 2441, 2462, 2470 and 2485; two killed and three surviving before), so they are counted as out of scope (rewritten in #2999) and left out of `tsql.py`'s score, and no tests were written against main's version of them. The scores below are of the other 145. The "after" numbers for `tsql.py` come from re-running its 22 survivors with `--only` once its new tests were in, as in batch 3; the other two modules were run again in full. Each of the 15 in-scope survivors that the new tests kill was also checked on its own against `test_tsql_mutation.py` alone, in its final form, and fails there.

A mutant whose run times out counts as killed, as in earlier batches. In `tsql_hints.py` four mutants time out before and six after, each a scan of the batch's tokens that no longer moves on: `j += 1` to `j += 0` in OPTION's scan (line 131), `i += 1` to `i += 0` past `UPDATE statistics SET` (line 93) and past any other word (line 105), and the statistics option refusal removed (line 226), before and after; and after, also the scan going on from `end` or `end - 1` instead of `end + 1` (line 111, two mutants), which never moves past an OPTION without its closing bracket, as the new tests write it. No `tsql.py` or `tsql_ctes.py` mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql.py` | test_local_tsql, test_local_tsql_more_edges, test_sql_lambdas, test_tsql_loud_gaps, test_tsql_arithmetic, test_tsql_dates, test_tsql_strings, test_tsql_data_types, test_tsql_decimals, test_tsql_names, test_tsql_scripts, test_tsql_changes, test_tsql_formatting, test_tsql_json, test_tsql_windows, test_tsql_time_travel, test_tsql_national_text, test_tsql_loading (+ test_tsql_mutation) | 145 of 1885 (140 + 5), after 5 out of scope (#2999) | 126 / 19 | **86.9%** | 122/140 → 136/140 | 4/5 → 5/5 | 141 / 4 | **97.2%** | 4 equivalent |
| `localdata/tsql_ctes.py` | test_tsql_ctes, test_local_tsql (+ test_tsql_ctes_mutation) | 54 of 54 (54 + 0) | 51 / 3 | **94.4%** | 51/54 → 52/54 | none | 52 / 2 | **96.3%** | 2 equivalent |
| `localdata/tsql_hints.py` | test_tsql_hints, test_local_tsql (+ test_tsql_hints_mutation) | 150 of 313 (139 + 11) | 126 / 24 | **84.0%** | 121/139 → 137/139 | 5/11 → 11/11 | 148 / 2 | **98.7%** | 2 equivalent |

Batch 93 (#2985), which landed while this batch ran, sampled the same 150 `tsql_hints.py` mutants (seed 7) and reached 100% with a wider set of test files. This batch's row measures the module's own hint tests alone, and its new tests are in `test_tsql_hints_mutation_150.py`, beside batch 93's `test_tsql_hints_mutation.py`.

One bug was found, in `localdata/tsql_hints.py`, and is fixed in the same change: a statistics statement whose `WITH SAMPLE n` had no `PERCENT` or `ROWS` and ended the batch (`CREATE STATISTICS st ON t (id) WITH SAMPLE 5`, `UPDATE STATISTICS t WITH SAMPLE 5`) stopped with Python's "list index out of range" instead of a message, because `SAMPLE` stepped three tokens ahead without checking that they were there. Followed by `;` and another statement, it ran and swallowed the `;`. Now any `SAMPLE` without a number and `PERCENT` or `ROWS` is refused by name, as SQL Server refuses it too; `test_a_sample_without_a_number_and_percent_or_rows_is_refused_by_name` pins it.

### What the survivors were, and the tests that now kill them

- **localdata/tsql.py**:
  - `DECIMAL(38, 20) * DECIMAL(38, 19)` (scales adding up to 39) is worked out in floating point and gives T-SQL's `DECIMAL(38, 6)`, not DuckDB's "Max scale is 38" error.
  - `RAND()` in decimal division and multiplication is drawn once (the exact forms read each side more than once).
  - A decimal divided by a `SMALLINT`, `TINYINT` or `INT` has T-SQL's scale (a `SMALLINT` counts as 5 digits).
  - The literal `0.` counts as one digit, so `0. * DECIMAL(38, 10)` keeps 8 places.
  - A text too long for its column names the table (`dbo.v`) and the column, and a `#temp` table with `WITH (DISTRIBUTION = ...)` runs.
  - `IDENTITY` is refused by name, also after `NOT NULL`, and no table is made.
  - `ALTER VIEW v ADD x INT` is refused with the shape ALTER VIEW takes, and the view is kept.
  - A view keeps the schema it names (`sales.o`) and reads its own CTE.
  - `STR` takes a length from 1 to 8000 and decimals from 0 to 16, and refuses 8001 with its message.
  - `ISJSON()` and `ISJSON` with three arguments are refused with its message; `STRING_SPLIT` with a NULL separator is refused by name.
  - The note for an unchecked `CONVERT` style lists the styles that are checked, ending with 126.
  - `DBCC` after another statement is refused by name.
  - → `test_tsql_mutation.py`.
- **localdata/tsql_ctes.py**:
  - `UPDATE x ... FROM t AS x JOIN c` beside a CTE `c` changes the table: x is the table's alias, not the CTE's.
  - → `test_tsql_ctes_mutation.py`.
- **localdata/tsql_hints.py**:
  - A batch that names OPTION but can't be tokenized stops with the parser's words; `PRINT` before a labelled query is refused as PRINT.
  - A table called `[option]` with a column list, and a column called `[create]`, `[update]`, `[drop]` or `[option]` at the end of a batch that says STATISTICS, are left as written.
  - A hint sqlglot can't read keeps the option beside it (`FORCE DISTRIBUTED PLAN, LABEL = ...`, `FORCE ORDER, FORCE SINGLE NODE PLAN`), and its note names it (`OPTION (USE HINT)`, `OPTION (FORCE SINGLE NODE PLAN)`).
  - `OPTION (RECOMPILE, FORCE SINGLE NODE PLAN)` still refuses RECOMPILE by name, and an OPTION without its closing bracket stops with "Expecting )".
  - A statistics statement that stops early or isn't written as T-SQL has it (`CREATE STATISTICS st`, `... ON t`, `... ON t id`, no ON, `CREATE`, `UPDATE` or `DROP STATISTICS` alone, `DROP STATISTICS t.st x`) is refused with the shape it takes.
  - An index hint is refused by its own name (`INDEX`, not `INDEX(IX)`).
  - → `test_tsql_hints_mutation_150.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql.py` | 718 | `if table.alias or table.db: return False` negated in `_names_its_alias` | Equivalent in practice: `_joined_changes` runs before `_names` and rewrites `UPDATE x ... FROM t AS x` and `DELETE x FROM t AS x` to name the table itself, so the function never answers True after it; negated, it answers False sooner. |
| `localdata/tsql.py` | 726 | `return False` to `return None` | Equivalent: the caller only tests the answer's truth. |
| `localdata/tsql.py` | 1739 | `zip(strict=True)` to `False` | Equivalent: the two lists' lengths are compared just before. |
| `localdata/tsql.py` | 1824 | `isinstance(node, exp.Literal) and node.is_string` to `or` in `_rtrim` | Equivalent: only a number literal would differ, and none reaches `_rtrim`: `LEN`'s argument comes cast to text, and the comparisons call it for text sides only. |
| `localdata/tsql.py` | 2441, 2470, 2485 | three survivors in `_string_edges`, `_repeat_count` and `_charindex` | Out of scope: rewritten in #2999, so they are left to that pull request's code. |
| `localdata/tsql_ctes.py` | 62, 73 | `return False` to `return None` in `_names_itself` and `_shadowed` | Equivalent: the callers only test the answer's truth. |
| `localdata/tsql_hints.py` | 91 | `return batch` to `return None` when the rest of `UPDATE STATISTICS` can't be tokenized | Equivalent: unreachable. The whole batch was tokenized just before, and the tokenizer scans a command's rest the same way before folding it into one string, so the rest can't fail on its own. |
| `localdata/tsql_hints.py` | 257 | `kept or None` to `kept and None` | Equivalent: the options kept are only ever MAXRECURSION, which `tsql_ctes` refuses before this pass runs, so the list is always empty and both give None. |
