# Mutation testing, batch 77

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Power BI reports, paginated reports and dashboards shown in the app: their embed addresses, the sign-in token's expiry and what each kind can do (`powerbi_embed.py`).
- Git repository addresses: which are accepted, the folder a clone gets, and the links to the repository, branch, commit and pull requests (`git/remote.py`).
- Notebook environments and packages: listing, attaching, installing and removing, and the organization settings that allow or stop a package install (`environment_service.py`).

The run was at `main` d177fe5b4 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `git/remote.py` has 185 mutants, so 150 were sampled; the other two have fewer than 150, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_rpc_surface.py` reads `app/`, so it was left out of the `environment_service.py` set; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `powerbi_embed.py` | test_powerbi_embed, test_powerbi_embed_service_edges, test_credential_expiry (+ test_powerbi_embed_mutation) | 84 of 84 (84 + 0) | 58 / 26 | **69.0%** | 58/84 → 84/84 | none | 84 / 0 | **100%** | none |
| `git/remote.py` | test_git_remote_links, test_git (+ test_git_remote_mutation) | 150 of 185 (143 + 7) | 129 / 21 | **86.0%** | 124/143 → 143/143 | 5/7 → 7/7 | 150 / 0 | **100%** | none |
| `environment_service.py` | test_environments, test_fabric_environment, test_shared_sessions (+ test_environment_service_mutation) | 58 of 58 (58 + 0) | 42 / 16 | **72.4%** | 42/58 → 58/58 | none | 58 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **powerbi_embed.py**:
  - Each kind (report, paginated report, dashboard) has exactly its own list of what it can do, the events it sends and its limits.
  - An error from Power BI other than 401, 403 or 404 comes through with its own status.
  - A token already past its expiry has 0 seconds left, not a negative count.
  - The expiry is read from a token whatever padding its claims need.
  - A report or dashboard keeps the id, name and model Power BI gives, and falls back to the asked id and empty text when they are missing; so do a dashboard's tiles.
  - An item with no type is called "this item" in the message.
  - → `test_powerbi_embed_mutation.py`.
- **git/remote.py**:
  - An empty or blank address asks for one, an http address is refused with the reason, and a file address is kept as given.
  - A sign-in name in the address of 30 characters or more, or starting like a GitHub token, is refused; 29 characters is accepted.
  - A clone folder is named from the last part of an scp, https or Windows address.
  - An address with no server has no host.
  - GitHub, Bitbucket and GitLab addresses name their owner and repository, including GitLab's nested groups.
  - An Azure DevOps address needs both a project and a repository, on either host name.
  - Links with no branch have no branch or pull request links, and Azure DevOps links name the branch, commit and target branch.
  - An address with only a password shows without it.
  - → `test_git_remote_mutation.py`.
- **environment_service.py**:
  - Each organization setting that stops a package install (packages turned off, a package folder setting that can't be used, no PyPI and no folder) stops it with its own message, before anything starts.
  - The package source is named for each combination of folder and PyPI settings, and an unset folder is no folder.
  - Packages are allowed unless the setting is false.
  - Removing packages starts a removal job, and a job is read as given.
  - A new environment made for a notebook has a name of at most 50 characters.
  - Attaching an environment looks up the notebook's session without making one.
  - Only a process kernel for a named notebook gets the notebook's packages.
  - → `test_environment_service_mutation.py`.

