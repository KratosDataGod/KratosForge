# Mutation testing, batch 523

This batch scores one module for the first time: `model_ossie_redact.py`. It ran with `--kinds all`, so the score includes the boundary mutants (`<` to `<=`, `>` to `>=`) along with the earlier kinds, and the table shows each part separately.
- The module controls what the AI tools get back from Export to Ossie and Import preview. Each security role's filter expressions and members are replaced by a length and a keyed fingerprint. This covers the fidelity report, the YAML, and any other text in the answer (`model_ossie_redact.py`).

The run was at `main` be5ef815c (2026-11-28), using `sidecar/tools/mutate.py` with the same method as earlier batches (`--cap 150 --seed 7 --kinds all`). The module has fewer than 150 mutants, so every one ran. Test files were passed to the tool as paths relative to `sidecar/`. The tests passed once on the unmutated copy first. Each pytest session got a new temporary folder that was removed when the session ended, and no folder 40 or more levels deep was left behind. No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected. No mutant's run timed out.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own, and against the module as `ast.unparse` writes it, so the run can't score a false 100%. The new tests also pass on DuckDB 2.0.0.

One survivor of the "after" run, on line 69, was then killed by a test added after that run. The mutant drops the full repr form of a value longer than 80 characters. It was run again on its own with `--only`. Adding a test can't revive a killed mutant, so that re-run gives the final count.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `model_ossie_redact.py` | test_integrations_ossie_redaction, test_integrations_services_mutation, test_integrations_workspace_scope, test_model_ossie (+ test_model_ossie_redact_mut523) | 102 of 102 (100 + 2) | 75 / 27 | **73.5%** | 75/100 → 99/100 | 0/2 → 2/2 | 101 / 1 | **99.0%** | 1 not reached |

No bugs were found.

### What the survivors were, and the tests that now kill them

- The shortest value withheld is six characters, and a six-character member id is withheld where it's quoted. A shorter value is left alone.
- A value is withheld in every form a message may quote it in: as written, as repr writes it, as JSON writes it, and cut at 80 characters both plain and as repr. Each form is tested with a value that only that form matches.
- The longest form of a value is replaced first.
- Numbers, flags and nulls in an answer are kept as they are.
- On a role, only the converter's row about the roles collection is kept, and every other row is withheld. A row about a role inside another object's evidence is withheld, and that object's other rows are kept.
- The metadata changes list marks an object's notes as informational.
- Export's YAML changes only the POWER_BI section, keeps its text as written (non-ASCII isn't escaped), keeps a table permission that has no filter, and leaves the caller's document unchanged.
- An Import preview with no fidelity report still withholds the rest of the answer and adds the note.
- These tests are in `test_model_ossie_redact_mut523.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `model_ossie_redact.py` | 141 | `return m` dropped for a member entry that isn't an object | Not reached: a role's members are the model's own member objects, as Export to Ossie writes them, so every entry here is an object. |
