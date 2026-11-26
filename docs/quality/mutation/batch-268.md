# Mutation testing, batch 268

One module that had no score before, run with `--kinds all`:
- The support bundle: the last lines of each log, read from the end of the file, what about.json says about versions, settings and the policy file, and the zip written under a new name each time (`support_bundle.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 102 mutants, so all were run; none is a boundary mutant. `test_support_bundle_save.py::test_the_bundle_and_the_save_dialog_say_what_is_kept_in_the_same_words` reads `app/ui/settings.js`, so it was left out with `--deselect`. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `support_bundle.py` | test_support_bundle, test_support_bundle_edges, test_support_bundle_save (one test deselected), test_support_bundle_secret_scan, test_secret_leaks (+ test_support_bundle_mutation) | 102 of 102 (102 + 0) | 80 / 22 | **78.4%** | 80/102 → 99/102 | none | 99 / 3 | **97.1%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **support_bundle.py**:
  - A log keeps exactly its last 2,000 lines.
  - Only the last 4 MiB of a log are read. When the read starts on a newline, only that empty piece is dropped.
  - A UTF-16 log is known by its byte order mark. Its tail starts on a whole code unit at every size, and a whole UTF-16 file is read from its first line.
  - A private key whose BEGIN line is the log's very first character is kept whole, not taken as cut off.
  - `path_facts` gives the folder's length, and says whether it is plain ASCII, has a space or is on a share.
  - about.json says "Microsoft's Fabric sign-in" or "own app registration", lists the platforms set up (or none when the value isn't a mapping), and gives Python's version.
  - about.json's folder facts and policy record are for the folder given, not the default one.
  - A bundle tries 99 names in one second before it gives up.
  - → `test_support_bundle_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `support_bundle.py` | 48 | `textwrap.wrap(text, 100)` to `101` | Equivalent: the only text wrapped is `KEPT`, which wraps the same way at 100 and 101. |
| `support_bundle.py` | 193 | about.json's `indent=2` to `3` | Equivalent: only the layout of the JSON changes. |
| `support_bundle.py` | 258 | `path.unlink(missing_ok=True)` to `False` | Equivalent: the zip was just created with `open("xb")`, so it is there to remove. |
