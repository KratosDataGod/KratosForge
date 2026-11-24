# Mutation testing, batch 65

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The `IPython.display` stand-in a notebook cell imports when IPython isn't installed: display objects, image formats, the `display_*` helpers, and the import hook that provides it (`ipython_display.py`).
- The Direct Lake check over a semantic model's definition and its Delta tables' numbers: guardrails per capacity SKU, schema drift, calculated columns, relationship keys and the order of findings (`model_directlake.py`).
- The capacities file in a platform repository: reading `capacity.toml`, comparing it with what Fabric shows, and writing Fabric's state back (`git/capacity_files.py`).

The run was at `main` 81d17063d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `model_directlake.py` has 172 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ipython_display.py` | test_ipython_display_edges, test_rich_outputs (+ test_ipython_display_mutation) | 87 of 87 (87 + 0) | 73 / 14 | **83.9%** | 73/87 → 86/87 | none | 86 / 1 | **98.9%** | 1 equivalent |
| `model_directlake.py` | test_model_directlake, test_model_directlake_service_edges (+ test_model_directlake_mutation) | 150 of 172 (147 + 3) | 99 / 51 | **66.0%** | 99/147 → 146/147 | 0/3 → 3/3 | 149 / 1 | **99.3%** | 1 equivalent |
| `git/capacity_files.py` | test_capacity_repo, test_capacity_files_edges, test_file_text_one_line, test_git_service_more_edges (+ test_capacity_files_mutation) | 91 of 91 (89 + 2) | 79 / 12 | **86.8%** | 78/89 → 89/89 | 1/2 → 2/2 | 91 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ipython_display.py**: the tests that call `install()` restore `sys.modules` and `sys.meta_path` with monkeypatch.
  - PNG bytes are read as PNG and shown as `image/png`; empty or unknown bytes are PNG too.
  - An object with both data and a URL, or with neither, isn't named by a URL.
  - JSON text given as data stays text, and `expanded` is off by default.
  - `display_json` wraps a value unless `raw=True`; with `raw=True` text is still wrapped; `display_png` gives an Image.
  - The stand-in reports IPython 8.30.0 as `version_info` and `__version__`.
  - Only `IPython` and `IPython.core` are packages; another name, or no stand-in yet, finds nothing.
  - `install()` leaves a real IPython module alone, and points a stand-in a cell already imported at the new kernel.
  - → `test_ipython_display_mutation.py`.
- **model_directlake.py**:
  - Every SKU's guardrails are pinned at Microsoft's numbers.
  - Row groups aren't estimated unless set, and files and rows are never shown as "about".
  - A partition that isn't an entity, or has no entity name, isn't read; a relationship needs both tables.
  - A table is named with its schema only when it has one, and a missing table is reported with `exists` False.
  - A calculated column fails on Direct Lake on SQL but not on OneLake, and the storage kind is reported.
  - A column with no source column is read by its name, and one with a source column by that.
  - Exactly at a limit, and at exactly 90% of it, is "near"; just under 90% says nothing.
  - A model exactly at its size limit is fine, the model's bytes are its tables' sum, and the size message gives whole GB.
  - Fails come before warnings whatever the table, and one duplicate value is singular.
  - → `test_model_directlake_mutation.py`.
- **git/capacity_files.py**:
  - Text is fine up to 256 characters; a name, id, field or workspace with other characters, or longer, is refused.
  - A missing file has no capacities and no problems.
  - A long section name is cut to 80 characters in a problem.
  - A capacity only the file has, or only Fabric has, is reported with every field exact.
  - Uncommitted changes need `confirm` by default, and the file isn't touched.
  - With no capacities, the file is the header as comments, and writing the same text again needs no confirm.
  - → `test_capacity_files_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `ipython_display.py` | 120 | `data[:8]` to `data[:9]` in the PNG check | Equivalent: a PNG that no longer matches falls through the JPEG and GIF checks to the default, which is also PNG. |
| `model_directlake.py` | 256 | `"info": 2` to `3` in the sort order | Equivalent: fail 0, warn 1 and info 3 sort the same as 0, 1, 2, and the counts use the keys, not the numbers. |

Neither involves text input, so leading or trailing whitespace and regex edge cases can't change them.

