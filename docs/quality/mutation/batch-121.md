# Mutation testing, batch 121

One module not covered before, touched by no open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Data mesh > Home: the incidents that reach data products, the team lead's products and what they depend on, on call, and the organization's counts per domain (`central/home.py`).

`central/keys_home.py` was planned too. Its "before" run finished (77 of 77 mutants, 48 killed and 29 survived, **62.3%**, with test_shared_keys and test_platform_service_paths), but its kill tests weren't written before this lane's stop, so it is left for a later batch. Most of its survivors are in `setup`, the steps that create the Keys workspace, which no test runs step by step.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. `central/home.py` has 142 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: test_mesh_home doesn't read `docs/`, `examples/` or `app/`. A mutant whose run times out counts as killed; none timed out.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/home.py` | test_mesh_home (+ test_mesh_home_mutation) | 142 of 142 (139 + 3) | 115 / 27 | **81.0%** | 113/139 → 136/139 | 2/3 → 3/3 | 139 / 3 | **97.9%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/home.py**:
  - An incident without a title is named by its name, and a missing priority, owner, opening time or number is empty text.
  - Open incidents come first, then by priority, with no priority last.
  - A product of mine without a name or version shows its id and no version; a deprecated one without a date "stops soon".
  - What I depend on is what my project reads or I follow, never my own project's products or a product the catalog no longer has. A dependency without a version is "Version 1".
  - Only my ten newest asks are shown.
  - Owner gaps count only the products with no owner.
  - A request is stale after more than 5 days (5 days is not stale), and the five oldest are shown, oldest first.
  - Domains sort by name (by id when they have none). A domain counts each product with an incident, and adds no "without a second owner" line when every product has two owners.
  - → `test_mesh_home_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/home.py` | 139 | `p.get("domain_id") or ""` to `and`, as the fallback of `parent.get` | Equivalent: the fallback is used only for a domain id that isn't a domain, and no domain row has that id or `""`, so the product counts toward no domain either way. |
| `central/home.py` | 143 | `(_days(...) or 0) > STALE_DAYS` to `or 1` | Equivalent: the fallback is used for a time that can't be read or 0 days, and 0 and 1 are both 5 or less, so the request isn't stale either way. |
| `central/home.py` | 143 | `-(r["days"] or 0)` to `or 1` in the sort | Equivalent: only requests with more than 5 days are in the list, so `days` is never 0 or None there. |

