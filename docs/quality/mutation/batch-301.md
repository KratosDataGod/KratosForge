# Mutation testing, batch 301

This batch re-scores one module with `--kinds all`. The module was last scored for point mutants in batch 24 (99.3%) and for boundary mutants in batch 164 (87.5% of 8, with the one survivor shown to be equivalent).
- Platform skins: what a skin file may hold, the contrast of its colors, the rail and its labels, and which notebook commands a skin may place or rename (`skins.py`).

The run was at `main` ea988e99e (2026-11-21), with `sidecar/tools/mutate.py` as it stands since #2902, using the method of earlier batches: sample cap 150, seed 7, `--kinds all`. The test paths given to the tool were relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest run used a fresh temporary folder that was removed when it ended. `skins.py` has 181 mutants, and 150 were sampled.

Before the run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. Six tests in `test_skins.py` and three in `test_parity.py` read `app/` or `docs/`, so they were deselected, as in batches 24 and 164. `test_parity_mutation.py` was left out because it replaces the built-in skins with an empty list and so does not check this module.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Killed / survived | Score | Boundary | Left |
| --- | --- | --- | --- | --- | --- | --- |
| `skins.py` | test_skins, test_skins_mutation, test_skins_boundary, test_skins_cov2, test_parity | 150 of 181 (143 + 7) | 149 / 1 | **99.3%** | 7/7 | 1 equivalent |

The existing tests already kill every mutant except one, so this batch adds no tests. No defect was found.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `skins.py` | 176 | `return False` dropped in `_command_id` | Equivalent, as in batch 24: the only caller that reads the answer tests it with `not`, and `None` is falsy too. |
