# Mutation testing, batch 114

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- The project files a release keeps in an environment, and restoring them for a run or a rollback (`meta/snapshot.py`).
- The workflow prompts the MCP server offers AI tools: their arguments and how they render (`integrations/prompts.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used.

Four tests in `test_integrations_prompts.py` read `docs/` or `deploy/`, so they were left out with `--deselect` (`test_workflows_page_matches_the_prompts`, `test_connect_page_carries_every_setup_from_the_app`, `test_agent_instructions_name_every_prompt_and_only_real_tools`, `test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page`). Of `test_integrations_conformance.py`, only `test_resources_templates_and_prompts_are_listed_and_readable` was run, the one test there that reads the prompts: the whole file takes about 47 s, and another of its tests reads `docs/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. In the "after" run of `meta/snapshot.py` the new test file was given first. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/snapshot.py` | test_rollback (+ test_meta_snapshot_mutation) | 39 of 39 (39 + 0) | 30 / 9 | **76.9%** | 30/39 → 37/39 | none | 37 / 2 | **94.9%** | 1 equivalent, 1 not killed |
| `integrations/prompts.py` | test_integrations_prompts, one test of test_integrations_conformance (+ test_integrations_prompts_mutation) | 22 of 22 (22 + 0) | 17 / 5 | **77.3%** | 17/22 → 22/22 | none | 22 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/snapshot.py**:
  - Saving a release says how many files it kept, and 0 the second time.
  - A release the environment doesn't keep is a `LookupError`.
  - A path outside the project, and files that don't add up to the version, are refused, and no half-written folder is left.
  - A folder that doesn't load isn't the release.
  - → `test_meta_snapshot_mutation.py`.
- **integrations/prompts.py**:
  - Each workflow needs every argument it names, and one with none given names them all.
  - A prompt can't be changed once made.
  - → `test_integrations_prompts_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/snapshot.py` | 105 | `_matches`'s `return False` to `None` | Equivalent: both callers read it as a condition, and `None` and `False` are both false. |
| `meta/snapshot.py` | 91 | `ignore_errors=True` to `False` when a failed restore clears its temporary folder | Not killed: the folder is made before the `try` and moved only by its last step, so it is there to delete; the two differ only when the delete itself fails, which a test here can't make happen on Linux without patching `shutil`. |

