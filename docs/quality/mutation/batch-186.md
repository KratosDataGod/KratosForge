# Mutation testing, batch 186

One module, run uncapped: every one of its mutants, not a sample of 150.
- The Snowflake SQL translator that runs Snowflake cells on this PC (`snowflake_sql.py`).

Batch 11 sampled 150 of this module's mutants then, 732 in all (88.0% before, 96.0% after). The module has grown since, with statements (MERGE, IDENTITY columns, table keys, transactions, ALTER SESSION), FLATTEN, the event and interpolation functions, JSON types and more, to 1250 mutants. This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 1250 of 1250, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` 75bf5b397 (2026-11-19). `main` has not changed the module since (up to 9dbe9175d). It used `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy (1104 passed). Each pytest run had a temporary folder of its own, a fresh one per run through a local plugin, and none was left too deep to remove, so another run's leftover files could not fail a mutant. The longest streak of kills was 55 of the 1250, and 28 of the last 100 mutants survived, so no run late in the batch failed for another reason. Three "killed" mutants picked at random (792, 959 and 1051) were run again by hand, each failed a real assertion, and the whole set also passed against the module as `ast.unparse` writes it.

A first run of this batch was lost when the container restarted, at about mutant 590. It was not used: the run here started again from the first mutant.

Batch 11 ran test_local_sql_mutation, test_snowflake_coverage, test_snowflake_sql, _edges, _parity, _variadic and test_sql_lambdas. The run here adds test_snowflake_sql_mutation (batch 11's own), test_snowflake_sql_parity_mutation, test_snowflake_coverage_mutation, test_snowflake_coverage_edges and test_snowflake_documented_edges; the score counts only the files given. Four tests read `docs/` or the module's source text, so they were left out with `--deselect`: `test_snowflake_sql.py::test_the_docs_table_is_the_codes` and `::test_every_rule_the_code_uses_is_in_the_docs_table` (as in batch 11), `test_snowflake_coverage_mutation.py::test_the_page_is_the_repositorys_docs_snowflake_functions_md` and `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`. Others that test the module, test_snowflake_sql_statements and test_snowflake_coverage, were left out for time.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used.

A mutant whose run times out counts as killed. One timed out in the "before" run: `_strftime`'s step through a format model (line 580) changed so it never moves on. None timed out in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `snowflake_sql.py` | test_snowflake_sql_mutation, test_snowflake_sql_parity_mutation, test_snowflake_coverage_mutation, test_snowflake_sql, test_snowflake_sql_edges, test_snowflake_sql_variadic, test_snowflake_coverage_edges, test_snowflake_documented_edges, test_sql_lambdas, test_local_sql_mutation, test_snowflake_sql_parity (+ test_snowflake_sql_uncapped) | 1250 of 1250 (1225 + 25), uncapped | 1069 / 181 | **85.5%** | 1058/1225 → 1164/1225 | 11/25 → 23/25 | 1187 / 63 | **95.0%** | 49 equivalent, 9 not reached, 5 not killed |

The "after" figures are from two runs of the survivors with the new file first: the 181 left by the "before" run (115 killed), then the 66 left by that run with three more tests (3 killed). 49 of the 63 left are equivalent and 9 are code no query here reaches. Without those 58, the score would be 1187 of 1192.

### Bugs found

- `TO_TIMESTAMP_NTZ` of a number from the data stops here with DuckDB's "Scalar Function with name to_timestamp_ntz does not exist". Snowflake reads the number as seconds since 1970, as it does here for a number written in the query. The rewrite to `TO_TIMESTAMP(...) AT TIME ZONE 'UTC'` is made only for a number written in the query (`_written_number`, line 1260), so a column, or a written number plus a column, was left as it is. #3133 has since fixed it on `main`, so the two tests that pinned it are now plain tests.

Seen while writing the tests, not pinned:
- `TO_TIMESTAMP` of a text column stops with a DuckDB error, where Snowflake reads the text.
- `LPAD` of a binary with a binary pad stops with DuckDB's Binder error (`lpad(BLOB, INTEGER, BLOB)`); Snowflake pads binary.
- `TIME_SLICE(d, n, 'DAY')` with `n` from the data is written by sqlglot as `TIME_BUCKET(INTERVAL (n) DAY, d)`, without Snowflake's 1970 origin: 2024-01-05 for `n = 2`, where a length written in the query gives 2024-01-04.
- `SET (a, b) = (1, 2)`, which Snowflake takes, stops with DuckDB's Parser error.
- `SELECT $1 FROM VALUES (1), (2)` stops with DuckDB's prepared-parameter error, where Snowflake gives the first column.
- `TO_CHAR(d, 'MM')` of a timestamp column whose type the translator can't see is refused as a number format (SFX-003).
- `CEIL` and `FLOOR` to 7 or more places give a DOUBLE (the FLOAT rewrite, as the code says), where Snowflake keeps the NUMBER: `ceil(123456789012345678901234567890.12345, 7)` is 1.2345678901234568e+29 here.

### What the survivors were, and the tests that now kill them

- **snowflake_sql.py**:
  - AS_DECIMAL with a precision and a scale, a precision only (scale 0), precision 1 and a scale equal to the precision; precision 0 and DATASKETCHES_HLL with three arguments are refused; HLL of a one-argument call counts the call's values.
  - TYPEOF of a path into JSON written in the query: an array, an object, a decimal; numbers past 38 digits in all, past 37 places, at exactly 38 digits and 37 places; an element just past the end, far past it, and a subscript into an object.
  - The event functions: a LAG over another window inside CONDITIONAL_TRUE_EVENT keeps its own order; an event function outside a SELECT is refused with its page.
  - IDENTITY and AUTOINCREMENT: start and step, a start that isn't a whole number or is past BIGINT at either end, a step of 0; table keys aren't enforced, a key column is NOT NULL (once), and a key naming no column is dropped.
  - REGEXP_SUBSTR with `e` (a written pattern, one from the data, one Python can't read) and with `m` and a pattern from the data.
  - TO_TIMESTAMP_NTZ of a number in brackets or negated; TO_DATE and TRY_TO_DATE of text holding seconds, milliseconds, microseconds or nanoseconds, at each unit's first value, either side of midnight, and past DuckDB's dates; a function this PC doesn't know keeps its digit text.
  - LPAD and RPAD with an empty pad, no pad or a written one; EDITDISTANCE with a maximum from the data or negative; CEIL and FLOOR at 6 and -18 places stay numbers, and a 32-digit number to 7 places runs.
  - A division in IFF still stops on a zero divisor, in either branch.
  - Refusals by rule and name: EXECUTE TASK (named by its first word), OBJECT_AGG with one argument, a mixed array (named after the function written), TO_CHAR with a number as its format, a statement sqlglot can't write (with sqlglot's message), INSERT ALL and INSERT FIRST, dynamic tables, stages, sequences and the others in the parametrised test; DDL after COMMIT or ROLLBACK runs.
  - SAMPLE's methods; a cast of slashed text to VARCHAR stays text; date arithmetic says a DATE may come back as a TIMESTAMP unless the value is plainly a DATE; TIME_SLICE with a length from the data runs.
  - MERGE stops on a duplicate source row before a conditional update, and a MERGE from a subquery with no name runs; PIVOT columns of numbers are named without quotes; IDENTIFIER($t) keeps the table's alias; ALTER TABLE dropping one column runs; `t.seq` of another table beside FLATTEN is that table's column; two TO_CHAR calls of a column keep their own formats.
  - → `test_snowflake_sql_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `snowflake_sql.py` | 1163 | the TO_JSON-wrapping check's `and` to `or`, so no other function call is wrapped | Not killed: no call was found where it matters. ARRAY_COMPACT, ARRAY_SLICE, ARRAY_DISTINCT and ARRAY_CAT over an ARRAY column are typed nodes, not plain calls, and gave the same JSON. |
| `snowflake_sql.py` | 895 | TO_DATE's digit-text check `and` to `or`, so a number written in the query is read too | Not killed: it changes only `to_date(1700006400)`, a NUMBER, which Snowflake's TO_DATE doesn't read as seconds (only text of digits). This PC stops there with DuckDB's Catalog error, and the mutant gives a date; a test would pin the stop. |
| `snowflake_sql.py` | 1005 | MERGE's SET column check `and` to `or` | Not killed: it changes only a SET column qualified by a database or another table (`set sch.t.a`, `set s.a`), which DuckDB's parser refuses here ("Qualified column names in UPDATE .. SET not supported"); the mutant drops the qualifier. A test would pin the stop. |
| `snowflake_sql.py` | 980 | the session-variable check `and` to `or` | Not killed: it changes only a positional `$1`, which stops here with DuckDB's prepared-parameter error; the mutant reads it as a session variable and gives NULL. Snowflake gives the first column; a test would pin the stop. |
| `snowflake_sql.py` | 1571 | `_window_key`'s PARTITION BY `or` to `and`, so partitions are left out of the key | Not killed: a LAG over another partition inside an event's condition is then ordered by the event's place numbers, which tie across partitions. DuckDB's order among ties gave a different answer in 1 of 3 runs, so no test kills it every time. |
| `snowflake_sql.py` | 718 | the raise when sql.py's SOUNDEX template changes, removed | Not reached: a guard; the template has the text it looks for. |
| `snowflake_sql.py` | 1826 (two), 1829 (two) | the fallback when sqlglot's qualify can't read the statement: its `and` to `or`, and its return | Not reached: no statement was found that qualify can't read (`v[0]` with LATERAL FLATTEN, USING, NATURAL JOIN and a repeated alias all qualify). |
| `snowflake_sql.py` | 686 | the VALUES width's `else 1` to `0` | Not reached: sqlglot reads every VALUES row as a tuple, `values 1, 2` too. |
| `snowflake_sql.py` | 1950 | a JSON path's other-part answer, `_NOT_KNOWN` to `None` | Not reached: a slice (`:a[0:1]`) stops before it, and `[*]` isn't read as a written path (SFD-007). |
| `snowflake_sql.py` | 2047 | `_nonzero_number`'s except branch, `False` to `True` | Not reached: every number literal sqlglot reads parses as a float. |
| `snowflake_sql.py` | 376 | ALTER's kind check (WAREHOUSE, STAGE, TASK, PIPE, STREAM) `or` to `and` | Not reached: sqlglot reads those ALTERs as commands, refused earlier as SFN-099. |
| `snowflake_sql.py` | 1268, 1162, 1164, 1819, 2080, 219, 2038 | a `return False` to `return None` | Equivalent: each is read only for truth. |
| `snowflake_sql.py` | 2025, 2032 (two), 2043, 2047 | `_div0_branch`'s answers and `_nonzero_number`'s `False` to `None` | Equivalent, as batch 11 found: the zero check is then added inside the branch DIV0 never takes with a zero divisor, or the answer is read only for truth. |
| `snowflake_sql.py` | 1559, 1610 | `COALESCE(running, 0)` to `1` for the event functions | Equivalent, as batch 11 found: the running sum up to the current row is never NULL, also with a NULL first value. |
| `snowflake_sql.py` | 208 | `_scalar_kind`'s `'other'` to `None` | Equivalent: either is a kind other than text and number in the set compared. |
| `snowflake_sql.py` | 603 | the cast to DATE round DATEADD and DATE_TRUNC of a DATE, negated | Equivalent: this sqlglot writes both of a plain DATE as a DATE itself, so the extra or missing cast changes nothing. |
| `snowflake_sql.py` | 996, 1046, 1039, 2328, 2331 | the MERGE, CREATE TABLE, emptied-properties and ALTER SESSION checks | Equivalent: another statement goes on and finds nothing to change (no WHEN clauses, no column schema, no ALTER SESSION action); emptied properties are written as nothing; UNSET is refused earlier as SFN-022. |
| `snowflake_sql.py` | 1153 | the VARIANT[] check's `and` to `or` | Equivalent: more TO_JSON calls are wrapped in a check whose other branch is the same TO_JSON. |
| `snowflake_sql.py` | 216 | the mixed-array check of a call on an array, `and` to `or` | Equivalent: the array inside the call already gives the same SFN-025. |
| `snowflake_sql.py` | 859 | EDITDISTANCE's maximum check `and` to `or` | Equivalent: a maximum of `-n` or `-(n)` from the data gives the same distances; the CASE on the maximum is written either way. |
| `snowflake_sql.py` | 920 | ARRAY_INSERT's written-position check `and` to `or` | Equivalent: a position from the data is refused either way. |
| `snowflake_sql.py` | 966 | the MERGE source's alias check, negated | Equivalent: a source with an alias gets the same alias again, and one without is read by its own name. |
| `snowflake_sql.py` | 974 | the PIVOT string-value names, `or []` to `and []` | Equivalent: the same SQL (`'a' AS "'a'"`) either way. |
| `snowflake_sql.py` | 1206 | `_pattern_groups`' check `and` to `or` | Equivalent: a number and a non-literal give 0 either way, and a string goes on either way. |
| `snowflake_sql.py` | 1715 | `_with_value`'s column check `and` to `or` | Equivalent: the template's lambda variable is an identifier, not a column, so `__sf_arg` is the only column with no table. |
| `snowflake_sql.py` | 1943, 1946 | the JSON path's key and subscript checks `and` to `or` | Equivalent: sqlglot's path keys always hold text and its subscripts a whole number (a wildcard holds neither). |
| `snowflake_sql.py` | 1024, 566 (two), 780 (two), 1729, 1832 | `_whole`'s 19 digits to 20; `_strftime`'s `end < 0` to `< 1` and `<= 0`; `_epoch_date`'s `scale <= 6` to `<= 7` and `< 6`; `[:2]` to `[:3]`; `zip(strict=True)` to `False` | Equivalent: a 20-digit number is past 2**63 and refused just after with the same note; `find` from `i + 1` is never 0; the scale is 0, 3, 6 or 9, and at 6 the other branch divides by 10**0; the list is always two long; the lists were just checked to match. |
| `snowflake_sql.py` | 817 | REGEXP_SUBSTR's first-occurrence check `and` to `or` | Equivalent: a later occurrence is then wrapped in `CASE WHEN REGEXP_MATCHES ... THEN` it, which adds NULL only where there is no match, where it is NULL already. |
| `snowflake_sql.py` | 888 | TRY_CAST to DATE's check `and` to `or` | Equivalent: TRY_CAST of text `_auto_date` can't read becomes NULL itself, where DuckDB's TRY_CAST gives NULL too (`2024/05/06`, `May 6, 2024`, `20240506`); the ISO dates DuckDB reads are kept out by the check before it. |
| `snowflake_sql.py` | 1146, 2442, 2448, 2450 | the ARRAY cast check; IDENTIFIER's alias, SET's assignment and SET's text checks, `and` to `or` | Equivalent: a type's parent other than a cast has no `to`; a column in a select has no alias there; every SET item is an assignment to a name; SET of a number then stops either way in IDENTIFIER. |
| `snowflake_sql.py` | 1505, 1591 | `exp.paren(..., copy=False)` to `True` | Equivalent: the same SQL for the interpolation functions. |
| `snowflake_sql.py` | 499 | FLATTEN's path prefix `$` and `$.` swapped | Equivalent: a PATH starting with `[` is refused before, and DuckDB reads `$b` as `$.b`. |
| `snowflake_sql.py` | 839 | CEIL and FLOOR's lowest scale -18 to -19 | Equivalent: `ceil(5, -19)` and `floor(15, -19)` give the same values and type either way. |
| `snowflake_sql.py` | 1824 | qualify's `identify=False` to `True` | Equivalent: only the copy read for types is quoted; the types are the same. |

