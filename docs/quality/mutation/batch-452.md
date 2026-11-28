# Mutation testing, batch 452

One module not scored before, run with `--kinds all`:
- Export to Ossie and Import from Ossie on the Semantic model tab: a model's definition read from its workspace or the project's folder and converted on this PC, Import previewed and written only when confirmed, the model's TMDL and a Fabric Semantic View saved in the project's folder, with caps on what is read and checks on where it is written (`model_ossie_service.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 429 mutants, so a sample of 150 was run. Since a73ebb71, `main` has changed this module once (ae63977be: `_ossie_path` refuses reserved names and links). The new tests also pass against that version.

The first run's test set was test_model_ossie_service_cov3, test_model_ossie, test_ossie_schema, test_semantic_view and test_model_folder_fuzz, and it left 43 survivors. Four more test files call this module, so those 43 were run again with them added: test_model_ossie_service_edges, test_definition_decode_fuzz, test_ossie_fidelity and test_ossie_profiles. They killed 11 of them. The "before" column counts both runs.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Six survivors were left by the first "after" run that weren't equivalent. They got tests and were run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `model_ossie_service.py` | test_model_ossie_service_cov3, test_model_ossie, test_ossie_schema, test_semantic_view, test_model_folder_fuzz, test_model_ossie_service_edges, test_definition_decode_fuzz, test_ossie_fidelity, test_ossie_profiles (+ test_model_ossie_service_mutation_b) | 150 of 429 (144 + 6) | 118 / 32 | **78.7%** | 118/144 → 142/144 | 0/6 → 6/6 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **model_ossie_service.py**:
  - A model's counts include its relationships and roles. Exactly three unreadable lines are named without "and more".
  - A file summary of a file that isn't one YAML object says it's unreadable. Only metrics with DAX text count as DAX metrics.
  - TMDL files are found eight folders down and no further. More than 20,000 TMDL files are refused.
  - A TMDL folder or a model.bim of exactly the cap is read, and one byte more isn't. An Ossie file over the cap is refused when it's read.
  - Export of a folder runs the round-trip check. Without a target, Test reads from Fabric and Dev doesn't.
  - A lakehouse in the environment needs no placeholder row, and one that isn't says it reads placeholder ids. A profile the converter can't read is previewed as refused.
  - Import answers the id Fabric gave, or the model's id when Fabric gives none.
  - Save TMDL: a model that isn't there is a LookupError. The first empty file is named. A part path outside the folder is refused, and so is a target outside the model's folder, before anything is written.
  - To TMDL writes `definition.pbism` with two-space indents. Another culture defined outside `cultures/` is not refused.
  - Semantic views: text over the cap is refused. A stale file that stopped being a file isn't removed. A folder of exactly the entry cap is read and one entry more isn't, and eight folders down is read and nine isn't. A target outside the view's folder is refused.
  - A view's YAML of exactly Fabric's cap is read, and more is refused before any file is read.
  - 200 saved views are listed, and more aren't read.
  - → `test_model_ossie_service_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `model_ossie_service.py` | 589 | `ensure_ascii=False` to `True` in the JSON that the Ossie-to-TMDL write's token is a digest of | Equivalent: the preview and the confirmed call build the digest the same way, and the token is only compared with that. Nothing else reads it. |
| `model_ossie_service.py` | 675 | `ensure_ascii=False` to `True` in the JSON that the semantic view's token is a digest of | Equivalent, as above. |
