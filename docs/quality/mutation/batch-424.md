# Mutation testing, batch 424

One module not scored before, run with `--kinds all`:
- Database project exercises: the learner's change set to a warehouse project, read as text, built in an in-memory DuckDB with no file or network access, and checked for one object per file in Fabric's Git layout, a build, the objects and columns asked for, a safe deploy and a query's rows (`academy/sqlprojlab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_exercises was left out of the test set because each of its runs takes about 875 seconds; that can only lower the "before" score. The module has 226 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_sqlprojlab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests build only the test's own project files with the lab's own build.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/sqlprojlab.py` | test_academy_sqlprojlab (+ test_academy_sqlprojlab_mutation_b) | 150 of 226 (146 + 4) | 112 / 38 | **74.7%** | 112/146 → 142/146 | 0/4 → 4/4 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/sqlprojlab.py**:
  - A change set of exactly 1 MiB is read and one byte more is refused. A project may hold 500 files but not 501. A path may be as deep as the project layout allows and no deeper. A query's rows stop at 10,000.
  - A line before any file marker is named from line 1. A file repeated after another file is still refused.
  - A table the T-SQL translator can't read fails its build in words, instead of the build crashing.
  - The query's column names are read. objects_present must be given a list.
  - project_builds counts the other problems ("(1 more)"), and adds nothing for one problem.
  - objects_present names one missing column or several, and says when a view doesn't build so its columns can't be read.
  - deploy_safe names what the rebuild would change.
  - result_matches says when the reference doesn't build, gives the query's own first error line, names the columns it expected, and compares order when asked to.
  - An unknown check kind fails in words. The reference project is built only for result_matches checks.
  - Run shows at most its limit of rows and says when there are more, and lists only the problems that don't belong to a file.
  - → `test_academy_sqlprojlab_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/sqlprojlab.py` | 206 | `o["kind"] == "schema"` to `!=` in the set of schemas to create | Equivalent in effect. A schema object's own `schema` is its name, so every schema the project needs is still created; the mutant only adds empty schemas named after the project's other objects, which nothing reads. |
| `academy/sqlprojlab.py` | 208 | `chr(34)` to `chr(35)`, and `chr(34) * 2` to `* 3`, when quoting a schema name | Equivalent: `_segment_ok` refuses a name with a double quote, so the replacement never has anything to replace. |
| `academy/sqlprojlab.py` | 316 | `fetchmany(MAX_ROWS + 1)` to `+ 0` | Equivalent: only `rows[:MAX_ROWS]` is used, so the extra row is never read. |
