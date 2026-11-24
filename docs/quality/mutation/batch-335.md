# Mutation testing, batch 335

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- An Apache Ossie document checked against the pinned JSON Schema for its version: the diagnostics, their messages and paths, and the limits that keep a bad file cheap (`ossie_validate.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 250 mutants, and the run ran all 250 of 250 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `ossie_validate.py`: test_ossie_profiles and test_ossie_schema. Together they take about 16 seconds, so none was left out. test_network_hosts names the module only in a comment, so it isn't in the set. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run's last two kills took about 11 seconds each, so both were checked by hand: each failed real tests (3 and 1). Three more kills, picked at random, were checked the same way: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (13, 70 and 55).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

The low "before" score is mostly the limits and the messages. The earlier tests check the limits by the module's own constants (`MAX_DIAGNOSTICS`, `MAX_DEPTH` and so on), so a changed constant changes the test with it. The new tests use the numbers themselves (200, 5,000, 80, 64, 100,000). They also check each rule's message and fix, one by one.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ossie_validate.py` | test_ossie_profiles, test_ossie_schema (+ test_ossie_validate_mutation) | 250 of 250 (242 + 8), uncapped | 159 / 91 | **63.6%** | 156/242 → 240/242 | 3/8 → 8/8 | 248 / 2 | **99.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ossie_validate.py**:
  - The limits, by their numbers: 200 diagnostics are shown and the 201st is one "more" line; the check stops after 5,000 schema errors and not before; nesting of 64 levels (in objects or in lists) is allowed and 65 isn't; a schema of 100,000 objects and lists is inlined and one more isn't; a list of exactly 800 kept diagnostics isn't cut, and one past it is cut to 200.
  - A short form: text is cut at 80 characters with `…`, `reprlib` keeps 70 of a string or another value, 40 digits of a number, two levels, and four items of a list, tuple, set or dict.
  - Each rule's own message and fix: const, minLength (one, or more), maxLength, minItems (one item, or items), maxItems, uniqueItems, pattern, the four number bounds, required (only the missing properties), enum (twelve values, then `…`), and any other rule. Types read as words ("text or null").
  - A missing property says where, unless it is at the top. Only anyOf and oneOf read their branches; the branch with the fewest problems is the one reported, and a value that matches no form lists at most six of them.
  - A check that ran says so. A document that isn't an object, has no version, can't be read, or is nested too deep isn't checked ("Not checked"), and keeps its version when it is text. A key that isn't text is reported under the detected profile without a schema check, and is named as text in the paths below it.
  - An unknown version shows the version as its value. An unresolvable `$ref` and a recursion each count as one problem. A check that stopped says "at least 1 more" even with few shown.
  - Diagnostics sort list positions by number, before names. A path keeps letters that aren't ASCII.
  - → `test_ossie_validate_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `ossie_validate.py` | 68 | `schema.get("$id") or ""` to `and ""` (the registry key) | Equivalent: jsonschema also registers the schema it checks under its own `$id`, so a `$ref` to that `$id` resolves either way (checked by hand). |
| `ossie_validate.py` | 154 | the sort key's `(1, 0, name)` to `(1, 1, name)` | Equivalent: every name gets the same middle value, so names still sort by name, and still after positions. |
