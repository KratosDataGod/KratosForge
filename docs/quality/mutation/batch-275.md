# Mutation testing, batch 275

One module that had no score before, run with `--kinds all`:
- Governance's notices: the events a request, a shortcut check, a product change or deprecation, an incident's reach and a policy exception about to end send to Teams (`governance_notices.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. From `test_platform.py`, only the two tests that use this module were run: `test_data_request_notices_name_who_decides` and `test_a_product_change_notice_names_its_followers_and_what_changed`. None of the tests run reads `docs/`, `examples/` or `app/`. The module has 166 mutants, so 150 were sampled. No mutant's run timed out. A first run of this batch was cut short when the machine restarted, so the figures here are from a second, complete run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `governance_notices.py` | test_governance_notices, test_requests_core_four, test_platform (2 tests) (+ test_governance_notices_mutation) | 150 of 166 (148 + 2) | 118 / 32 | **78.7%** | 117/148 → 147/148 | 1/2 → 2/2 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **governance_notices.py**:
  - A request is about the product it names, not the first product in the list.
  - A new data product is in the domain of the product its detail names.
  - A shortcut request with no product is about none.
  - Only an exception for a domain is in that domain.
  - A waiting request keeps the time it was asked.
  - What a declined request left in Fabric is said only for its own kind. A copy job on a domain request, a domain on a source request, a shortcut on a domain request, or an empty record says nothing.
  - A title of exactly 200 characters is kept whole. A tail longer than the limit keeps none of the head.
  - A changed product keeps its domain, and a shortcut event keeps its time.
  - A deprecation names each reading project and link, with `?` for a link without an alias. It trims the date and leaves out the product's own project. It goes to the people who saved the links and each project's team, or to platform admins when no project name can be a role. It falls back to the product's `domain`.
  - A deprecation read by nobody else, or by a consumer with no project, or of a product without an ID, says nothing.
  - An incident's reach is posted whole: its title, environment, single domain, affected products, people and dedupe key. When it spans more than one domain it names none, and with no products it has no detail.
  - An exception about to end names the place it covers and goes to the domain's owners. One for a place that isn't a project or a domain goes to whoever asked for it.
  - → `test_governance_notices_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `governance_notices.py` | 194 | `usedforsecurity=False` to `True` | Equivalent: the flag only tells hashlib what the digest is for, and the digest is the same. |
