# Mutation testing, batch 420

One module not scored before, run with `--kinds all`:
- `notebookutils` (and `mssparkutils`) in a PySpark exercise: notebook.run, exit and runMultiple on the exercise's own notebooks, the fs calls bound to its Files/ folder, runtime.context, and the Fabric-only parts that say they aren't there (`academy/notebookutils_lab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_pyspark_lab was left out of the test set because each of its runs takes about 93 seconds; that can only lower the "before" score. The module has 143 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_pyspark_streaming, test_trees, test_state_writes and test_file_replace.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The notebooks the new tests run are their own few lines (an assignment, an exit, a raise), run as notebook.run runs them; no learner script runs. Four survivors were left by the first "after" run. They got tests, and those four were run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/notebookutils_lab.py` | test_academy_pyspark_streaming, test_trees, test_state_writes, test_file_replace (+ test_academy_notebookutils_lab_mutation_b) | 143 of 143 (142 + 1) | 62 / 81 | **43.4%** | 62/142 → 140/142 | 0/1 → 1/1 | 141 / 2 | **98.6%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/notebookutils_lab.py**:
  - Cells split at Fabric's markers, and the parameters cell is flagged. An empty notebook has no cells.
  - A notebook is named with or without `.py`, up to 63 characters, and must be there.
  - run returns the exit value as a string, or None. It refuses a workspace and argument names that aren't Python names. Arguments are set after the parameters cell, or before the first cell when there is none.
  - A notebook that runs itself stops at ten, and the running list is cleared afterwards. An error names the notebook's own file and line.
  - runtime.context names the innermost running notebook, reads fresh through every dict method, and json.dumps prints it.
  - runMultiple runs each notebook, and validateDAG checks the DAG. Both help calls print what is there.
  - The Fabric-only parts raise NotImplementedError in words, and a dunder lookup is a plain AttributeError.
  - Paths outside Files/ are refused, the lakehouse prefixes and backslashes name the same file, and a link on the way is refused.
  - ls lists a folder or one file with Fabric's fields, and the modified time in milliseconds. head reads 64 KiB by default. put, append, mkdirs, rm, cp and mv keep their overwrite, create and recurse rules, make missing folders and reuse existing ones. A link inside a copied folder is copied as a link.
  - install gives `notebookutils` and `mssparkutils`, and exit carries its value.
  - → `test_academy_notebookutils_lab_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/notebookutils_lab.py` | 139 | `timeout_seconds=90` to `91` in run | Equivalent: run never reads timeout_seconds. The exercise's own time limit applies. |
| `academy/notebookutils_lab.py` | 177 | `self.run(path, 0, args)` to `1` in runMultiple | Equivalent, for the same reason. |
