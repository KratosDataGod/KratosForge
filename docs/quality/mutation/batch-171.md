# Mutation testing, batch 171

One module, run uncapped: every mutant, not a sample of 150. Batch 11 sampled 150 of its then 1133 mutants (68.0% before, 91.3% after).
- The SQL compatibility rules behind the SQL editor's and the notebook's compatibility check (`sqlcompat/rules.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 1212 of 1212 mutants ran (1141 point + 71 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did.

The set is batch 11's own files (test_sqlcompat_rules_mutation, test_sql_compat) and the other files that check the rules' findings: test_boundary_mutation_3, test_sqlcompat_analyzer_mutation, test_sqlcompat_practices_mutation, test_sqlcompat_profiles_mutation, test_sql_practices, test_snowflake_sql_variadic, test_learn_fixes and _edges, test_learn_guides, test_localdata_engine_mutation and test_tsql_contract. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` and test_practice_docs read pages under `docs/`, which the mutator's copy of `sidecar/` doesn't have, so they were left out; test_learn_labs, test_sql_portability and the spark_* files were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. Nine timed out in the first run, each a loop that never ends: the `_writes` loop condition negated (line 985), and a step of `+ 1` made `+ 0` (or `- 1`) in the name reader (line 296), the bracket scan (line 455), the column-list readers (lines 750, 751, 763 and 764), the CREATE TABLE skip (line 773) and the write-verb skip (line 986). None timed out in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sqlcompat/rules.py` | test_sqlcompat_rules_mutation, test_sql_compat, test_boundary_mutation_3, test_sqlcompat_analyzer_mutation, test_sqlcompat_practices_mutation, test_sqlcompat_profiles_mutation, test_sql_practices, test_snowflake_sql_variadic, test_learn_fixes, test_learn_fixes_edges, test_learn_guides, test_localdata_engine_mutation, test_tsql_contract (+ test_sqlcompat_rules_uncapped) | 1212 of 1212 (1141 + 71) | 996 / 216 | **82.2%** | 934/1141 → 1096/1141 | 62/71 → 65/71 | 1161 / 51 | **95.8%** | 50 equivalent, 1 not shown to differ |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sqlcompat/rules.py**:
  - The text each finding points at, which no test read: BULK INSERT, SET ROWCOUNT, FOR XML, FOR JSON in a subquery, SET TRANSACTION ISOLATION, SAVE TRAN, BEGIN DISTRIBUTED TRAN, WITH MARK, BEGIN TRAN, INSERT OVERWRITE, CREATE OR REPLACE TABLE, TRUNCATE TABLE, DROP COLUMN, ALTER COLUMN, CREATE (UNIQUE) INDEX, EXEC AS, WITH RECURSIVE, GROUP BY ALL, a three- and four-part name, a @variable, OPTION (...), and an endpoint write (`UPDATE t`, `INSERT INTO t`).
  - Words read as names or as keywords: a table named rlike, foreign or temp, a column t.rlike or x.limit, a CTE named materialized, `ORDER` read as a name before WITH (...), INTO inside parentheses, `[7]` before a division, `7.5 / 2`.
  - Statements that end early: RLIKE, NOT, `[`, `NOT IN (SELECT`, `ADD CONSTRAINT c DEFAULT`, `datetime2(7)` and `db..` as the last words; `CREATE TABLE ##`; a name ending in a dot.
  - Statement starts: the body after AS in a procedure, after IF ... ELSE and BEGIN, and after a CTE before DELETE or MERGE; SET in a later statement's UPDATE.
  - Keys: each key's own NOT ENFORCED, and NOT NULL isn't NOT ENFORCED.
  - CTEs: AS MATERIALIZED and AS NOT MATERIALIZED, a bracketed name, a name that differs only in case.
  - Calls: each call reported once; calls inside parentheses keep their arguments (ISNULL, split, date_format); an unknown call isn't dropped by a later finding; a hint's INDEX(...).
  - The clock, case, decimal and division rules: CURRENT_DATE in parentheses, only the first string comparison told about case, `*` after SELECT isn't a product, a plain and a TEMP view, DuckDB's `SELECT [a]`.
  - Names in the titles: DROP PROC is "procedure", the digits of datetime2(7), the kind after ADD CONSTRAINT, long names cut to 64 characters, and the whole-number division's result.
  - → `test_sqlcompat_rules_uncapped.py`.

### Left

Fifty are equivalent:
- `return False` → `return None` in `_where_follows` (two), `_after_top`, `_regex_pattern` and `_update_statistics`: every caller only tests the result's truth. The `@check` decorator returning None: the function is already registered and no name is called again.
- A scan that starts one token earlier or later and lands on a token that can't match: `_close` from i - 1 or i (a name and a `(`, never a `)`), `_after_top` from the `)` or the `[` (never a `(`), the bracket scan from the `[`, the ALTER TABLE ADD NOT NULL scan from ADD, `_regex_pattern`'s commas from the name or the `(`, `_args` counting from the name or the `(`, the FOREIGN KEY look-back from REFERENCES or stopping before CREATE, `_after_top` stopping before the first token, the self-reference scan from `(`, the key span from the key word.
- `_args` given 0 instead of 1 for a call with no arguments (three): every caller compares with 2 or more. `_args`, `_regex_pattern`, `_close` and the hint readers given the name's or WITH's index instead of the `(`'s: they have the same depth, so the same `)` is found (eight).
- Bounds that can't be reached: CREATE, ALTER or DROP as the last token (three: its rest is empty), `CONVERT` as the first token of a CTAS (two), a column list read once past its end (two), a rest list one word longer than is read.
- INTO skipped after MERGE: INTO is itself a keyword the name reader starts from, and the same finding is reported once. The SYN-007 position clamped to the end or end - 1, and its fallback token: a statement ends where its last token ends, so the last token is found either way (three). SET looked for from UPDATE's end instead of STATISTICS': the word between holds no SET. The verb itself added to the words after CREATE. The table-name reader testing the keyword itself for `(` instead of the next token: after FROM ( it then reads no name from the `(` and skips it anyway. INTO's own depth tested twice: a token before a depth-0 INTO has depth 0.

One is not shown to differ: the FN-001 filter `a <= h.start < b` → `<= b` (line 417, boundary) drops an unknown call that starts exactly where another finding ends. No statement was found where a call is glued to the end of another finding (719 variants of the test statements with calls glued to the token before them gave the same findings), but that is not a proof.

