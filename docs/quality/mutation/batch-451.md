# Mutation testing, batch 451

One module not scored before, run with `--kinds all`:
- The platform and the data mesh as the desktop app calls them: which Platform workspace each environment uses, shared keys and moving them, domains made in Fabric, landing zones, the overview, Data mesh > Home and the map, products, links and catalog text (`central/service.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 735 mutants, so a sample of 150 was run. `main` has changed this module since (#3319); the new tests pass against today's version.

The first run's test set was test_central_service_cov3, test_platform_service_paths, test_mesh_links_remove and test_platform, and it left 54 survivors. Sixteen more test files call this module, so those 54 were run again with them added: test_shared_keys, test_platform_repo, test_domain_adoption, test_versioned_contracts, test_landing_zones, test_requests_core_four, test_landing_copy_requests, test_request_left_in_fabric, test_mesh_home, test_domain_in_fabric, test_shortcut_request_retries, test_request_retries, test_governance_notices, test_product_promises, test_product_status and test_landing_copy_service_edges. They killed 35 of them. The "before" column counts both runs. test_delete_my_data_stores was left out, because it reads `docs/` and the module's source.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests use fakes only: nothing reaches Fabric.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/service.py` | test_central_service_cov3, test_platform_service_paths, test_mesh_links_remove, test_platform and the 16 files above (+ test_central_service_mutation_b) | 150 of 735 (150 + 0) | 131 / 19 | **87.3%** | 131/150 → 150/150 | 0/0 → 0/0 | 150 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/service.py**:
  - Keys already shared say so, with nothing renumbered. A cancelled move is ok, clears the keys database and reopens every environment's keys.
  - A landing zone's listing has only the files under `Files/`, with that prefix taken off, and it lists recursively.
  - Fabric refusing a new domain with 401 or 403 says only a Fabric administrator can. An `AlreadyExists` code without 409 is still a domain Fabric has. Any other refusal is raised as it is.
  - The suggested domain is Prod's, even when Dev's workspace is in another one.
  - Deleting a product that has no file isn't an error.
  - A product is linked only when the open project consumes it.
  - A link the catalog already has, with the same version and columns, isn't sent again.
  - Going back to LH_Keys: a key table with no stats there counts from 1, so it's allowed. Keys only the platform issued are named up to the last one.
  - Data mesh > Home: the team view gets the access reviews, and the org view gets the domains.
  - The overview counts the key tables and their keys, and shows the last 8 audit entries.
  - An environment with functions is "fabric", and one without is "local".
  - → `test_central_service_mutation_b.py`.

### Left

None.
