# Source control (Preview)

**Source control** keeps a folder on your PC in Git without leaving Fabric Desktop by KratosBI. You can clone a repository, change notebooks and metadata files, see exactly what changed, make a branch, commit and push. It works with GitHub, Azure DevOps and any other Git server, over HTTPS or SSH.

Open it from **Source control** at the bottom of the left rail, or from the branch name in the top bar.

> **Your PC, not Fabric.** Everything on this page is Git on your PC. Commit and push change your repository; they never change a Fabric workspace. Connecting a workspace to Git is Fabric's own feature and isn't part of this page yet.

## Before you start

- **Install Git for Windows** from [git-scm.com](https://git-scm.com/download/win). It includes Git Credential Manager, which signs you in to GitHub and Azure DevOps in your browser and keeps the sign-in in Windows Credential Manager. Fabric Desktop by KratosBI uses the Git you installed and never sees your password or token.
- **For SSH**, add your key to the SSH agent (`ssh-add`) and connect to the server once in a terminal so its host key is known. The app doesn't ask for passphrases or accept host keys for you.
- **Tell Git who you are** in **Repository settings** if Git asks. The name and email are saved for that repository only.

## Getting a repository

| Choose | What it does |
| --- | --- |
| **Clone a repository** | Paste the address from GitHub or Azure DevOps (`https://…` or `git@…`). The copy goes to a new folder under `source\repos` in your user folder, or a folder you pick. Cloning runs in the background with progress and **Cancel**; a cancelled or failed clone removes only the folder it made. |
| **Open a folder** | A folder that is already a Git repository. |
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

Today the starting files are templates: the app doesn't yet read `locations.toml`, `platform.toml` or `capacity.toml`, and notebooks still save in the app's one notebooks folder. Reading notebook locations, exporting and deploying platform definitions, and the capacity drift report are the next stages in the [repositories by scope plan](git-integration-plan.md#repositories-by-scope).

## Changes

The **Changes** tab lists **Conflicts**, **Staged** files (going into the next commit) and **Changes** (not staged yet). Each file has buttons to stage, unstage or discard it.

- Click a file to see its diff, **Inline** or **Side by side**, with line numbers. For a notebook, **Notebook view** compares the cells instead of the raw JSON. **Open previous version** shows the last committed copy, read-only.
- **Discard** always asks first and says what will be lost. A new file that was never committed is deleted, so the question says that too.
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

A notebook saved inside a repository shows its branch and whether it has uncommitted changes next to **Save**; click it to open Source control. Your notebooks folder isn't a repository unless you make it one: **Version my notebooks** makes it one, or clone a repository and save notebooks there.

## What it doesn't do yet

- Stage single lines of a file (whole files only).
- Create a pull request inside the app (it opens the service's page).
- Rebase, cherry-pick, tags or submodules.
- Use `locations.toml`, deploy the platform repository, or compare the capacity repository with Fabric (see **Repository kinds**).
- Connect a Fabric workspace to Git, or deploy between environments. Those are the next phases in the [Git integration plan](git-integration-plan.md).
- It hasn't been tried with many Git servers and sign-in setups yet, which is why it is tagged **Preview**.

Something went wrong? See [Troubleshooting: Source control](troubleshooting.md#source-control).
