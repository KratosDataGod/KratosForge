# Git integration plan

KratosForge becomes a Git-native place to build Fabric solutions: build locally, version with Git, validate before deployment, run and consume in Fabric. This page is the assessment that came first and the plan the four phases follow. Phase 1 (Git foundation) is built; the rest is planned.

## What exists today

| Area | What is there | What it means for Git |
| --- | --- | --- |
| Shell | Tauri 2 (`app/src-tauri/src/main.rs`). One `rpc` command forwards JSON lines to the Python engine. Calls are serialized: one at a time. | A clone, fetch, pull or push can't run inside one `rpc` call, or every other page waits. Network Git work runs in the background in the engine and the page polls, as Prove and Side by side already do. |
| UI | Plain JS in `app/ui/`, pages registered in `features.js`. The left rail is full (8 pages); Settings sits in the bottom group. | Source control goes in the bottom group, above Settings (see below). |
| Engine | `fabric_desktop` package; `Service` mixes in one class per area; `server.py` lists every callable method. | A new `GitMixin` (`git_service.py`) over a `fabric_desktop.git` package. |
| Git today | `meta/release.py` reads a metadata project's branch, commit, ahead/behind and uncommitted files with the person's `git`, without network or prompts. Promote records the commit in `meta.deployments` (PR #73). | Read-only and metadata-only. Phase 1 adds the general Git layer; `release.status` keeps its own rules about which files count, and can move onto the new layer later (owned by the Staged roadmap work). |
| Environments | `environments.toml` in a metadata project: Dev, Test, Prod workspaces, "Tables in", "Runs on", `require_commit`. | Phase 3 builds environment profiles on this file rather than a second format. |
| Deployment | Compare before promote, tests gate promote, release record, notebook publish to a chosen workspace. | Phase 3's Deploy workflow reuses these gates. |
| Auth | MSAL public client (Fabric CLI app) + WAM; Fabric, Storage and Power BI scopes. | Git needs no new Entra scope: Git signs in through Git's own credential helper. Phase 2's Fabric Git API calls need `Workspace.GitCommit.All` / `Workspace.GitUpdate.All` (see below), added to `docs/permissions.md` then. |
| Notebooks | `.ipynb` in `%LOCALAPPDATA%\FabricDesktop\notebooks`. | Not in a repository by default. Source control can version that folder, and a notebook in a repository shows its branch and changes. |

Nothing in the repository clones, commits, switches branches or pushes today.

## Decisions

- **Git CLI, not a library.** The person's own `git` (Git for Windows) runs every operation: it already handles HTTPS through Git Credential Manager (stored in Windows Credential Manager), SSH through the OpenSSH agent, every hosting provider and every repository format. No new Python or Rust dependency. The app never sees, stores or logs a password or token.
- **Never prompts in a terminal.** `GIT_TERMINAL_PROMPT=0`; Git Credential Manager's own sign-in window still opens when a remote needs one. SSH runs with `BatchMode=yes` unless the person configured their own SSH command, so a missing key fails with a message instead of hanging.
- **Arguments, never a shell.** Every call is an argument list. Paths are checked to stay inside the repository; branch names are checked with `git check-ref-format`; remote addresses must be `https://`, `ssh://`, `git@host:path` or a local folder, and may not carry a password. `ext::` and other transports are refused (`GIT_ALLOW_PROTOCOL`).
- **Destructive steps need a yes.** Deleting a branch, discarding changes and undoing a merge refuse without `confirm`; the UI asks first. Switching branches with uncommitted changes refuses until the person picks "put them aside" (stash) or "bring them with me". Force push is not offered.
- **Secrets are checked before a commit.** Staged content is scanned for keys, tokens, connection strings with passwords and private keys; a commit with a finding needs an explicit "commit anyway". Every message from Git is redacted (credentials in URLs, bearer tokens, keys) before it reaches the UI or a log.
- **Local and cloud are labeled.** Fetch, pull, push and clone say they talk to the remote; everything else is "this PC only". Pushing to Git never claims to update a Fabric workspace.
- **Where Source control goes.** The rail is full. Source control joins the bottom group, above Settings, like a project-level tool, and the top bar shows the current repository's branch everywhere. Pull requests open in the provider's site (GitHub, Azure DevOps) in Phase 1.

## Phase 1: Git foundation (this pull request)

| File | What |
| --- | --- |
| `sidecar/fabric_desktop/git/__init__.py` | Package entry. |
| `sidecar/fabric_desktop/git/cli.py` | Runs `git` safely: environment, timeouts, cancel, errors in plain words, redaction. |
| `sidecar/fabric_desktop/git/repo.py` | Repository: init, open, status (porcelain v2), branches, switch, create, delete, stage, unstage, discard, commit, log, commit detail, compare, file at a commit, diffs parsed into hunks and lines, notebook view of `.ipynb` diffs, merge conflicts and undoing a merge. |
| `sidecar/fabric_desktop/git/remote.py` | Remote address checks and provider links: GitHub, Azure DevOps, generic. Links to the repository, branch, commit and a new pull request. |
| `sidecar/fabric_desktop/git/secrets.py` | Secret patterns, the pre-commit scan and redaction. |
| `sidecar/fabric_desktop/git/ops.py` | Background clone, fetch, pull and push with progress and cancel. |
| `sidecar/fabric_desktop/git_service.py` | `GitMixin`: the UI's methods, the list of repositories this PC opened (`git_repos.json`, paths only). |
| `sidecar/fabric_desktop/service.py`, `server.py` | Mix in `GitMixin`; list its methods. |
| `sidecar/tests/test_git.py` | Tests on temporary repositories and a local bare remote. |
| `app/ui/git.js` | Source control page: Changes, Branches, History, Repository settings; diff view; dialogs; top-bar branch pill; commands. |
| `app/ui/index.html`, `styles.css`, `icons.js`, `features.js` | Load the page, its styles and icons, Preview flag. |
| `app/ui/notebook.js` | Notebook ribbon shows the notebook's branch and opens its changes when it is in a repository. |
| `app/tests/ui.test.cjs` | Pure helpers of `git.js`. |
| `docs/source-control.md`, `user-guide.md`, `README.md`, `docs/README.md`, `architecture.md`, `troubleshooting.md` | Guide and links. |

Acceptance: clone a repository, change a project, see the differences, create a branch, commit and push, without leaving the app.

## Phase 2: Fabric Git awareness

Fabric's own Git integration connects a workspace to a branch and folder in Azure DevOps or GitHub; KratosForge shows that relationship and drives it, but never treats a push as a workspace update.

Fabric REST API (Core > Git, checked 2026-10-17 on Microsoft Learn):

| Operation | Call | Scope | Notes |
| --- | --- | --- | --- |
| Get connection | `GET /v1/workspaces/{id}/git/connection` | Workspace.Read.All | Provider, organization, repository, branch, directory. |
| Get status | `GET .../git/status` | Workspace.GitUpdate.All or Workspace.GitCommit.All | Long-running. Workspace head, remote commit, per-item workspace and remote changes, conflicts. |
| Commit to Git | `POST .../git/commitToGit` | Workspace.GitCommit.All | Long-running; All or Selective; needs `workspaceHead`; comment up to 300 characters. |
| Update from Git | `POST .../git/updateFromGit` | Workspace.GitUpdate.All | Long-running; needs `remoteCommitHash`; conflicts need `PreferRemote` or `PreferWorkspace`; overriding items needs `allowOverrideItems`. |
| Connect, initialize, disconnect | `POST .../git/connect`, `initializeConnection`, `disconnect` | Workspace.ReadWrite.All | Workspace admin. GitHub needs a configured connection. |
| My Git credentials | `GET/PATCH .../git/myGitCredentials` | Workspace.ReadWrite.All | Automatic or a configured connection. |

Plan: `fabric_desktop/fabric_git.py` (client over these calls, long-running operation polling), workspace association saved in a committed `fabric-desktop.toml` (tenant, workspace, branch, folder; no secrets), a status view that lines up local branch, remote branch and the workspace's synced commit, Commit to Git and Update from Git behind a preview of every item and an explicit choice for each conflict. The artifact capability registry (`fabric_desktop/artifacts.py`) records per item type: Git support, deployment pipeline support, definition API, local editing, safe round-trip and limits, starting from Microsoft's supported-items lists (Git: notebooks, lakehouses, pipelines, environments, warehouses, Spark job definitions, variable libraries and more; reports and semantic models are still preview). Anything not in the registry is shown as unsupported, never guessed. Limits to surface: 1,000 items per workspace, 250-character paths, 25 MB files, no sensitivity-labelled items, no GitHub Enterprise Server.

## Phase 3: deployment and validation

Environment profiles on `environments.toml` plus custom environments; a Validate project command with registered checks (Python syntax, SQL parse with sqlglot, notebook structure, project files, item definitions, missing dependencies, secrets, environment config) reporting severity, file, line and what to do; a dependency inventory from notebook lakehouse attachments, pipeline activities and model data sources (only from real metadata); a Deploy page: target, changes, validation, compatibility, dependencies, preview, confirm, run through item definition APIs or deployment pipelines (`POST /v1/deploymentPipelines/{id}/deploy`, scope Pipeline.Deploy), results and history. It reuses compare before promote, tests gate promote and the release record in `meta.deployments`.

## Phase 4: extensions

Extension manifest (id, version, engine range, entry points, permissions), validators and templates first, declarative and in-process only for signed or built-in extensions; third-party code runs in its own process with only the permissions its manifest names, and can be turned off or removed. One example extension and a contributor guide.

## Repositories by scope

Each kind of definition has its own repository: notebooks (one per team or project), data mesh projects (one per domain or project), platform (one per tenant) and capacity. A `fabric-desktop.repo.toml` marker at the root says the kind. For platform definitions, Git is the source of truth and the catalog (SQL_Platform and LH_Meta) is the deployed state; drift is reported, never silently overwritten.

1. **Built:** the marker and the four kinds, starting files for each, the list grouped by kind, choosing a kind for an existing repository (marker only, confirm to change), and a warning when a notebook sits in a repository of another kind.
2. **Built:** notebooks repositories are places to save notebooks (Save in, Move), and a notebook there takes its lakehouse for the current environment from `locations.toml` unless it has its own. Prove and Side by side use it too.
3. **Built:** platform repository: export the catalog as files to review, compare Git with each environment's platform, and deploy the committed files with a plan and one confirm (admins only, audited, no renumbering, removals ticked one by one, policies published as new versions). File shapes and deploy rules agreed with the Enterprise governance owners; see [Platform repository](source-control.md#platform-repository).
4. **Built:** capacity repository: `capacity.toml` and a drift report against what Fabric shows (size, region, state, which workspaces sit where), plus writing Fabric's state into the file as a change to review. Read-only towards Fabric: no writes to capacities. See [Capacity repository](source-control.md#capacity-repository).

Stages 1 to 4 need no new Entra scope: stage 3 uses the platform's functions as the Platform page does, and stage 4 reads capacities and workspaces with the Capacity.Read.All and Workspace.ReadWrite.All scopes the app already asks for (only reading).

## Known gaps after Phase 1

- Pull requests open on the provider's site; status, reviews and checks inside the app come later.
- Staging part of a file is by block of changed lines (hunk); choosing single lines inside a block is not built.
- No rebase, cherry-pick, tags or submodules UI.
- Not yet tried on Windows with Git Credential Manager and an SSH agent, so the page carries a Preview tag.
