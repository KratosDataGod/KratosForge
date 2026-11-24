# Mutation testing, batch 244

A re-score of `localdata/discovery.py` (finding tables in a local folder) on today's `main`. Batch 4 scored it at **95.8%** before boundary mutants existed, and batch 37's boundary-only re-score left it at **88.9%**, with 1 equivalent. This run uses `--kinds all`.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 151 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/discovery.py` | test_local_data, test_local_data_discovery_edges, test_localdata_discovery_mutation, test_boundary_mutation_2, test_local_data_engine_more_edges, test_localdata_projects_mutation, test_file_reader_fuzz, test_local_data_projects_fuzz | 150 of 151 (141 + 9) | 143 / 7 | **95.3%** | 135/141 → 135/141 | 8/9 → 8/9 | 143 / 7 | **95.3%** | 7 equivalent |

No bugs were found, and no new tests were needed: every mutant left is equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/discovery.py` | 39 | `"excel": 4` to `5` in `PRECEDENCE` | Equivalent: only the order of the values is used, and excel is still last. |
| `localdata/discovery.py` | 90, 91 | `size: int = 0` and `files: int = 0` to `1` in `Found` | Equivalent: both places that make a `Found` pass `size` and `files`. |
| `localdata/discovery.py` | 124 | suffixes tried shortest first (`reverse=False`) | Equivalent: no suffix in `FORMATS` ends with another one, so at most one can match a name. |
| `localdata/discovery.py` | 131 | `(None, False)` to `(None, True)` for a folder that isn't a dataset | Equivalent: with no kind, `hive` is never read. |
| `localdata/discovery.py` | 173 | `len(names) >= 1` to `>= 0` | Equivalent: `formats` holds one format only when at least one name was added with it. |
| `localdata/discovery.py` | 173 | `formats <= {...}` to `formats < {...}` | Equivalent, as in batch 37: `formats` holds exactly one format there. |

