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
