# Mutation testing, batch 188

One module, run uncapped: every one of its mutants, not a sample of 150.
- Spark's regular expressions and string results on this PC: Java's pattern and replacement syntax as RE2 reads it, the refusals that name what RE2 can't read, and the string functions whose results differ on DuckDB (`spark_strings.py`).

Batch 89 sampled 150 of this module's 458 mutants then (72.0% before, 93.3% after). This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 458 of 458, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` ad1ac4cf8 (2026-11-18); the module is unchanged on `main` bdf2c1775, where the new tests pass too. It used `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own (`--basetemp` for the "before" run, a fresh one per run for the "after" run), and none was left too deep to remove, so another run's leftover files could not fail a mutant. A few "killed" mutants were run again by hand to see a test really fail.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_spark_strings.py::test_what_still_differs_is_listed_in_the_compatibility_docs` reads `docs/`, so it was left out with `--deselect`, as in batch 89. Batch 89 also ran test_spark_sql_names and test_spark_views_text; they take about 17 s together, more than the rest, so they were left out of this run; the score counts only the files given.

A mutant whose run times out counts as killed. Two timed out in the "before" run, each a loop that no longer moves on: `i += 1` to `+= 0` after a lazy quantifier (line 203) and after a group that isn't a flag group (line 185). None timed out in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_strings.py` | test_spark_strings, test_spark_strings_mutation, test_spark_missing_functions, test_spark_function_names (+ test_spark_strings_uncapped) | 458 of 458 (430 + 28), uncapped | 398 / 60 | **86.9%** | 381/430 → 411/430 | 17/28 → 25/28 | 436 / 22 | **95.2%** | 21 equivalent, 1 not killed |

### Bugs found

- On DuckDB 1.5.6, a concatenation with a NULL inside another one, `('a' || NULL) || 'b'` or `concat(concat('a', NULL), 'b')`, comes back as an `int32` column, where Spark's is a string. The value is NULL either way, so only the type is wrong. `spark_text_results` casts a concatenation to text only when one of its own arguments is the NULL and it isn't inside another concatenation (line 461), so neither the inner nor the outer one is cast. DuckDB 2.0.0 gives a string here. The test is marked `xfail(strict=True)` on DuckDB 1.x and runs as a plain test on 2.0; it isn't fixed here.

### What the survivors were, and the tests that now kill them

- **spark_strings.py**:
  - A `+` at the start, after a class (`[ab]+`) or after a lazy quantifier (`a*?+`) isn't refused as a possessive quantifier: only one after a greedy quantifier is.
  - A class ends at its `]` and another can follow (`[a][b]`); a `]` first in a negated class is a character (`[^]\s]`).
  - A backslash at the very end is left as it is, and `\p` at the end is an empty name.
  - `\p{IsL}` is `\p{L}`, and `\p{Is}` is kept.
  - `\d{2}` keeps its quantifier, and `\b{g}` is refused.
  - `\R` inside a class is refused; `\u` takes exactly four hex digits, and `\cA` is read once.
  - A comment group, `(?#x)`, is left for the engine, and a refused flag names the whole group (`'(?x)'`).
  - A replacement reads two-digit groups only while the pattern has them (`$12` with 11 groups is group 1 and a 2), `$9` is fine and `$10` or `$11` is refused on this PC; a missing group's message says which groups there are, or to use 0.
  - `regexp_extract` takes group 1 by default, which a pattern without groups doesn't have, and a group up to 9; group 10 or 12 is refused on this PC, also for a pattern from the data. Position 1 is the first character.
  - An index from the data (`split_part`, `left`) is read when the query runs, and a pattern that isn't text is left for the engine.
  - The DataFrame wrapper passes a call that stops before its replacement or group.
  - → `test_spark_strings_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_strings.py` | 292 | `_swap`'s `return new` to `return None` | Equivalent: only `_regex` returns what `_swap` gives, and no caller reads what `_regex` returns. |
| `spark_strings.py` | 306 | `_around`'s `return new` to `return None` | Equivalent: only `locate` reads it, and without it the inner call is cast instead, with the same `int` (as batch 89 found). |
| `spark_strings.py` | 414 | `len(args) < 2 or not any(...)` to `and`, and `< 2` to `< 1`, in `concat_ws` | Equivalent: a `concat_ws` without an array then goes the array way, each value cast to text, and run by hand it gave the same text and type for doubles, floats, decimals, dates, timestamps, booleans, NULL, binary, structs and maps. With one argument there is nothing after the separator, so it is left to DuckDB either way. |
| `spark_strings.py` | 196, 151 | `c == "?" and quantified` to `or`, and `quantified = False` to `True` after `(` | Equivalent in effect: they differ only for a quantifier right after another one or right after `(` (`a**+`, `(+a)`), which Java and RE2 both refuse, so both stop. |
| `spark_strings.py` | 286 | `_text`'s `and` to `or`, so a number reads as text | Not killed: it changes only a number written where Spark takes text, such as `regexp_replace('a1', 'a', 7)`. This PC stops there with DuckDB's own error, and the mutant would give Spark's answer, `'71'`; a test would pin the stop. |
| `spark_strings.py` | 459, 457 | which NULL arguments of `\|\|` are cast to text | Equivalent: the whole concatenation is then cast to text anyway, so values and types came out the same by hand (as batch 89 found for line 457). |
| `spark_strings.py` | 468 | `node is statement or (...)` to `and` | Equivalent: the statement is a query, never a length, so the mutant only casts a length already cast to `INT` again, the same value. |
| `spark_strings.py` | 324 | the default group 1 to 0 | Equivalent: sqlglot fills in group 1 when `regexp_extract` or `regexp_extract_all` is written without one, so `group is None` never happens. |
| `spark_strings.py` | 344 | `replacement is not None and not NULL` to `or` | Equivalent: as batch 89 found. |
| `spark_strings.py` | 418 | `len(parts) == 1` to `== 0` | Equivalent: DuckDB's `LIST_CONCAT` of one list is that list (as batch 89 found). |
| `spark_strings.py` | 242 | `end > 0` to `end > 1` and to `end >= 0` for `${` | Equivalent: `}` is searched for from the `$`, so it is found two or more places on, or not at all (-1) (as batch 89 found). |
| `spark_strings.py` | 73, 80 | `end < 0` to `end < 1` and to `end <= 0`, for `\Q` without `\E` and `\p{` without `}` | Equivalent: each search starts after the escape, so it finds nothing (-1) or a position of 2 or more, never 0 or 1. |
| `spark_strings.py` | 256 | `i + 1` to `i - 1` and to `i + 0` while reading a replacement group's digits | Equivalent: any group the loop builds is 10 or more and refused just after it, so the position isn't read again. |
| `spark_strings.py` | 169 | `flags.group(0) != ":"` to `flags.group(1)` | Equivalent: for `(?:`, group 1 is empty, so the flags branch writes `(?:` itself, the same text the other way writes a character at a time. |

