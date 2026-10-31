# Source control (Preview)

**Source control** keeps a folder on your PC in Git without leaving KratosForge. You can clone a repository, change notebooks and metadata files, see exactly what changed, make a branch, commit and push. It works with GitHub, Azure DevOps and any other Git server, over HTTPS or SSH.

Open it from **Source control** at the bottom of the left rail, or from the branch name in the top bar.

> **Your PC, not Fabric.** Everything on this page is Git on your PC. Commit and push change your repository; they never change a Fabric workspace. Connecting a workspace to Git is Fabric's own feature and isn't part of this page yet.

## Before you start

- **Install Git for Windows** from [git-scm.com](https://git-scm.com/download/win). It includes Git Credential Manager, which signs you in to GitHub and Azure DevOps in your browser and keeps the sign-in in Windows Credential Manager. KratosForge uses the Git you installed and never sees your password or token.
- **For SSH**, add your key to the SSH agent (`ssh-add`) and connect to the server once in a terminal so its host key is known. The app doesn't ask for passphrases or accept host keys for you.
- **Tell Git who you are** in **Repository settings** if Git asks. The name and email are saved for that repository only.

## Getting a repository

| Choose | What it does |
| --- | --- |
| **Clone a repository** | Paste the address from GitHub or Azure DevOps (`https://…` or `git@…`). The copy goes to a new folder under `source\repos` in your user folder, or a folder you pick. Cloning runs in the background with progress and **Cancel**; a cancelled or failed clone removes only the folder it made. |
| **Open a folder** | A folder that is already a Git repository. Paste its path, or pick it with **Browse…**. |
| **Version my notebooks** | Starts a repository in the folder the app keeps your notebooks in. |
| **New repository** | Makes a new repository for one kind of definition (below), in an empty folder, with that kind's starting files. It refuses a folder that is already inside another repository. |
| **Make a folder a repository** | From the command bar: turns a folder that isn't a repository yet into one, without adding files. |

The app remembers up to 50 repositories; pick one from the name at the top of the page. **Repository settings > Take it off the list** forgets a repository without deleting any files.

Addresses are checked before Git sees them: unencrypted `http://`, addresses with a password or token in them, and anything that isn't a Git address are refused.

## Repository kinds

Each kind of definition lives in its own repository, so a notebook change never rides along with a platform change. A repository says what it holds in a small file at its root, `fabric-desktop.repo.toml` (for example `kind = "platform"`).

| Kind | How many | Starts with |
| --- | --- | --- |
| **Notebooks** | One per team or project | `notebooks/` and `locations.toml`, where these notebooks find their data in each environment (workspace and lakehouse ids, never passwords or keys) |
| **Data mesh project** | One per domain or project | Nothing extra; the project's own files go here as today |
| **Platform** | One per tenant, for platform admins | `platform.toml` (where the platform's items live) and folders for domains, sources, key tables, landing zones and policies |
| **Capacity** | One or more, for capacity admins | `capacity.toml`: which workspaces sit on which capacity |

Every kind also gets a `.gitignore` that keeps local data (`.local/`), caches and `.env` files out of Git. The new files show as changes to review; nothing is committed or pushed for you.

The repository list at the top of the page is grouped by kind. A repository that doesn't say what it holds shows **What does this repository hold?** with a button per kind; the app suggests one from what is in the folder (for example `environments.toml` means a data mesh project). Choosing writes only the marker file, as a change you commit. Changing a kind a repository already states asks first.

A notebook saved in a repository of another kind shows **wrong repository** next to **Save**, so it can be moved to a notebooks repository.

### Notebooks in a notebooks repository

Every notebooks repository you open here is also a place to save notebooks, next to the app's own notebooks folder (**This PC**).

- **New notebook** asks where to save it once you have a notebooks repository. It goes under `notebooks/` in that repository, as a change to commit. The last choice is remembered.
- **Move this notebook** (from the notebook's **Data** button or the command bar) moves its file to another place. The copy is checked before the original is removed, and nothing moves if a notebook with that name is already there. Nothing is committed.
- Names stay unique across all places, so schedules, runs and published items still find a notebook by its name.
- Home lists where each notebook is saved.

**Where the data is.** A notebook in a notebooks repository has a **Data** button next to **Save**. It shows the lakehouse for the environment picked in the top bar, taken from the repository's `locations.toml`:

1. A lakehouse attached to the notebook itself always wins, as before.
2. Otherwise, the repository's location for the current environment. Switching the environment switches the lakehouse for the next run, and the Explorer marks it, for example **Test location**.
3. Otherwise nothing is attached. **This PC** means that environment attaches nothing on purpose; an environment with no location shows **No location for Test**.

Running cells, **Prove**, **Side by side**, **Publish to OneLake**, **Run in Fabric** and Fabric schedules use the location's lakehouse when the notebook has none of its own. It is never saved into the notebook, so the notebook keeps following the environment.

**Editing locations.** **Data locations** (next to the repository's name on this page, or **Edit locations** on the notebook's **Data** button) shows Dev, Test and Prod, each with a workspace, a lakehouse and an optional Fabric SQL database, picked from what you can see in Fabric. Saving writes `locations.toml` with workspace and item ids and their names, as a change to commit. Nothing in Fabric changes, and no passwords or keys are written.

**VS Code.** **Set up for VS Code** (next to the repository's name) adds a validate task, the Copilot server and recommended extensions to the repository, as changes to commit. See [Set up a repository for VS Code](vscode.md#set-up-a-repository-for-vs-code).

### Platform repository

For platform definitions, Git is the source of truth and each environment's platform holds what is deployed. A platform repository has a **Platform** tab next to **Changes**.

**Compare with** Dev, Test or Prod shows every object as **same**, **changed** (with each field's value in Git and deployed), **only in Git** (not deployed yet) or **removed in Git** (deployed, but its file is gone). Comparing only reads; nothing changes in Git or in Fabric.

**Export from platform** writes what the platform holds into the repository, one file per object, as changes to review. Nothing is committed and nothing in Fabric changes. Only files whose text changes are written, so exporting twice changes nothing. A file you changed and haven't committed is replaced only after a second confirm. Files for objects that aren't deployed are left as they are, never deleted.

| File | Holds |
| --- | --- |
| `domains/<id>.toml` | id, name, parent, description, owners. A domain from Fabric keeps only its `fabric_domain_id` and our fields; its name and parent come from Fabric. |
| `landing_zones/<id>.toml` | id, name, domain, secure, readers (groups, by name or object id), sensitivity label, and `devtest` (whether Dev and Test read it: `none` or `read`) |
| `sources/<code>.toml` | source_id, code, title, description, owner, system type, active, expected hours, and `zone` (its landing zone's id) when it isn't in Shared |
| `key_tables/<name>.toml` | name, business key, description, domain |
| `policy_templates/<id>.toml` | your organization's policy templates: name, category, description and the rest of the template's `[definition]`. Built-in templates ship with the app and aren't exported. |
| `policies/<id>.toml` | status, the published version's `[definition]` and each assignment as `[[assign]]` |
| `glossary.toml` | each term with its definition, domain and owner |
| `admins.toml` | the platform admins |
| `shortcut_rules.toml` | the shortcut request rules |

Never in a file: times, who changed what, key values, requests, decisions, grants, audit rows, a source's readers (which projects read it), shortcut health, source connections, where a landing zone lives (its workspace and lakehouse ids), when reads were switched to the landing workspace and by whom, a template's author, webhook URLs, passwords or connection strings. A zone's readers are free text in the platform, so one can be a person's email: export leaves any reader with an `@` out of the file, and a file that names one can't be deployed. Name a group instead. A policy that was never published isn't exported. On a platform without policies yet, no policy or template files are written. **Who can add templates** (a platform setting in the catalog) isn't exported: the repository has no file for platform settings yet (`platform.toml` says where the platform's items live), so set it on the Templates view. Key tables come from the shared keys database that Dev, Test and Prod use. Until keys have moved there, export takes them from every environment, keeps Prod's fields when environments disagree and lists the difference.

**Deploy** makes the platform match the committed files: the catalog (in Prod), admins in every environment, and key tables in the shared keys database (in every environment until keys have moved there). It shows its plan first and runs only that plan, after one confirm.

- Only platform admins can deploy, and only committed files: commit first, so what is deployed is in Git's history. Each step is recorded in the platform's audit as you, with the commit in policy notes.
- A changed policy is published as a new version; a published version never changes. A policy with a draft in progress in the platform is skipped, so a deploy never overwrites someone's draft.
- A source id is never renumbered: a file whose `source_id` doesn't match the platform stops the whole deploy before anything is written. A new source keeps the id its file gives it. Source connections stay as they are in the platform.
- Key tables are created where missing, never renamed, changed or removed (their keys stay).
- A new landing zone is made the way **New landing zone** makes it: its lakehouse in the landing workspace first, then the zone in the catalog. So the platform needs its landing workspace first (Platform > Set up > Landing zone); until then, zones are skipped and the plan says why, and so is a source whose zone can't be made. Shared is made with the landing workspace, never by a deploy. A changed zone is saved with its lakehouse kept, and people named as its readers in the platform stay. A zone in the platform with no file shows as **removed in Git** and is left alone: a deploy never removes a landing zone (remove it on the Landing zone tab once no source is in it and no waiting source request lands in it).
- A deploy never switches reads to the landing workspace. **Switch reads to the landing workspace** stays a manual step on the Landing zone tab, done once the files runs need are there.
- Your organization's policy templates are added or changed with the template operations; a file using a built-in template's id is skipped. A template in the platform with no file is left alone: a deploy never deletes a template.
- Something removed in Git is removed only when you tick it, after every other change: policies are retired (never deleted), then sources, then domains (subdomains first). A domain or source still in use is refused. Glossary terms can't be removed yet.
- If the platform refuses a step, the deploy stops there and says which step and why. Steps already done show as **same** next time.

The platform's functions must include this release's changes: if Export or Compare says they are older than the app, open Platform > Settings and choose **Finish or repair**.

### Capacity repository

A capacity repository holds one file, `capacity.toml`, with a section per capacity. Ids and names only: no keys, costs or people.

```toml
[capacities."F64 Prod"]
capacity_id = "…"
sku = "F64"            # optional: a setting left out isn't compared
region = "West Europe" # optional
state = "Active"       # optional
workspaces = [{ id = "…", name = "Sales Gold" }, "…"]   # a bare id is fine too
```

The **Capacity** tab is the drift report. It reads `capacity.toml` and what Fabric shows you (`GET /v1/capacities`, Capacity.Read.All, and `GET /v1/workspaces`, with the Workspace.ReadWrite.All granted at sign-in, used here only to read; no admin role and no new permission), matches capacities and workspaces by id, and shows for each capacity:

- **same**, or **differs**: a setting the file sets that Fabric has differently, a workspace the file lists that Fabric has on another capacity, or a workspace on the capacity that the file doesn't list anywhere.
- **not in Git**: a capacity Fabric shows you that the file doesn't have.
- **not visible to you**: a capacity in the file that Fabric doesn't show you. It isn't compared, because people see different capacities: missing from your view says nothing about whether it changed. A workspace you can't see is listed the same way. Fabric lists the capacities you can assign workspaces to.

It only reads. KratosForge never resizes, pauses, resumes or assigns a capacity, and never moves a workspace: fix drift in the Fabric admin portal, or edit `capacity.toml` and commit if Git is the one that's wrong. **Copy report** puts the report on the clipboard.

**Write Fabric's state to capacity.toml** replaces the file with what Fabric shows you now, after you confirm, as a change to review and commit. Capacities and workspaces you can't see stay in the file as they are. State is written only for a capacity whose section already sets it, so pausing or resuming one doesn't show as drift unless you ask for that. If the file has changes you haven't committed, it asks again before replacing them, and a file with sections it can't read is never overwritten: fix it first.

## Changes

The **Changes** tab lists **Conflicts**, **Staged** files (going into the next commit) and **Changes** (not staged yet). Each file has buttons to stage, unstage or discard it.

- Click a file to see its diff, **Inline** or **Side by side**, with line numbers. For a notebook, **Notebook view** compares the cells instead of the raw JSON. **Open previous version** shows the last committed copy, read-only.
- **Discard** always asks first and says what will be lost. A new file that was never committed is deleted, so the question says that too.
- **Part of a file.** In the diff, each block of changed lines has its own buttons: **Stage block** and **Discard block…** in **Changes**, **Unstage block** in **Staged**. So one commit can take a fix and leave an experiment in the same file for later; while part of a file is staged, it shows in both lists. A block is applied only if it is still exactly what the diff showed: if the file changed since, nothing changes and the new diff is shown. **Discard block** asks first, says how many lines it throws away, and leaves the rest of the file as it is. Blocks aren't offered for a notebook in **Notebook view** (it compares cells, not lines: turn the view off to take the JSON's blocks), a new file Git doesn't track yet, a file that isn't text, or a file with conflicts; use the whole-file buttons there.
- Write a message and press **Commit** (or Ctrl+Enter). With nothing staged, the button offers to stage everything and commit.

### Secrets are checked before every commit

Before a commit, the staged files are scanned for things that are almost always secrets: private keys, storage account keys, SAS signatures, passwords in connection strings, GitHub tokens, Entra client secrets, AWS and Slack keys, sign-in tokens (often in notebook output), Teams webhook addresses, and files such as `.env`, `.pem` or `.pfx`. Notebook findings name the cell, and say when it's in a cell's output.

If something is found, nothing is committed. The dialog shows where it is, with only its first few characters, and what to do instead (usually Key Vault or a variable library). **Go back and fix it** is the default; **It isn't a secret, commit** commits anyway. Values such as `${DB_PASSWORD}` or `{{secret}}` count as placeholders, not secrets.

## Branches

The **Branches** tab lists your branches with how far each is ahead of or behind its remote branch, and the remote branches you can check out.

- **New branch** starts from the branch you're on.
- **Switch** with uncommitted changes asks what to do with them: **Put them aside** (Git stash; **Bring back** under **Changes put aside** on this tab restores them) or **Bring them with me** (only if Git can do that without overwriting anything). Your changes are never thrown away. If the switch fails, the changes put aside are restored.
- **Delete** asks first. A branch whose commits aren't merged anywhere asks a second time, naming that the commits will be lost. You can't delete the branch you're on.

## Fetch, pull and push

The buttons at the top say which service they talk to (GitHub, Azure DevOps or the server's name), because these are the only actions that leave your PC.

- **Fetch** gets the latest branches and commits without changing your files.
- **Pull** fast-forwards by default. If your branch and the remote have both moved on, it stops and says so; **Pull > Merge** then merges. Pull never rebases.
- **Push** sends your commits. A new branch shows **Publish branch**, which pushes it and links it to the remote branch. There is no force push.
- **Create pull request** and **Open on GitHub / Azure DevOps** open the service's page in your browser.

If a pull ends in a conflict, a banner says so, conflicted files are listed first, and their diff shows your side and the remote side. Fix the files, stage them and commit the merge, or **Undo the pull** (asks first) to go back to how things were.

## History

The **History** tab lists commits on the current branch. Click one for its message, author and changed files, and each file's diff. Tick two commits and **Compare the two commits** to see every file that differs. The open button next to a file shows that file as it was in that commit, read-only.

## Notebooks in a repository

A notebook saved inside a repository shows its branch and whether it has uncommitted changes next to **Save**; click it to open Source control. Your notebooks folder isn't a repository unless you make it one: **Version my notebooks** makes it one, or save notebooks in a notebooks repository (see [Notebooks in a notebooks repository](#notebooks-in-a-notebooks-repository)).

## What it doesn't do yet

- Stage single lines of a file (whole files only).
- Create a pull request inside the app (it opens the service's page).
- Rebase, cherry-pick, tags or submodules.
- Compare the capacity repository with Fabric (see **Repository kinds**).
- Connect a Fabric workspace to Git, or deploy between environments. Those are the next phases in the [Git integration plan](git-integration-plan.md).
- It hasn't been tried with many Git servers and sign-in setups yet, which is why it is tagged **Preview**.

Something went wrong? See [Troubleshooting: Source control](troubleshooting.md#source-control).
