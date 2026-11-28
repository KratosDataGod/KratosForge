# Mutation testing, batch 19

Three modules that no open pull request is changing:
- The metadata project templates: a new project's defaults, who owns the sample key tables and source systems, and the TOML each entity template writes (`meta/templates.py`).
- Source control's service: the defaults each call passes on, the list of repositories this PC opened, the platform repository's reads and deploy refusals, and the note a deploy leaves (`git_service.py`).
- Optimize's app calls: the outbox kept open, which failures are tried again, the overhead a sync records, the central lakehouse's role changes, and the asks before sharing runs or an analysis (`optimize_service.py`).

`integrations/capabilities.py` was started and dropped. Its tests take about 40 seconds per mutant, so after 35 minutes only 41 of 150 mutants had run, and `optimize_service.py` took its place.

The run was at `main` dbc70faff (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 18 (sample cap 150, seed 7), on DuckDB 1.5.6. The branch was then brought up to `main` 352188c0d after batch 18 merged, and its tests were run again there.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. One test was left out because it reads files outside `sidecar/`: `test_vscode_setup.py::test_copilot_instructions_match_the_docs`, in the wider `git_service.py` set.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/templates.py` | test_meta_templates_edges, test_meta, test_meta_project_edges, test_meta_project_mutation, test_landing_zones, test_model_date_table (+ test_meta_templates_mutation) | 84 of 84 | 67 / 17 | **79.8%** | 84 / 0 | **100%** | none |
| `git_service.py` | test_git_service_edges, test_git, test_fabric_git, test_corrupt_state_reads (+ test_capacity_repo, test_git_scopes, test_git_service_more_edges, test_platform_repo, test_vscode_setup, test_notebook_places, test_notebook_folders, test_git_service_mutation, test_git_service_platform_mutation) | 150 of 211 | 76 / 74 | **50.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `optimize_service.py` | test_opportunities_share, test_optimize_local_runs, test_optimize_outbox, test_optimize_service, test_optimize_service_edges, test_optimize_share_analysis (+ test_access_cache, test_tenant_history, test_opportunities, test_optimize_service_mutation) | 150 of 239 | 126 / 24 | **84.0%** | 150 / 0 | **100%** | none |

There were no timeouts.

The existing tests in brackets were tried on the survivors first. For `git_service.py` they killed 51 of the 74: the sampled set had missed the platform repository, notebook places and capacity repository tests. For `optimize_service.py` they killed none. The new tests killed the rest. The four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **meta/templates.py**:
  - A new project having the sample by default, and going into a folder that already exists without touching what is there.
  - Only the three-layer preset with the sample getting it: no entities, landing files, key tables or source systems otherwise.
  - The sample key tables, source systems and entities belonging to the owner, or to "me" when there is none.
  - Without keys, the first column being the key.
  - A landing entity reading its source, or a placeholder named after the entity.
  - Clean and fact loading incrementally on the upstream table's watermark, and in full when there is none.
  - A type 2 dimension tracking the first three columns that aren't keys.
  - A custom step owned by its owner, or by "me".
  - → `test_meta_templates_mutation.py`.
- **git_service.py**:
  - The list of repositories keeping the newest 50, and a notebooks place named by its saved name or else its folder.
  - Open, init and create handing back the repository's status.
  - The operations list for one repository or all of them.
  - Each default a call passes on: changing a repository's kind, discarding a block and deleting a branch needing confirm; a notebook diff comparing code only; the log showing 50 commits; a new branch being switched to; a branch with commits no other holds kept without force.
  - A platform read that fails about another operation, or for another reason, not being taken for an older platform.
  - A diff against an environment that isn't one, export keeping prod's key table when dev and prod disagree, only a platform admin deploying, ignored platform files named five at a time, and a deploy noting its commit.
  - → `test_git_service_mutation.py`, `test_git_service_platform_mutation.py`.
- **optimize_service.py**:
  - The outbox opened once, and discover counting the tables that aren't any family's.
  - Connecting saying it created nothing.
  - A refused write, including a network error that says 403, not being tried again, and the answer being `False`, not just falsy.
  - A sync recording the rows and commits it added.
  - A connection saved without the schemas flag being read as having schemas, both for a family's tables and for keeping them from DefaultReader.
  - The reason a role read failed being cut to 200 characters.
  - A role write refused with either `PreconditionFailed` or status 412 saying the roles changed, and any other failure raised as it is.
  - Sharing runs reading the whole history, and sharing runs or an analysis needing approve as well as the confirm.
  - Each confirm being 32 hex characters, the ask naming the lakehouse, and an analysis's confirm not depending on key order.
  - Metadata runs coming only from environments kept on this PC (Dev by default), and a run log row without its environment getting the one it was read from.
  - → `test_optimize_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `git_service.py` | 84 | `json.dumps(..., indent=2)` → `indent=3` for the list of repositories | Equivalent: the file is only ever read back as JSON. |

### Real bugs found

None.

### Not in this batch

`integrations/capabilities.py`, for the time its tests take per mutant.

