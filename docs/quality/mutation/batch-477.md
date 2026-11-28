# Mutation testing, batch 477

One module not scored before, run with `--kinds all`:
- The local Databricks Asset Bundle check (Preview): databricks.yml and the files it includes are read with yaml, never run, and every problem is reported with the field's path, its file and its line. It checks the keys, types, targets, variables, task types, local paths and references the configuration reference documents, keeps includes inside the bundle folder, and refuses YAML aliases, files over the size limit and nesting past the depth limit (`engines/databricks_bundle_check.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. One test reads `docs/engines.md`, so it was deselected (`test_docs_example_output_matches_a_real_run`); that can only lower the "before" score. The module has 410 mutants, so a sample of 150 was run.

The test set was test_databricks_bundle_check, without that one test.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_bundle_check.py` | test_databricks_bundle_check, one test deselected (+ test_engines_databricks_bundle_check_mutation_b) | 150 of 410 (146 + 4) | 128 / 22 | **85.3%** | 127/146 → 144/146 | 1/4 → 2/4 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_bundle_check.py**:
  - YAML nested exactly 200 levels deep is read, and one level more is refused. Many keys side by side aren't nesting.
  - A key given twice is named by its own name, not its whole path. A blank bundle.name is an error.
  - An integer where a list belongs is called "an integer". A setting that isn't a mapping is named by the last part of its path.
  - A depends_on entry that isn't a mapping is reported at the entry, and one without a task_key at its task_key.
  - A reference to a missing resource names its type and its name.
  - Findings in one file come in line order.
  - Files an include matches outside the folder are counted in the finding, with "file ... isn't" for one and "files ... aren't" for more.
  - A path that can't be resolved isn't inside the folder. `**/a.yml` matches only files named a.yml.
  - A path on another drive is shown whole. A line is found from the nearest parent path that has one.
  - → `test_engines_databricks_bundle_check_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/databricks_bundle_check.py` | 657 | `mapping_keys` returns None instead of False | Equivalent: every caller tests `not ...` or ignores the result. |
| `engines/databricks_bundle_check.py` | 678 | the sort key's `f.line or 0` to `f.line or 1` | Equivalent: a finding with no line is a whole-file one (unreadable, too big, bad YAML), which stops that file, so it is never sorted against another finding of its file. |
| `engines/databricks_bundle_check.py` | 757 | the walk's `len(found) >= limit` to `>` | Equivalent: it makes one more call at most, and the list is cut to `limit` when the walk returns. |
| `engines/databricks_bundle_check.py` | 822 | `cut > 0` to `cut >= 0` in `_line_of` | Equivalent: at 0, `path[:0]` is "" too. |
