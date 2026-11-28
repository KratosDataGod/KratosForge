# Mutation testing, batch 507

A re-score of one module, run with `--kinds all`:
- Platform skins: the size of a skin file, the rail and its labels, the colors and bar a skin may set, their contrast, and how a skin is exported (`skins.py`). It was last scored in batch 24 at 90.7% before and 99.3% after (point mutants only), and its boundary mutants in batch 164 at 75.0% before and 87.5% after (`--kinds boundary`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Tests that read `app/` or `docs/` fail in the mutator's copy of `sidecar/`, so they were left out with `--deselect`: six in test_skins (`test_every_token_a_skin_sets_is_on_styles_root`, `test_the_kratosforge_skin_is_todays_look`, `test_kratosforge_dark_is_the_styles_dark_block`, `test_the_notebook_commands_a_skin_can_place_are_the_apps_own`, `test_the_engine_knows_each_notebook_commands_own_label`, `test_the_apps_default_look_is_the_first_choice_of_each_part`) and three in test_parity (`test_docs_page_matches_the_code` and both cases of `test_a_sql_rows_local_claim_follows_its_function_list`). The module has 181 mutants, so a sample of 150 was run (143 point + 7 boundary).

The test set was the files of batches 24 and 164, with test_skins_cov2, which also tests the module: test_skins, test_parity, test_skins_mutation, test_skins_boundary and test_skins_cov2.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `skins.py` | test_skins, test_parity, test_skins_mutation, test_skins_boundary, test_skins_cov2 | 150 of 181 (143 + 7) | 149 / 1 | **99.3%** | 142/143 → 142/143 | 7/7 → 7/7 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

The only survivor is the one batch 24 already explained as equivalent, and the existing tests killed everything else. No new tests needed.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `skins.py` | 176 | `return False` dropped in `_command_id` | Equivalent, as batch 24 said: the only caller that reads the answer tests it with `not`, and `None` is falsy too. |
