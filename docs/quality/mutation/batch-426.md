# Mutation testing, batch 426

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Fabric Semantic View files: a semantic model written as the YAML of a Semantic View folder, with the report of what is left out and why (`semantic_view.py`).

Both runs were at `main` e49cddbebe (2026-11-24). `mutate.py` is as it is since #2902. The module has 284 mutants, and the run ran all 284 of 284 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_semantic_view, test_fail_closed_actions, test_semantic_parity, test_semantic_parity_l2, test_model_ossie and test_model_ossie_service_edges, about 45 seconds together. test_semantic_parity_l3, test_semantic_parity_l3_cov32, test_model_ossie_edges, test_model_ossie_mutation and test_model_ossie_service_cov3 also reach the module, through `semantic_parity` or the model service, and were left out to keep each mutant's run well inside the 120 second limit on a busy machine; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. No mutant's run came near the 120 second limit (the longest took 45 seconds), and none timed out. The run ends on four kills after a run of survivors: the last two are the module's two boundary mutants, each killed by an existing test of the file-count limit and of the view's twentieth file. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: `folder_name` returning None failed 54, the view file's text returning None failed 64, and a stripped construct named by the wrong rule failed 4.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semantic_view.py` | test_semantic_view, test_fail_closed_actions, test_semantic_parity, test_semantic_parity_l2, test_model_ossie, test_model_ossie_service_edges (+ test_semantic_view_mutation) | 284 of 284 (282 + 2), uncapped | 211 / 73 | **74.3%** | 209/282 → 279/282 | 2/2 → 2/2 | 281 / 3 | **98.9%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **semantic_view.py**:
  - An Ossie document with no name, no datasets or datasets that aren't a list is refused by the emitter itself.
  - Ossie JSON text is read with the Ossie size limit, and bad or too deeply nested JSON is refused in words.
  - A view of more files than Fabric takes is refused, and twenty datasets split by dataset are merged two a file into ten files, with the exact message.
  - `merged` refuses a file that isn't one object, skips files that aren't `.yaml`, and takes keys other than datasets, relationships and metrics from the first file only.
  - A clean document excludes nothing, every row says whether it is excluded, and the report says it is a preview.
  - Each stripped construct has its exact path (`$.datasets[0].fields[1]`), kind and reason, with the detail only when there is one; a field with no name is named by position, with no key.
  - A dataset's notes say "fields left out" only when it has fields; a DAX-only metric has no "other dialects" note.
  - A dataset, dataset metric, model metric or relationship that can't be written is named by position (`#1`, `#2`, …) or as `<unnamed>`, and is excluded; a relationship's reason counts its columns, with `?` for a list that isn't one.
  - A metric of a query-source dataset is excluded.
  - What was lost converting a model to Ossie is a row of the view.
  - A converter that can't read the document says why on each SQL metric, and a metric it doesn't return gets the generic reason.
  - The converter's own reason for a metric it can't translate is kept, and a DAX metric is never taken as translated, whatever else it is annotated with.
  - Excluded measures, relationships and tables in the POWER_BI extension are listed one by one with their own keys; a calculation group's reason says a view has none; an empty extension list names no vendor.
  - A metric keeps only its first DAX expression; a model's DAX measures keep their SQL translation, from TMSL and from TMDL.
  - A view file is block YAML in document order, with Unicode as written.
  - An Ossie document with a key ending `.tmdl` is still read as Ossie.
  - The files summary counts only metrics with a DAX expression.
  - → `test_semantic_view_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `semantic_view.py` | 124 | "needs a letter or digit" refusal removed | Equivalent: a name that reaches it doesn't start or end with a space or a dot, so `name.strip(". ")` is never empty there. |
| `semantic_view.py` | 521 | `lru_cache(maxsize=1)` to `maxsize=0` | Equivalent: with no cache the dumper class is made again on each call, and it dumps the same text. |
| `semantic_view.py` | 560 | `_BREAKS[:3]` to `_BREAKS[:4]` (also refuse a raw `\r`) | Equivalent: every string holding `\r` is written in double quotes, where it is the escape `\r`, so the text never holds a raw one. |
