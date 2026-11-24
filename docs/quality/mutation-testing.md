# Mutation testing

How strong are the tests on the modules where a quiet bug costs the most? Line coverage says a line
ran; mutation testing says whether a test would notice if that line were wrong. Each batch scores a
few modules, adds tests for the survivors that matter, and explains every mutant it leaves.

## Where the results are

Each batch has its own page in `docs/quality/mutation/`, named `batch-NNN.md` with the batch
number padded to three digits, for example [batch 1](mutation/batch-001.md) and
[batch 36](mutation/batch-036.md), which added boundary mutants. Each page has the batch's
results, the survivors and the tests that now kill them, the mutants left and why, and any real
bugs found. Two pages describe property tests rather than one batch:
[the savings package never invents a saving](mutation/properties-savings.md) (before batches 191 to
193) and [the optimizer never invents a saving](mutation/properties-optimizer.md) (before batches
198 to 201).

Until 2026-11-24 every batch was a section of this page. Each page in the folder is its old section,
unchanged byte for byte, so the history reads as before.

**Adding a batch:** add one new `docs/quality/mutation/batch-NNN.md` and change no other doc page.
There is no hand-kept list of batches here, so two batch PRs never touch the same line. Take batch
numbers from the ledger the coordinating thread keeps, so two lanes don't use the same one.

The method and how to re-run below are batch 1's, and they still hold. Later pages say where a
batch changed them, such as the boundary mutants added in batch 36, the bytecode-cache fix
(#2836) and the unmutated pre-run (#2902).

## Method

- **Mutator.** A small AST mutator, run in batch 1 from outside the repository and since batch 2 kept
  as the dev-only script `sidecar/tools/mutate.py` (standard library and pytest only; the engine never
  imports it, so it adds no dependency). For each mutation point it rewrites the module with one
  change, runs the module's own test files with `pytest -x` and a 120 s timeout, and restores the
  file. mutmut was not used:
  it doesn't fit the sidecar layout (package under `sidecar/`, tests importing `fabric_desktop`
  from `PYTHONPATH`) without configuration that would live in the repository.
- **Mutations.** Comparison flips (`==`/`!=`, `<`/`>=`, `>`/`<=`, `is`/`is not`, `in`/`not in`),
  `and`/`or` swaps, negated `if`/`while`/conditional expressions, integer and boolean constants
  (0↔1, n→n+1, True↔False), a return value dropped to `None`, a `raise` removed, and `+`/`-`,
  `*`/`//` swaps. Docstrings are never mutated. Regular expressions are not mutated by this tool, so
  the redactor got a second run that deletes each secret pattern in turn (below).
- **Sampling.** At most 150 mutants a module, sampled with a fixed seed (7), so a re-run mutates the
  same points. Modules with fewer points are run in full.
- **Tests run against each mutant.** Only the module's own test files, listed in the table. A
  mutant that another file in the suite would catch still counts as surviving here: the point is
  whether the tests *about* the module pin its behavior.
- **Score** = (killed + timed out) / mutants run. A mutant that hangs the tests has been noticed,
  so a timeout counts as killed.
- **After.** The new tests were added to each module's set and the same sample re-run. The last
  few survivors in localdata/delta and retrying were then re-checked one by one after their final
  tests went in. Adding a test can't revive a killed mutant, so those re-checks give the final
  count.

## How to re-run

`sidecar/tools/mutate.py` (dev only: never run in CI, never imported by the engine) works in a
temporary copy of `sidecar/`, so a run in progress, or one stopped halfway, can't leave a mutant in
your working tree, and several modules can run at once. From `sidecar/`:

```sh
python tools/mutate.py fabric_desktop/retrying.py --out retrying.json -- \
  tests/test_retrying.py tests/test_retrying_more_edges.py tests/test_retry_after.py \
  tests/test_remote_retry.py tests/test_retrying_mutation.py
python tools/mutate.py fabric_desktop/redact.py --entries _SECRETS --out redact-entries.json -- \
  tests/test_redact.py tests/test_redactor_characterization.py tests/test_secret_detectors_userinfo.py \
  tests/test_git_secrets_shapes.py tests/test_secrets_at_rest.py
python tools/mutate.py fabric_desktop/delta_dml.py --only 186,222 --out recheck.json -- \
  tests/test_delta_dml_edges.py tests/test_delta_dml_mutation.py
```

Options: `--cap` (150) and `--seed` (7) choose the sample; `--entries NAME` deletes each entry of a
module-level list instead of mutating points; `--only` runs just the mutants with those indexes again,
to re-check survivors once tests are added. After `--`, the test files. Each line of output is
`<index> <line> <kind> <change> killed|survived|timeout`, and the `--out` JSON has the totals and every
result. Keep the seed at 7 to compare with the tables here: the script samples and mutates the same
points as the batch 1 scratch copy did. One module at a time takes 5 to 25
minutes on four cores; write_queue is the slowest, because a mutant that stops the queue's workers
hangs until the 120 s timeout. A timed-out run is stopped with every process it started (its own
process group, or `taskkill /T` on Windows); a kill that hasn't finished within 30 s is given up on,
so one mutant can't hang the run. Caches (anywhere) and a local `.venv` and build output (at the top of
`sidecar/` only, so a test fixture folder of the same name is still copied) aren't copied into the
temporary `sidecar/`, and `--only` names any index that isn't in the run's sample instead of quietly
skipping it.

Each mutant's tests run with no bytecode cache (`PYTHONDONTWRITEBYTECODE=1`), since #2836. Before
that, Python could reuse the cached code of the previous mutant when the two were the same size and
written in the same second, so a mutant could be scored as the one before it, in either direction.
Scores from batches 1 to 77 were taken without the fix and may be off by such a mutant; batch 78 and
later were taken with it. Two runs with the fix give the same result for every mutant.

Before any mutant, the tests run once on the unmutated copy, and the run stops with a message if they
fail or time out there. Only `sidecar/` is copied, so a test that reads `docs/` or `app/` fails in the
copy whatever the mutant, and without this check every mutant would count as killed. Earlier batches
left such tests out by hand; leave them out (or `--deselect` them) in the same way.

# Mutation testing, batch 331

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What a change to one column would break: the tables, Python and semantic model that read it (`meta/change_impact.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 165 mutants, and the run ran all 165 of 165 (uncapped). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `meta/change_impact.py`: test_change_impact and test_change_impact_cov3. Together they take about 4 seconds, so none was left out. No `-k` filter or `--deselect` was needed: no test in the set reads `docs/`, `examples/` or `app/`.

Each pytest session got a fresh temporary folder of its own, made by a small plugin that was not committed and removed again when the session ended, so a folder left by one mutant can't fail the next one (see batch 182). After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 2 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/change_impact.py` | test_change_impact, test_change_impact_cov3 (+ test_change_impact_mutation) | 165 of 165 (164 + 1), uncapped | 138 / 27 | **83.6%** | 138/164 → 159/164 | 0/1 → 1/1 | 160 / 5 | **97.0%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/change_impact.py**:
  - A module reference with a part that isn't a Python name (`transforms.bad-name`) is never read, even when the file is there.
  - An unknown entity is refused as "No entity ...", and a column the entity doesn't write is refused by name.
  - The changed table's own rows say "this table" and the others say "table".
  - A `[[clean]]` or `[[check]]` step on another column isn't one of the entity's own references to the column.
  - Tables come before Python, and Python before the model, whatever their names. A second reason of the same severity keeps the first chain.
  - A new type flags reports only on columns that copy it, not on a derived column. A drop whose direct reader is derived flags no report.
  - A new type on a column that another table's `exists_in` check looks up is a check, "both sides must keep the same type".
  - A column dropped two copies upstream still flags reports on the last copy, and a relationship on such a copy loses a side.
  - A key column dropped in the model layer loses its relationship but isn't a report column. A renamed relationship column breaks the relationship.
  - Python of a table that doesn't read the changed one isn't listed.
  - → `test_change_impact_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/change_impact.py` | 31 | `_RANK` `"breaks": 2` to 3, and `"check": 1` to 0 | Equivalent: `_RANK` is only compared and sorted on, and "breaks" stays above "check". |
| `meta/change_impact.py` | 106 | the model's sort order 2 to 3 | Equivalent: the model still sorts after tables (0) and Python (1). |
| `meta/change_impact.py` | 122 | `if new_type` negated in the retype wording | Equivalent: the wording (`verb`) is only used when the column goes (a drop or a rename), never for a new type. |
| `meta/change_impact.py` | 223 | `layer and any(...)` to `layer or any(...)` | Equivalent: an entity's layer is always a name, so with no layers the `any` is false too. With a layer, the loop that follows skips every column whose entity isn't in the last layer, so nothing is added. |

# Mutation testing, batch 332

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The savings ledger's corrections, reversals and reconciliation report (`savings/corrections.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 317 mutants, and the run ran all 317 of 317 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `savings/corrections.py`: test_savings_corrections. It takes about a second, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (30, 27 and 5).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/corrections.py` | test_savings_corrections (+ test_savings_corrections_mutation) | 317 of 317 (310 + 7), uncapped | 264 / 53 | **83.3%** | 262/310 → 306/310 | 2/7 → 7/7 | 313 / 4 | **98.7%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/corrections.py**:
  - Measured is stronger than estimated, in either order.
  - An event without an id, a run or a layer is refused by name. A scenario event is refused as a scenario, and an unknown classification by name.
  - A correction whose layer afterwards is a scenario, missing or unknown is refused. So is one whose result figure doesn't fit its classification.
  - A correction of another run's event in the same layer, or of the same run in another layer, is reported. Corrections that correct each other in a circle are reported, not a crash.
  - `correct` refuses a new attribution without its run, workload, baseline or layers, events of another workload, a layer of another run, and a layer given twice.
  - The reason says what the layer was and is now ("was 4 CU-hours, now 3 CU-hours", "was removed"). A layer gone from the new attribution is reversed like its first accrual, and the reason ends with the reason given.
  - Figures are kept to six decimal places: the chain total, each correction and its result, and each difference.
  - A layer whose figure was withdrawn gets the new figure less what is left (within the tolerance). A change of exactly the tolerance is no change, in `correct` and in `reconcile`.
  - Zero is a valid accrual and a valid regression.
  - New layers come in layer order, layers without one last, then by name. Reversals come in layer order.
  - `reconcile` names each kind of layer difference (not expected, not in the ledger, Unknown in the ledger) and counts the matched runs. A run only in the ledger keeps the ledger's workload.
  - Problems name the run and the layer.
  - → `test_savings_corrections_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/corrections.py` | 55 | `_STRENGTH` Unknown 2 to 3 | Equivalent: `_STRENGTH` is only used for membership and to find the weakest, and Unknown stays the weakest. |
| `savings/corrections.py` | 117 | `_state` of a chain with no head says removed | Equivalent: a chain has no head only when `_chains` reports a problem. `correct` and `reverse` refuse a ledger with problems, and `reconcile` replaces all three values for such a chain. |
| `savings/corrections.py` | 394 | `a[0] is not None` to `a[1] is not None`, and `b[0] is not None` to `b[1] is not None` | Equivalent: the branch before has already sent on every layer where only one side is Unknown, and the classification (`[1]`) is never None, so the test is still true exactly when both figures are known. |

# Mutation testing, batch 333

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- What one run on a PC still cost, as separate measurements: its transfer figures, the syncs that carried it, and its share of each sync (`savings/overhead.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 140 mutants, and the run ran all 140 of 140 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `savings/overhead.py`: test_savings_overhead. It takes about a second, so none was left out. No `-k` filter or `--deselect` was needed. Since the run, `main` (3424d8f33) has not changed the module, and the new tests pass there.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (20, 1 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/overhead.py` | test_savings_overhead (+ test_savings_overhead_mutation) | 140 of 140 (139 + 1), uncapped | 130 / 10 | **92.9%** | 129/139 → 139/139 | 1/1 → 1/1 | 140 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/overhead.py**:
  - A transfer figure keeps the run's workspace and capacity, and an empty one is None.
  - A sync keeps each figure's method, assumptions and period, also when the period comes as a dict.
  - A rows share keeps the sync's method and assumptions, and the commit that isn't split is still `optimize_sync_commits`.
  - A run without an execution id can't share a sync.
  - A run synced once keeps that sync's figures as they are, not a total of one.
  - → `test_savings_overhead_mutation.py`.

### Left

None.

# Mutation testing, batch 334

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Best practice checks: the registry of checkers that governance policies point at, and the built-in naming, ownership, classification and contract checkers (`governance_checks.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 222 mutants, and the run ran all 222 of 222 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `governance_checks.py`: test_model_practices, test_best_practice_checks, test_best_practice_checkers_cov2, test_best_practice_checkers and test_checks_service_cov2. Together they take about 7 seconds, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (24, 2 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `governance_checks.py` | test_model_practices, test_best_practice_checks, test_best_practice_checkers_cov2, test_best_practice_checkers, test_checks_service_cov2 (+ test_governance_checks_mutation) | 222 of 222 (217 + 5), uncapped | 180 / 42 | **81.1%** | 180/217 → 217/217 | 0/5 → 5/5 | 222 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **governance_checks.py**:
  - `register` returns the checker, and `checkers` lists it. A rule with a severity that isn't Error, Warning or Info is refused, and so is a rule missing a field, by its id and the field.
  - A rule no checker has is reported missing. A disabled rule checks and passes nothing. Settings without `enabled` run the rule.
  - A checker that fails reports Unknown for the item it was checking, by its kind and id.
  - A finding links the rule's own page, or the docs when the rule has none.
  - Naming checks only entities. A table name of exactly 256 characters is checked and one of 257 isn't, and the message shows its first 40. A pattern of exactly 300 characters is used and one of 301 isn't. A name of exactly `max_length` is allowed.
  - The entity owner rule applies only when it is on, and flags only a missing owner. A product needs a steward unless the policy says otherwise.
  - "Tables are classified" applies only in its layers (any case), and everywhere when no layers are given.
  - Contract checks only published products, and only rules that are on. Freshness allows 24 hours by default, a load exactly at the limit is fresh, a time without a zone is read as UTC, and the fix opens the first entity. Row count without a minimum allows zero, and exactly the minimum passes.
  - → `test_governance_checks_mutation.py`.

### Left

None.

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

# Mutation testing, batch 336

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`). The module has no `<` or `>` comparisons, so it has no boundary mutants.
- What a local pipeline run does with each Data Factory activity type and expression function, and the page made from it (`orchestration/coverage.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 84 mutants, and the run ran all 84 of 84 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `orchestration/coverage.py`: test_pipeline_coverage. It takes about a second, so none was left out. Two of its tests read files outside `sidecar/` (`docs/orchestration/coverage.md` and `zensical.toml`), so they fail in the tool's copy and were left out with `--deselect`: `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `test_pipeline_coverage.py::test_the_page_is_in_the_docs_site`.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (1, 4, and the shared fixture for 5).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways, and with the two left-out tests in the repository. No mutant's run timed out.

The low "before" score is the left-out page test: it is what notices a changed example value (the function then "differs" instead of "runs") or a changed page. The new tests check the same facts without reading `docs/`: which functions don't give the reference's value and why, the status counts, and the page's rows and notes. When the evaluator changes a function's status, `NOT_RUNNING` in the new tests changes with the page.

## Results

| Module | Test files run | Mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/coverage.py` | test_pipeline_coverage (two tests left out) (+ test_orchestration_coverage_mutation) | 84 of 84, uncapped | 19 / 65 | **22.6%** | 84 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/coverage.py**:
  - Every reference example but `convertFromUtc` gives the reference's value. The functions that don't run are listed by status: 10 not checked, 8 refused, 1 differs. This catches a changed example value (45 of the survivors) and a function put in the wrong status.
  - A checked row keeps its example, the value it expected and what it gave. An example that stops with an error "fails".
  - `_canonical` writes whole floats as ints, inside lists and dicts too, with sorted keys. A page cell is JSON with `|` escaped, and empty for None.
  - The page counts each status, lists each category apart, shows "Value here" only for a function that differs or fails, and prints each note once under its function.
  - → `test_orchestration_coverage_mutation.py`.

### Left

None.

# Mutation testing, batch 343

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The mastery class catalog: classes, their specializations and talent trees, the skills registry, and the checks that refuse a bad catalog (`mastery/catalog.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 125 mutants, and the run ran all 125 of 125 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `mastery/catalog.py`: test_mastery and test_mastery_catalog_cov3. Together they take about 2 seconds, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: two stopped test_mastery from loading at all, because it reads the shipped catalog when it is collected, and the third failed 1 test.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/catalog.py` | test_mastery, test_mastery_catalog_cov3 (+ test_mastery_catalog_mutation) | 125 of 125 (118 + 7), uncapped | 116 / 9 | **92.8%** | 110/118 → 117/118 | 6/7 → 7/7 | 124 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/catalog.py**:
  - `CatalogError` counts only the problems past five: five give no "(and N more)", six give "(and 1 more)".
  - A class title and an achievement name may not name a god.
  - `known_ids` names what hooks may point at, Learn drafts included.
  - `load` checks that hooks name real content unless told not to.
  - → `test_mastery_catalog_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/catalog.py` | 254 | `lru_cache(maxsize=4)` to 5 | Equivalent: the cache size changes only how many loaded catalogs are kept, never what `load` returns. |

# Mutation testing, batch 344

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The first excellence controls: workspace admins, guests, apps and direct writers, the workspace description, a running capacity, and the optimization engine's duplicate and full-refresh findings (`excellence/catalog.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 116 mutants, and the run ran all 116 of 116 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that imports `excellence/catalog.py`: test_catalog_artifacts_cov3, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops and test_excellence_models. Together they take about 5 seconds. test_excellence_ui_names also imports it, but its one test reads `app/ui/optimize-excellence.js`, which the tool's copy of `sidecar/` doesn't have, so that file was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 1 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog.py` | test_catalog_artifacts_cov3, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops, test_excellence_models (+ test_excellence_catalog_mutation) | 116 of 116 (112 + 4), uncapped | 94 / 22 | **81.0%** | 92/112 → 112/112 | 2/4 → 4/4 | 116 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

Most survivors were the words of a control's message: one or more, "is" or "are". The new tests run each check on a stand-in context that returns one evidence record, and read its message exactly.

- **excellence/catalog.py**:
  - Names are listed five at a time, then "and N more"; so are the peers that couldn't be read.
  - Admin, guest, app and direct-writer counts read as words for 0, 1 and 2; an unknown guest is "this is a guest" or "they are guests".
  - Only users with a write role count as direct writers: groups and viewers don't.
  - An active capacity names its size, or "size not given".
  - → `test_excellence_catalog_mutation.py`.

### Left

None.

Batch 343's section first said its run was at `main` 293fb38da; it was at 447143424, as this batch's was. That line is corrected.

# Mutation testing, batch 345

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`). The module has no `<` or `>` comparisons, so it has no boundary mutants.
- Extensions for the AI integrations: the checks an extension must pass, the host that turns extensions on and off, and the built-in notebook outline sample (`integrations/extensions.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 115 mutants, and the run ran all 115 of 115 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is the test files that call `integrations/extensions.py`: test_integrations_extensions, test_integrations_tool_guards and test_integrations_workspace_scope, about 16 seconds together. test_integrations_docs_examples also imports it, but it reads `docs/ai/*.md`, which the tool's copy of `sidecar/` doesn't have, so that file was left out.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (40, 1 and 14).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out. The last survivor (the outline's cell schema) got its test after the first "after" run, so it was re-checked alone with `--only` at `main` 293fb38da; the module was the same there.

## Results

| Module | Test files run | Mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/extensions.py` | test_integrations_extensions, test_integrations_tool_guards, test_integrations_workspace_scope (+ test_integrations_extensions_mutation) | 115 of 115, uncapped | 101 / 14 | **87.8%** | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/extensions.py**:
  - An extension is not built in unless it says so, and can't be changed once made. Two extensions compare without their capability builders.
  - An id that would give the core prefix is refused by name, and so is a capability that isn't a `Capability` object.
  - `describe()` lists every extension. Turning on an unknown one is refused by name.
  - A load that fails keeps a readable error: a `ValueError`'s own text, or another error's type and text.
  - The outline numbers cells from 1 and cuts headings at 200 characters. It takes a name of 1 to 200 characters, stops after 30 seconds, and tells tools a cell may carry more keys than it lists.
  - → `test_integrations_extensions_mutation.py`.

### Left

None.

# Mutation testing, batch 346

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Policy packages: a project's policies taken from the platform once, verified by their hashes, and resolved again offline (`central/policy_package.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902; the "after" run was at `main` 293fb38da, where the module is the same. The module has 147 mutants, and the run ran all 147 of 147 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_policy_package, test_policy_package_cov21 and test_policy_package_fuzz, about 16 seconds together. test_policy_promote_gate also calls the module, but it takes about a minute and uses it in one place, so it was left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run's last kills each took 9 to 11 seconds, so the last four were checked by hand: each failed real tests (32, 1, 1 and 1). Three more kills, picked at random, were checked the same way: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (44, 29 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/policy_package.py` | test_policy_package, test_policy_package_cov21, test_policy_package_fuzz (+ test_policy_package_mutation) | 147 of 147 (141 + 6), uncapped | 138 / 9 | **93.9%** | 132/141 → 141/141 | 6/6 → 6/6 | 147 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/policy_package.py**:
  - A package is format 1, and format 1 is read.
  - A package missing parts names every one. A changed package shows the first 12 characters of both hashes, or "missing".
  - An assignment's settings are kept, and missing ones are empty. A resolved policy is kept even without an assignment on the chain.
  - Resolving offline filters by the context given.
  - The package hash ignores key order and writes text as ASCII.
  - → `test_policy_package_mutation.py`.

### Left

None.

# Mutation testing, batch 347

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The notebook debugger: breakpoints, stepping, pausing a cell and evaluating expressions while it is paused (`debugger.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 174 mutants, and the run ran all 174 of 174 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_debugger, test_debugger_hardening and test_debugger_robust, about 48 seconds together. A first try without test_debugger_robust was stopped part way and started again with it, because that file covers stepping; only the full run is scored. test_debugger_eval also calls the module, but it takes about 88 seconds on its own, so it was left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills, and its last kills took their usual time. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed a real test.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. The test that a pause after Stop ends at once runs the pause on a thread with a 5 second limit, so a mutant that keeps it waiting fails the test instead of hanging the run. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `debugger.py` | test_debugger, test_debugger_hardening, test_debugger_robust (+ test_debugger_mutation) | 174 of 174 (164 + 10), uncapped | 135 / 39 | **77.6%** | 129/164 → 158/164 | 6/10 → 8/10 | 166 / 8 | **95.4%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **debugger.py**:
  - A cell takes at most 100 breakpoints. A pause shows the first 200 variables and says how many more there are, keeps the latest 20 evaluations, and leaves out dunder names and the kernel's own names.
  - An expression is at most 2000 characters, and the kernel waits 10 seconds for its answer. An evaluation re-raises when the cell is stopping.
  - A command says what it resumed. A command when the cell isn't paused is refused with "The cell isn't paused.". Stop when not paused returns False and marks the cell stopping; when paused it returns True and resumes the cell to stop it; a pause after Stop ends at once.
  - A plain frame never suspends, and a generator's frame does: at its return events a traced generator gives True, True, then False.
  - A frame's module name is used only when it is non-empty text.
  - A step that lands on a breakpoint line says "step".
  - → `test_debugger_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `debugger.py` | 125 | `_line` returns None, not itself | Equivalent: CPython keeps a frame's local trace function when it returns None on a line event, so tracing goes on the same way (checked by experiment). |
| `debugger.py` | 154 | `_stops` returns None, not False | Equivalent: its only caller tests it in an `if`, where None and False are both false. |
| `debugger.py` | 135 | `_stops_on_return` returns None, not False | Equivalent: its only caller tests it in an `if`, where None and False are both false. |
| `debugger.py` | 70 | the first `self.depth = 0` to 1 | Equivalent: every pause sets the depth before Step over and Step out read it, and Step and Continue never read it. |
| `debugger.py` | 139 | `_depth` starts counting at 1, not 0 | Equivalent: every depth moves by the same one, and the pause's depth comes from `_depth` too, so the comparisons don't change. |
| `debugger.py` | 269 | `0 <= frame.f_lasti` to `1 <=` | Equivalent: only the first instruction differs, and a code object never starts with a return instruction. |
| `debugger.py` | 269 | `0 <= frame.f_lasti` to `0 <` | Equivalent: the same, at offset 0. |
| `debugger.py` | 269 | `frame.f_lasti < len(code)` to `<=` | Equivalent: a frame's last instruction is always inside its code, so it never equals the length. |

# Mutation testing, batch 348

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The notebook kernel's own process: the JSON lines it reads and writes, the calls it makes back to the engine, and how it starts (`kernel_host.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 116 mutants, and the run ran all 116 of 116 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_kernel_host_coverage, test_kernel_process_edges and test_debugger_robust, about 22 seconds together. test_kernel_host_coverage drives the module in this process; the other two also start it as a process of its own. test_kernel_process (about 55 seconds) and test_capacity_blocked_every_route (about 22 seconds, one use of the module) also reach it, and were left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (41, 1 and 2).

Two mutants timed out, and were counted as killed: both turn the check in `Channel.answer` around, so an answer from the engine never wakes the call waiting for it.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kernel_host.py` | test_kernel_host_coverage, test_kernel_process_edges, test_debugger_robust (+ test_kernel_host_mutation) | 116 of 116 (115 + 1), uncapped | 103 / 13 (2 of the 103 timed out) | **88.8%** | 103/115 → 113/115 | 0/1 → 1/1 | 114 / 2 | **98.3%** | 2, not equivalent (see Left) |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **kernel_host.py**:
  - The longest line the process reads is 64 MB: a line of exactly 64 MB is read, and one character more is refused as "longer than 64 MB", with its length on stderr.
  - A refused request names only the first 40 characters of its op.
  - A request runs on a daemon thread named "request-" and the first 40 characters of its op.
  - → `test_kernel_host_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `kernel_host.py` | 293 | `if __name__ == "__main__"` negated | Not equivalent, and not caught only because of how the score is read. With this mutant, importing the module runs `main()`: it takes over stdin and stdout and ends the process with exit code 0 when stdin closes. Each test file imports the module, so pytest ends with exit code 0 while collecting, before any test runs, and `mutate.py` reads exit code 0 as "survived". Checked by hand: pytest printed nothing and returned 0. The new test that importing the module does not start the protocol fails on this mutant when run in a process of its own, but in the scored run pytest ends first. |
| `kernel_host.py` | 293 | `==` to `!=` in the same line | The same as the line above. |

# Mutation testing, batch 384

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The platform's two clients: the SQLite one on this PC and the one that calls UDF_Platform in Fabric, plus surrogate keys taken from the platform (`central/client.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 108 mutants, and the run ran all 108 of 108 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_central_client_cov3, test_http_session, test_sqlite_stores, test_platform_refusals, test_typed_errors, test_platform_retries, test_shared_keys and test_platform, about 70 seconds together. Many more test files reach the module through the platform's services; they were left out, and the score counts only the files given. UDF_Platform is a scripted session in every test: nothing reaches a tenant.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on a survivor, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (111, 31 and 99, errors included).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/client.py` | test_central_client_cov3, test_http_session, test_sqlite_stores, test_platform_refusals, test_typed_errors, test_platform_retries, test_shared_keys, test_platform (+ test_central_client_mutation) | 108 of 108 (106 + 2), uncapped | 101 / 7 | **93.5%** | 100/106 → 106/106 | 1/2 → 2/2 | 108 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/client.py**:
  - Business keys go to the platform in batches of 20000: 20001 keys are two calls, of 20000 and 1.
  - An answer without "inserted" counts no new keys.
  - `exists` and `ensure` say True or False, never None.
  - A key table that isn't there and can't be made is refused by name, and no key is issued.
  - Any 4xx answer with a JSON object and no errors is a refusal with the answer's text, from 400 on.
  - Every call to UDF_Platform waits up to 300 seconds.
  - → `test_central_client_mutation.py`.

### Left

None.

# Mutation testing, batch 385

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Entra ID security groups for a domain's roles: their names, and the scripts and CSV that make them and give them their Fabric roles (`central/entra.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 137 mutants, and the run ran all 137 of 137 (uncapped). It has no boundary mutants. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_entra_graph, test_entra_service_mutation and test_entra_groups, about 9 seconds together. test_csv_cells also calls the module, but it takes about 80 seconds and uses it for one CSV, so it was left out; the score counts only the files given. The module calls nothing outside: every test checks text.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on a survivor, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (11, 1 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/entra.py` | test_entra_graph, test_entra_service_mutation, test_entra_groups (+ test_entra_mutation) | 137 of 137 (137 + 0), uncapped | 114 / 23 | **83.2%** | 114/137 → 137/137 | none | 137 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/entra.py**:
  - A role is frozen.
  - Groups are named by the domain's id unless the platform's naming says name.
  - A domain that isn't there is refused by its id. Roles the platform turned off get no groups.
  - A group name is cut at 256 characters and its mail nickname at 64.
  - A row's description uses the domain's name (its id when it has none) and gives the environment only when there is one; the row keeps its Fabric domain.
  - The scripts' header names the domain from the first row (an empty plan names none), says who made it only when someone is named, and counts "1 new group" or "2 new groups" and "0 new groups".
  - Only new groups whose names a script can't carry are left for by hand.
  - The CSV gives a mail nickname only for a group to make.
  - The role script's by-hand lines name the role each group gets (Admins, Contributors, Admin, Viewer), readers get their OneLake line, and stewards get none. Its Write-Host line names the domain role given.
  - → `test_entra_mutation.py`.

### Left

None.

# Mutation testing, batch 386

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Sign-in for a Snowflake connection's SQL API calls: key-pair tokens signed on this PC, programmatic access tokens from their references, and when a token is made again (`engines/snowflake_auth.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 73 mutants, and the run ran all 73 of 73 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_snowflake_auth, test_snowflake_auth_cov20, test_snowflake_remote, test_snowflake_remote_cov20 and test_snowflake_sql_engine, about 5 seconds together. Every key is made by the test that uses it, and the one made-up token is marked for the secret scan; no test reaches Snowflake.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 1 and 1).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_auth.py` | test_snowflake_auth, test_snowflake_auth_cov20, test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine (+ test_snowflake_auth_mutation) | 73 of 73 (71 + 2), uncapped | 67 / 6 | **91.8%** | 67/71 → 71/71 | 0/2 → 2/2 | 73 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_auth.py**:
  - A key-pair token lives 59 minutes (3540 seconds).
  - A token is made again from 120 seconds before it lapses: at 880 of 1000 it is made again, at 879.999 it is not.
  - A key file of exactly 64 KB is read; one byte more is refused as too large to be a key.
  - → `test_snowflake_auth_mutation.py`.

### Left

None.

# Mutation testing, batch 387

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Snowflake's SQL API from this PC: submitting, following and cancelling a statement, its result partitions, and the connection check that never resumes a warehouse (`engines/snowflake_remote.py`).

The run was at `main` 293fb38da (2026-11-24) and the "after" run at `main` 9c7201ae4, where the module and every file in the set are the same, with `sidecar/tools/mutate.py` as it is since #2902. The module has 176 mutants, and the run ran all 176 of 176 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine, test_snowflake_auth and test_snowflake_auth_cov20, about 3 seconds together.

A first run was ended part way by a restart of the machine, and was run again from the start; only the full run is scored. While that first run went, the machine's network proxy refused connections to a Fabric host and a Snowflake account host. Other test runs shared the machine, so the run again was made with a check, used only for this run and not committed, that refuses every connection from a test to anything but this machine and writes it down. The run and the "after" run wrote down none, and neither did the set on the module as it is. Every test here answers from a fake; none reaches Snowflake.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (7, 4 and 7).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_remote.py` | test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine, test_snowflake_auth, test_snowflake_auth_cov20 (+ test_snowflake_remote_mutation) | 176 of 176 (175 + 1), uncapped | 149 / 27 | **84.7%** | 149/175 → 172/175 | 0/1 → 1/1 | 173 / 3 | **98.3%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_remote.py**:
  - A statement gives Snowflake 30 seconds, each request waits 60, and a statement isn't asynchronous unless asked.
  - A submit that gets a 503 is never sent again; a look at a running statement, a partition and a cancel are. A cancel is sent again even once Cancel was pressed.
  - A partition with a bad handle, or refused, raises.
  - A statement still running is cancelled and says how long it ran: "119 seconds", "61 seconds", and from 120 on in minutes ("2 minutes").
  - An error has no status, code or SQL state unless given. A refusal keeps 12 characters of the code and 8 of the state.
  - The hints for 408 and 429, and none for 409 or 430.
  - The connection check names the account and who signed in, says a found schema is visible, and fails a missing warehouse.
  - → `test_snowflake_remote_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/snowflake_remote.py` | 256 | `role_ok = True` to False | Equivalent: `role_ok` is read only when the profile names a role, and then it is set again first. |
| `engines/snowflake_remote.py` | 122 | the message cut `[:300]` to `[:301]` | Equivalent: `redact.one_line` just before it already cuts the message to 300 characters. |
| `engines/snowflake_remote.py` | 195 | `zip(..., strict=True)` to False | Equivalent: the lines above refuse any row whose length isn't the number of columns, so the lengths always match. |

# Mutation testing, batch 388

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Spark's to_json on this PC: JSON written by each value's type as Spark writes it, its options, and typeof's names for structs and maps (`spark_json.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 160 mutants, and the run ran all 160 of 160 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_spark_json_cov2, test_spark_complex_cov3, test_sql_boundary, test_sql_cov2, test_sql_mutation, test_spark_json, test_spark_complex_rescore and test_spark_json_values, about 20 seconds together. The first try stopped before any mutant, because one test failed in the copy of `sidecar/`: test_spark_json_values' check against Spark 3.5's captured answers reads them from outside `sidecar/`. It was deselected and the run started again; the score counts only the tests given. That check covers most of the types to_json writes, which is why so many mutants survived without it.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (12, 1 and 1).

The new tests run to_json through this PC's `spark.sql`, with the session time zone set to UTC. Before the "after" run, they were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_json.py` | test_spark_json_cov2, test_spark_complex_cov3, test_sql_boundary, test_sql_cov2, test_sql_mutation, test_spark_json, test_spark_complex_rescore, test_spark_json_values (one test deselected) (+ test_spark_json_mutation) | 160 of 160 (157 + 3), uncapped | 104 / 56 | **65.0%** | 104/157 → 150/157 | 0/3 → 2/3 | 152 / 8 | **95.0%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_json.py**:
  - to_json writes each type as Spark does: true and false, a double as 1.0E10 and NaN quoted, a date, a timestamp with Z in UTC and one without a zone, binary as base64, and nested arrays.
  - It writes maps by their key type (text, int, boolean, and a date as its days since 1970-01-01), structs with arrays and maps three deep, and an empty array of no type.
  - A decimal is written in Java's form: 1E-7 for scale 7, 1.50 for scale 2, and 1.000001 for scale 6. A decimal with no scale given has scale 0.
  - NULL struct fields are kept only when ignoreNullFields is false.
  - The dateFormat, timestampFormat and timestampNTZFormat options are read.
  - to_json of a plain value is refused by name.
  - Options are read by name in any case. One this PC doesn't read is refused by name, and options that aren't text pairs are refused. More names than values is an error.
  - typeof names decimals with and without a scale (decimal(10,0) by default), structs of two fields, arrays of structs, and maps of arrays, and names none for a type it isn't sure of.
  - → `test_spark_json_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_json.py` | 114 | a decimal with no scale given has scale 1, not 0 | Equivalent: scale 0 and 1 are both 6 or under, so either way the decimal is written plain. |
| `spark_json.py` | 116 | `scale <= 6` to `< 6` | Equivalent: at scale 6 the E-form branch checks the adjusted exponent, which is never below -6 at that scale, so it writes the plain text too. |
| `spark_json.py` | 76 | an array's element written at the same depth, not one deeper | Equivalent: the depth only names the lambda's variable. An inner lambda that reuses the outer name only reads its own, because the list it works on is read outside it. Nested arrays, maps and structs give the same JSON. |
| `spark_json.py` | 88 | a map's value written at the same depth | Equivalent, as the line above. |
| `spark_json.py` | 98 | a struct field written at the same depth | Equivalent, as the line above. |
| `spark_json.py` | 155 | the outermost value written at depth 1, not 0 | Equivalent: only the lambdas' names change. |
| `spark_json.py` | 83 | a map key written at depth - 1, and at the same depth | Equivalent (two mutants): the keys written here (int, boolean, decimal) never use the depth. |

# Mutation testing, batch 389

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Prove this notebook: the check that sorts what a notebook does into blockers, warnings and notes before a proof, whether it runs as Spark, and the cells that make the Fabric run a proof (`proof.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 92 mutants, and the run ran all 92 of 92 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_prove_spark, test_compute_check_ast, test_fabric_step, test_spark_refusals, test_proof_spark_uncapped, test_prove_spark_install, test_dbutils, test_notebook_magics and test_prove, about 50 seconds together. Other test files reach the module through the proof service; they were left out, and the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (22, 6 and 10).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `proof.py` | test_prove_spark, test_compute_check_ast, test_fabric_step, test_spark_refusals, test_proof_spark_uncapped, test_prove_spark_install, test_dbutils, test_notebook_magics, test_prove (+ test_proof_mutation) | 92 of 92 (91 + 1), uncapped | 69 / 23 | **75.0%** | 69/91 → 91/91 | 0/1 → 1/1 | 92 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **proof.py**:
  - A finding names its cell counted from 1, its line, and its code. A cell that starts with a blank line puts the finding on line 2.
  - Code of 80 characters is kept, and longer code is cut to 77 characters and "...".
  - A markdown cell is never read as code.
  - A %sql line that only reads is no blocker. One that changes tables names its own line, with lines after it.
  - A reason from compute_check without a cell is said again, except Fabric's when the missing lakehouse was already said. A reason in a cell names that cell.
  - A notebook runs as Spark when it has SQL (a %%sql cell or a %sql line, in any code cell) and not the lakehouse module. A markdown cell's SQL doesn't count.
  - A Spark proof reads schemas unless told not to.
  - → `test_proof_mutation.py`.

### Left

None.

# Mutation testing, batch 396

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's certification change check: reading a vendor's study guide page, comparing it with a pack's registry, and the draft report it gives (`academy/guide_check.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 145 mutants, and the run ran all 145 of 145 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_guide_check, about 1 second; it is the only test file that uses the module. Its page is a fixture in `sidecar/tests/`, and the new tests use a small made-up guide; nothing fetches a page.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (6, 6 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/guide_check.py` | test_academy_guide_check (+ test_academy_guide_check_mutation) | 145 of 145 (143 + 2), uncapped | 129 / 16 | **89.0%** | 129/143 → 143/143 | 0/2 → 2/2 | 145 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/guide_check.py**:
  - A page of exactly 4 MB is read, and one byte more is refused as "over 4 MB".
  - A guide without skill areas is refused by name.
  - Objectives exactly 0.6 alike are one reworded objective. The most alike are paired first, and no objective is paired twice.
  - The page's HTML is read as it shows: entities as their characters, a heading after an unclosed item, and a nested item's own words.
  - A removed skill area has no new id.
  - The report says which page and registry it compared, with "not shown" for no date. Its crosswalk marks a removed objective "(removed)".
  - A report cell is cut at 400 characters.
  - → `test_academy_guide_check_mutation.py`.

### Left

None.

# Mutation testing, batch 397

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's pipeline and orchestration exercises: the learner's pipeline run on the local runner with control flow and the exercise's mocks only, and runMultiple DAGs checked as data (`academy/pipelab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 115 mutants, and the run ran all 115 of 115 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_pipelab and test_academy_limits, about 18 seconds together. test_academy_exercises also reaches the module, but it takes about 9 minutes and uses it in three places, so it was left out; the score counts only the files given. Pipelines run in this process on the local runner, with no sandbox process to stub.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 2 and 1).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pipelab.py` | test_academy_pipelab, test_academy_limits (+ test_academy_pipelab_mutation) | 115 of 115 (110 + 5), uncapped | 87 / 28 | **75.7%** | 87/110 → 110/110 | 0/5 → 5/5 | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pipelab.py**:
  - A pipeline may hold 200 activities and a DAG 500; one more is refused with the limit named.
  - Activities it can't run are named five at most, then "and more". Missing and extra notebooks are named five at most, and extra ones are fine unless the check is exact.
  - A run that can't start names its first three reasons.
  - A definition it can't read, and a DAG that isn't valid, say why in 300 characters.
  - A DAG's concurrency must be from 1 to the exercise's cap.
  - A child pipeline is held to the same rule, and one that passes runs.
  - A pipeline runs four activities at a time, with waits that take no time and a 20 second limit.
  - A check it doesn't know fails in words. Grading without the exercise's folder reads no child pipelines.
  - Only a pipeline_outcome check has its status words checked.
  - → `test_academy_pipelab_mutation.py`.

### Left

None.

# Mutation testing, batch 398

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's Python and Delta exercises: the learner's script run in the learner process, then checks of what it left in its workspace (`academy/pylab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 75 mutants, and the run ran all 75 of 75 (uncapped). It has no boundary mutants. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_pylab, test_academy_limits and test_academy_sandbox, which start real learner processes. test_academy_databricks_content also reaches the module, but it takes about 2.5 minutes, so it was left out; the score counts only the files given. The new tests stub the learner process (`sandbox.run`) where they grade, so they start no script.

The machine was shared and loaded during the run (load average about 8), and the set took close to `mutate.py`'s 120 second limit. The run reported 61 killed, 1 survived and 13 timed out. A timed-out mutant counts as killed by the score's rule, but here that would be wrong: each of the 13 was run again by hand, without `-x` and without a limit. Twelve passed every test (they took 107 to 137 seconds), so they are counted as survivors below. One (line 40) failed 2 tests and is counted as killed. The one slow kill, at 111 seconds, was also run again by hand and failed a real test. So the honest "before" is 62 killed and 13 survived. Memory ran short on the machine during these runs; no result above came from a run that was stopped for it.

By the "after" run the unmutated set itself took longer than 120 seconds, so the "after" run used the new tests, test_academy_pylab and test_academy_limits. A mutant that fails some of the set's tests fails the whole set, so its kills hold for the full set too. It killed all 13.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 6 and 6).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pylab.py` | test_academy_pylab, test_academy_limits, test_academy_sandbox (+ test_academy_pylab_mutation) | 75 of 75 (75 + 0), uncapped | 62 / 13 (the run said 61 killed, 13 timed out, 1 survived; see above) | **82.7%** | 62/75 → 75/75 | none | 75 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pylab.py**:
  - A check it doesn't know fails in words.
  - A table file that isn't there, or is outside the workspace, fails with the workspace's words. One DuckDB can't read says why in 200 characters.
  - The table is named from the file: 40 characters at most, other characters as `_`, and `t_` before a leading digit.
  - A config check gives the validator's answer.
  - An error is named by its last line, cut at 300 characters.
  - Grading shows the last 20000 characters of the script's output and errors.
  - Only exercises without Delta may run in an AppContainer.
  - → `test_academy_pylab_mutation.py`.

### Left

None.

# Mutation testing, batch 399

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's Databricks SQL exercises: the learner's SQL translated to DuckDB by the engine's own Databricks path, then checked, including the tables an answer leaves after one run and after two (`academy/dbsql_lab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 87 mutants, and the run ran all 87 of 87 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_dbsql_lab and test_academy_limits, about 30 seconds together on the loaded machine; they are the only test files that use the module. Every check runs on an in-memory DuckDB in this process.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. No mutant's run came near the 120 second limit (the longest took 38 seconds), and none timed out. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 8 and 7).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/dbsql_lab.py` | test_academy_dbsql_lab, test_academy_limits (+ test_academy_dbsql_lab_mutation) | 87 of 87 (85 + 2), uncapped | 71 / 16 | **81.6%** | 71/85 → 85/85 | 0/2 → 2/2 | 87 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/dbsql_lab.py**:
  - An answer of exactly 100,000 characters is read, and one more is refused with the limit named.
  - SQL nested too deeply to read is refused in words. SQL it can't parse calls no functions.
  - A table is compared after one run, or after two for an idempotent check, each with its own words.
  - An answer that fails says whether it failed to run or failed the second time, with the first line of DuckDB's error.
  - Wrong columns are named (a table with none says "none"), and the number of rows that differ is counted.
  - A table is read one row past the row limit and compared at exactly the limit; the limit is made small in the test.
  - → `test_academy_dbsql_lab_mutation.py`.

### Left

None.

# Mutation testing, batch 425

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Ossie version profiles: which Ossie a file is, the rules each target adds, and a Fabric semantic view's set of files checked as Fabric reads them (`ossie_profiles.py`).

The run was at `main` 9c7201ae4 (2026-11-24). The "after" run was in the middle of merging `main` 7e383d5af1, where the module, `sidecar/tools/mutate.py` and every file in the set are the same; only the docs had changed (#3270 split this log into a page per batch). `mutate.py` is as it is since #2902. The module has 268 mutants, and the run ran all 268 of 268 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_ossie_validate_mutation, test_ossie_profiles, test_semantic_view and test_ossie_fuzz, about 35 seconds together. test_network_hosts also imports the module, only to list the hosts it names, so it was left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. No mutant's run came near the 120 second limit (the longest took 49 seconds), and none timed out. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: one (`_rule` returning None) stops the module importing, so the set fails while collecting, and the others failed 108 and 40 tests.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ossie_profiles.py` | test_ossie_validate_mutation, test_ossie_profiles, test_semantic_view, test_ossie_fuzz (+ test_ossie_profiles_mutation) | 268 of 268 (259 + 9), uncapped | 227 / 41 | **84.7%** | 221/259 → 254/259 | 6/9 → 8/9 | 262 / 6 | **97.8%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ossie_profiles.py**:
  - A document that isn't an object, and a 0.2 or 0.3 file in the older semantic_model layout, are refused in words, with where the diagnostic goes.
  - A profile that isn't there is refused by name.
  - Rules are chosen by profile and by scope.
  - Which profiles the converter takes.
  - A result names the profile's schema version, or the document's own version when the profile has no schema.
  - More than 200 problems are counted in one "more" line.
  - A file that isn't text or bytes is refused by name.
  - Ignored files, and files that can't be read, are named five at most, then "…", and each unreadable file is Invalid.
  - Past twenty files, the rest are counted as not checked.
  - A file missing its name or version is told what to add.
  - A problem with the view's name is shown in the first file.
  - YAML over the size limit is Invalid, not "Not checked".
  - A set of files names its own rules.
  - A summary counts "1 other place" and "2 other places".
  - Values nested too deeply to compare are not the same.
  - → `test_ossie_profiles_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `ossie_profiles.py` | 312 | `total` starts at 1, not 0 | Equivalent: the starting value is read only when the schema check ran, and that sets it first. |
| `ossie_profiles.py` | 390 | the merged version's file is the first file's contents, not its name | Equivalent: the merged view's version is always set to 0.2.0.dev0, so no problem is ever found at `$.version` to look its file up. |
| `ossie_profiles.py` | 419 | `zip(..., strict=True)` to False | Equivalent: each merged item's origin is added as the item is, so the two lists always have the same length. |
| `ossie_profiles.py` | 443 | `index < len(origins)` to `<=` | Equivalent: a merged problem's index comes from the merged list, which is as long as its origins, so the index is always below it. |
| `ossie_profiles.py` | 519 | `merge_notes[-1]` to `merge_notes[0]` | Equivalent: the list has exactly one note when it is read. |
| `ossie_profiles.py` | 543 | `stopped or more` to `stopped and more` | Equivalent: when either is set, the merged result has a "more" line at `$`, which lands in the set's own problems, so the set is invalid either way. |

# Mutation testing, batch 426

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Fabric Semantic View files: a semantic model written as the YAML of a Semantic View folder, with the report of what is left out and why (`semantic_view.py`).

Both runs were at `main` e49cddbebe (2026-11-24). `mutate.py` is as it is since #2902. The module has 284 mutants, and the run ran all 284 of 284 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_semantic_view, test_fail_closed_actions, test_semantic_parity, test_semantic_parity_l2, test_model_ossie and test_model_ossie_service_edges, about 45 seconds together. test_semantic_parity_l3, test_semantic_parity_l3_cov32, test_model_ossie_edges, test_model_ossie_mutation and test_model_ossie_service_cov3 also reach the module, through `semantic_parity` or the model service, and were left out to keep each mutant's run well inside the 120 second limit on a busy machine; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. No mutant's run came near the 120 second limit (the longest took 45 seconds), and none timed out. The run ends on four kills after a run of survivors: the last two are the module's two boundary mutants, each killed by an existing test of the file-count limit and of the view's twentieth file. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: `folder_name` returning None failed 54, the view file's text returning None failed 64, and a stripped construct named by the wrong rule failed 4.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semantic_view.py` | test_semantic_view, test_fail_closed_actions, test_semantic_parity, test_semantic_parity_l2, test_model_ossie, test_model_ossie_service_edges (+ test_semantic_view_mutation) | 284 of 284 (282 + 2), uncapped | 211 / 73 | **74.3%** | 209/282 → 279/282 | 2/2 → 2/2 | 281 / 3 | **98.9%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **semantic_view.py**:
  - An Ossie document with no name, no datasets or datasets that aren't a list is refused by the emitter itself.
  - Ossie JSON text is read with the Ossie size limit, and bad or too deeply nested JSON is refused in words.
  - A view of more files than Fabric takes is refused, and twenty datasets split by dataset are merged two a file into ten files, with the exact message.
  - `merged` refuses a file that isn't one object, skips files that aren't `.yaml`, and takes keys other than datasets, relationships and metrics from the first file only.
  - A clean document excludes nothing, every row says whether it is excluded, and the report says it is a preview.
  - Each stripped construct has its exact path (`$.datasets[0].fields[1]`), kind and reason, with the detail only when there is one; a field with no name is named by position, with no key.
  - A dataset's notes say "fields left out" only when it has fields; a DAX-only metric has no "other dialects" note.
  - A dataset, dataset metric, model metric or relationship that can't be written is named by position (`#1`, `#2`, …) or as `<unnamed>`, and is excluded; a relationship's reason counts its columns, with `?` for a list that isn't one.
  - A metric of a query-source dataset is excluded.
  - What was lost converting a model to Ossie is a row of the view.
  - A converter that can't read the document says why on each SQL metric, and a metric it doesn't return gets the generic reason.
  - The converter's own reason for a metric it can't translate is kept, and a DAX metric is never taken as translated, whatever else it is annotated with.
  - Excluded measures, relationships and tables in the POWER_BI extension are listed one by one with their own keys; a calculation group's reason says a view has none; an empty extension list names no vendor.
  - A metric keeps only its first DAX expression; a model's DAX measures keep their SQL translation, from TMSL and from TMDL.
  - A view file is block YAML in document order, with Unicode as written.
  - An Ossie document with a key ending `.tmdl` is still read as Ossie.
  - The files summary counts only metrics with a DAX expression.
  - → `test_semantic_view_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `semantic_view.py` | 124 | "needs a letter or digit" refusal removed | Equivalent: a name that reaches it doesn't start or end with a space or a dot, so `name.strip(". ")` is never empty there. |
| `semantic_view.py` | 521 | `lru_cache(maxsize=1)` to `maxsize=0` | Equivalent: with no cache the dumper class is made again on each call, and it dumps the same text. |
| `semantic_view.py` | 560 | `_BREAKS[:3]` to `_BREAKS[:4]` (also refuse a raw `\r`) | Equivalent: every string holding `\r` is written in double quotes, where it is the escape `\r`, so the text never holds a raw one. |
