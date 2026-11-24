# Mutation testing, batch 111

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence controls: what a control's definition must have, the registry, and running one control on one subject (`excellence/controls.py`).
- The PySpark DataFrame API list: one example run as a notebook cell, a method's status and note, rows compared in any order, and the docs page (`spark_api_coverage.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used.

Four tests in `test_spark_api_coverage.py` were left out with `--deselect`: three read `docs/` (`test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list`) and one reads the repository's `README.md` (`test_the_readme_counts_match_spark_api_coverage`). That is why so many `spark_api_coverage.py` mutants survived before: nothing left in the run called `markdown()`, and the real examples rarely reach the notes or the error paths. The new tests check those on a stand-in kernel.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. One `spark_api_coverage.py` survivor of the "after" run (line 113) was killed by a test added after it; the figures below take that test's own `--only` run for that mutant. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/controls.py` | test_excellence, test_excellence_catalog, test_excellence_scoring_mutation, test_excellence_findings_mutation (+ test_excellence_controls_mutation) | 113 of 113 (112 + 1) | 107 / 6 | **94.7%** | 106/112 → 112/112 | 1/1 → 1/1 | 113 / 0 | **100%** | none |
| `spark_api_coverage.py` | test_spark_api_coverage, test_spark_api_fails_cleared (+ test_spark_api_coverage_mutation) | 100 of 100 (97 + 3) | 41 / 59 | **41.0%** | 41/97 → 96/97 | 0/3 → 3/3 | 99 / 1 | **99.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/controls.py**:
  - A context refuses a kind it wasn't given, with a message saying so.
  - A failure keeps its detail, and the detail reaches the outcome.
  - A control can't be changed once made.
  - A reference needs a title as well as an https url.
  - Registering a control gives it back.
  - → `test_excellence_controls_mutation.py`.
- **spark_api_coverage.py**:
  - Each refusal phrase counts on its own, in any case.
  - An expression is run as the value and kept; a statement, and source Python can't read, run as they are. Text and stream outputs are printed, others aren't.
  - An error is its name and first line, cut to 160 characters; one without text is the status. A value this PC can't collect gives its error's first line.
  - Only one expression is an expression.
  - An example Spark stops on must stop here too; a stop here is refused or fails, with the source and the error.
  - Rows without a set order compare in any order, and ordered rows don't. A different value names both, each cut to 70 characters.
  - A result without printed text is kept. Each table's rows are sorted, its header stays, and a stray border after the tables is left alone.
  - A source is shown on one line, cut after 60 characters.
  - The page counts each status, lists each class that has methods, and escapes `|` and line breaks in a note.
  - The page is written to the repository's `docs/`, and importing the module writes nothing.
  - → `test_spark_api_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_api_coverage.py` | 144 | `return False` to `None` for source Python can't read | Equivalent: its only caller reads it as a condition, and `None` and `False` are both false. |

