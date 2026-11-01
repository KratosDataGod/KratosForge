# Troubleshooting

When something goes wrong, KratosForge says what happened in a sentence and keeps the technical text in **Activity** (the bell) under **Details**. Find the sentence you saw below. If it isn't here, use **Report a problem** in the account menu: it collects the build and recent errors for you, without your data.

When a page or panel couldn't load something, the error shows in place with **Retry** and a **Details** section; **Copy** puts the technical text on the clipboard for a problem report. An error message at the bottom of the window stays while the pointer or keyboard focus is on it, and has **Retry** where trying again is safe.

## Starting the app

| What you see | What it means | What to do |
| --- | --- | --- |
| "Windows protected your PC" | SmartScreen doesn't know the exe yet because it isn't code-signed. | **More info > Run anyway**. |
| A blank white window | The WebView2 runtime is missing or broken. | Install the [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) and start again. |
| **Windows Smart App Control blocked it** or **An App Control policy on this PC blocked it** | Windows refused the engine's unsigned Python packages. Reinstalling won't help. | See [Install: If Windows blocks the engine](install.md#if-windows-blocks-the-engine). |
| "Could not check for a newer build" | GitHub didn't answer, or its limit of 60 checks an hour from your network was reached. | Nothing. The app runs the build you have and checks again next start. |
| "The update didn't take" | The new exe couldn't replace the old one, usually because another copy was still running or antivirus held the file. | Close every KratosForge window, then download `kratosforge.exe` again from the Releases page. |
| "HTTPS was refused because the server's certificate isn't trusted on this PC" | Your network inspects HTTPS and its root certificate isn't in Windows' certificate store on this PC. | Ask IT to add it to Windows, or set `REQUESTS_CA_BUNDLE` to a file that holds it ([Enterprise install: Proxies and HTTPS inspection](enterprise-install.md#proxies-and-https-inspection)). |
| "The engine could not be installed" | The engine download or unpack failed. The log is under the message. | Check the internet connection and disk space, then **Retry**. |
| "The engine download doesn't match the checksum its release gives" or "...is a different build from the one its release names" | The download didn't match what the release says it should be, so it wasn't installed. Usually a new build was being published at that moment; rarely, a proxy changed the file. | Wait a few minutes, then **Retry**. If it keeps happening, report it with the log under the message. |
| "The update channel is showing an older release ... than one this PC already saw" | The release information the app received is older than what it has already seen on your channel, usually an out-of-date copy kept by a proxy or cache. The app doesn't install it, so it never moves you back by mistake. | Nothing. The app runs the build you have and checks again next start. If it keeps happening for a day, report it. |
| "... says it was published in the future" | This PC's clock is more than 24 hours behind, so every new release looks like it comes from the future. Updates are refused until the clock is right. | Set the date and time correctly (Windows Settings > Time & language), then restart the app. |
| "... closed before it finished starting, so this build was put back" | The new build was installed but didn't open its window, so the app put the build you had back and kept running it. | Restart the app to try the update again. If it keeps happening, report it; antivirus holding the new file is a common cause. |
| "Could not read build ...'s checksum from its release" during an update | The app couldn't read the release's `version.json`, so it couldn't check the new exe and didn't install it. | Check the internet connection, then try the update again. |
| "Build ... was replaced on the update channel by build ..." | A newer build was published after the app last checked (the banner or dialog showed the older one). The app checks the channel again just before downloading and installs the build it offers now. | Nothing. |
| "The update channel is being republished" | The channel changed builds twice during one update, which happens while a release is being published. Nothing was installed. | Try again in a few minutes. |
| "The local engine stopped while doing this" | The Python engine crashed. It restarts on your next action. | Try again. If it keeps happening, report it; `%LOCALAPPDATA%\FabricDesktop\sidecar.log` has the reason. |
| "The local engine stopped answering, so the app restarted it" | A quick request (opening a notebook, reading variables, checking a running cell) had no answer for 60 seconds, so the app ended the engine instead of freezing. Notebook variables are gone. | Run the notebook's cells again. If it keeps happening, report it with the request named in the details and `%LOCALAPPDATA%\FabricDesktop\sidecar.log`. |

## Signing in

| What you see | What it means | What to do |
| --- | --- | --- |
| "Your organization's sign-in rules (Conditional Access) blocked this sign-in" | A Conditional Access policy: often "require a managed device", or one that blocks Microsoft's developer tools such as Fabric CLI. | Use a PC your organization manages, or ask IT to allow the Fabric CLI app for you ([permissions.md](permissions.md#microsofts-fabric-sign-in-what-it-may-need-to-check)). |
| "Your organization hasn't allowed this sign-in yet" | Your organization uses its own app registration and hasn't approved it, and users can't consent themselves. | Copy the link in **Settings > Account > Admin approval** and send it to a Microsoft Entra admin. |
| "Your sign-in doesn't carry the permission this feature needs" | Microsoft's sign-in doesn't cover that feature (Key Vault secrets, or finding someone in the directory), or your organization's own app lacks the permission. | **Details** names it. [permissions.md](permissions.md#what-each-feature-uses) says what IT can change. |
| "Could not grant admin consent ... does not have a subscription (or service principal)" (admin, in Entra) | The tenant has no service principal for one of the APIs. | Create it with `az ad sp create --id <id>` ([permissions.md](permissions.md#could-not-grant-admin-consent-your-organization-does-not-have-a-subscription-or-service-principal)). |
| "Your organization needs an extra sign-in step (multi-factor authentication)" | Entra asked for MFA (codes AADSTS50076, 50079, 50074 or 50072). | Sign in again and finish the step in the Microsoft window. If no window offers it, set up MFA at https://aka.ms/mfasetup first. |
| "That account doesn't belong to your organization's Fabric tenant" | You signed in with a personal or guest account the tenant doesn't know (AADSTS50020, 50034 or 90072). | Sign in with your work account. **Settings > Account > Sign out** first if Windows picked the wrong one. |
| "Windows sign-in wouldn't give KratosForge's built-in sign-in a token for this service" | Windows sign-in (WAM) answered `Status_IncorrectConfiguration`, or Entra answered AADSTS65002, for one service while the rest of the sign-in works. It has been seen for warehouse queries (Azure SQL) and warehouse Monitoring (Kusto) with Microsoft's Fabric sign-in ([#1079](https://github.com/KratosDataGod/KratosForge/issues/1079)). | Everything else keeps working. Send IT the text from **Details**. An organization's own app registration with that permission ([permissions.md](permissions.md#using-your-own-app-registration)) avoids it. |
| "Microsoft sign-in didn't finish" | Entra stopped the sign-in with a code the app doesn't explain yet. | Sign in again. If it repeats, send IT the code from **Details** (it starts with `AADSTS`). |
| Closing the sign-in window | You cancelled. | Nothing is shown and nothing changes; sign in when you're ready. |
| "Your sign-in expired" | The saved token couldn't be renewed. | Sign in again. If it repeats, close the app, delete `%LOCALAPPDATA%\FabricDesktop\msal_cache.bin`, and start again. |
| A new permission still isn't working after consent | Your saved token was issued before the permission was added. | Close the app, delete `msal_cache.bin`, start again and sign in. Or wait up to an hour. |
| The Fabric page or Monitoring hub asks you to sign in every time | The saved Fabric sign-in couldn't be restored. | Sign in once more. If it keeps asking, delete `%LOCALAPPDATA%\FabricDesktop\fabric-view-signin.bin` and sign in again. |

## Workspaces, lakehouses and data

| What you see | What it means | What to do |
| --- | --- | --- |
| Workspaces is empty | Your account has no role on any workspace. | Ask a workspace admin to add you. |
| "You don't have permission for that in this workspace" | You need a higher workspace role for that action. | Contributor or above to write; Admin to change access. |
| "Skipped N workspaces" on OneLake | Those workspaces couldn't be read with your access. | Usually fine. Ask for a role if you need them. |
| Every read or write in OneLake is refused, but Fabric works in the browser | The tenant setting **Users can access data stored in OneLake with apps external to Fabric** is off for you. | A Fabric admin turns it on ([azure-setup.md](azure-setup.md#1-check-your-fabric-tenant-settings)). |
| "The workspace's Fabric capacity is paused or full" | Creating items, or anything that runs in Fabric, needs an active capacity. | Resume or scale the capacity, or wait. Work on your PC isn't affected. |
| "Fabric doesn't accept that name" when creating a lakehouse | Lakehouse names can use only letters, numbers and underscores, and must start with a letter, so `TPC-H_Benchmark` is refused. | Use a name like `TPC_H_Benchmark`. The **New lakehouse** box now says this before anything is sent. |
| "... isn't in ... yet. Run the TPC-H data notebook" | **TPC-H benchmark** reads the tables **TPC-H data** writes, and that notebook hasn't run on the attached lakehouse. | Attach the same lakehouse to **TPC-H data**, run it once, then run **TPC-H benchmark** again. |
| "Fabric is busy and asked the app to slow down" | Fabric throttled the requests. | Wait a minute and retry. |
| "There is no file or folder at ... on this PC" | A file the app or engine expected on this PC is missing: it was moved, renamed or deleted. This is about your PC, not Fabric. | Check the path, or pick the file again. **Retry** if it was only briefly unavailable. |
| "This PC didn't allow access to ..." | Windows refused to open or write a file, usually because another program has it open or the folder is read-only. | Close the program that has the file open, or pick a folder you can write to. |
| A new table shows under **Unidentified** in Fabric | Fabric hasn't picked up a table written from outside yet. | Refresh the lakehouse in Fabric. See also [xray-fixes.md](xray-fixes.md#unidentified-tables). |
| **Endorsed items** is greyed out on **OneLake > Catalog** (and **Endorsed** in the lakehouse picker) | KratosForge reads endorsement only through Power BI's admin tenant scan, and it runs that scan only for data products' items (**Scan now** on a product's page, a platform admin who is also a Fabric administrator), so it has no endorsement for every item in the catalog, and the filter stays off. | Nothing to fix. See a data product's endorsement on its page under **Data mesh > Products** ([data-mesh.md](data-mesh.md#endorsement-and-business-terms)); for any other item, **Open Fabric's catalog** on the same page shows it in Fabric. |
| "GET /workspaces/... failed with 403 InsufficientPrivileges: ... Request id ..." | Fabric refused a request. The message gives Fabric's error code and its own reason, then what to do. | Do what it says. If you contact support, give the request id from the bell's **Details**. **Report a problem** replaces ids, so copy it from there. |
| **Couldn't list capacities** when creating a workspace | Your sign-in lacks `Capacity.Read.All` (your organization's own app registration doesn't have it). | Create the workspace in Fabric, or an admin adds it ([permissions.md](permissions.md#using-your-own-app-registration)). |

## Notebooks and SQL

| What you see | What it means | What to do |
| --- | --- | --- |
| "This notebook only runs in Fabric" | A cell uses something the PC doesn't have: RDDs, UDFs, streaming, Scala or R, or SQL MERGE/UPDATE/DELETE. The note names the cell. | Rewrite that cell, or set **Runs on** to Fabric. |
| "This notebook can't run in Fabric yet" | No lakehouse is attached, or a cell reads a path on your PC like `C:\data`. | Attach a lakehouse, or read the file from the lakehouse's `Files`. |
| A `%pip install` line is skipped | The engine's packages are fixed. | Use the packages the engine has (Polars, DuckDB, pandas, pyarrow, deltalake and more), or run the notebook in Fabric. |
| Writing a table says it is managed by the framework | The table belongs to a metadata entity. | Change the entity instead, or write to a `scratch_` table. |
| **Cancel** doesn't stop a cell | The code is stuck inside a library call that can't be interrupted. | **Restart kernel**. Variables are cleared. With **Run notebooks in their own process** on (Settings), this ends only the notebook's process; otherwise it restarts the app's engine. |
| A cell ends with "The notebook's Python process stopped (exit code ...)" | With notebooks in their own process, the cell's code (or a library it called) ended the process. | Run the cell again: it starts a fresh process, so run earlier cells first. |
| A SQL query fails on a Spark-only feature | Not every Spark SQL function has a DuckDB match. | The error names the part it couldn't run. Rewrite that part, or run the query in Fabric. |

## Metadata

| What you see | What it means | What to do |
| --- | --- | --- |
| "N problems to fix" banner | The project doesn't pass the save gate. Runs and promote stay blocked. | Fix each listed problem in its entity file. An approved exception in `exceptions.toml` is the escape hatch. |
| A run **Needs a decision** | The table's logic changed and it loads incrementally. | Choose **Apply to new rows only** or **Rebuild it and everything downstream** ([guide](metadata-guide.md#5-load-modes-and-decisions)). |
| "check ... failed on N rows, so nothing was written" | A check with `on_fail = "stop"` failed. | Open the run's details, fix the data or the rule, or change `on_fail` to `quarantine`. |
| "no files match ..." | The source pattern found no files in the layer's lakehouse `Files`. | Check the path and that files were delivered. In Dev on the PC, files go in `.local`. |
| "... has not been loaded in <env> yet" | The table this one reads hasn't been run in this environment. | Run upstream first, or **Run all**, which runs in layer order. |
| Promote says "things to do first" | The promote gate isn't met. | Each line says what: a failing test, a table whose last run isn't green on this version, a save gate problem, or a finding of a Block policy (fix it, or ask for an exception). |
| Promote to Prod says "The governance policies couldn't be read" | The platform can't be reached and there's no usable policy package on this PC, or the platform answered with an error. Prod fails closed. | Try again when the platform can be reached. Run Check now once while it can, so a policy package is kept on this PC for next time. |
| "Assign a workspace before Test can run" | The environment has no workspace. | **Environments and promote > Assign workspace**. |
| A Fabric run "stopped before it saved any results" | The runner notebook failed in Fabric. | Open it from **Monitor** to see the notebook's output in Fabric. |
| Fabric schedule or Run in Fabric fails with 403 | You aren't Contributor on the workspace, or your organization's own app registration lacks `Item.Execute.All`. | Ask for Contributor, or an admin adds the permission and you sign in again ([permissions.md](permissions.md#using-your-own-app-registration)). |
| A scheduled run on this PC didn't happen | The PC was off, asleep or nobody was signed in to Windows; or the app was signed out. | Use a Fabric schedule for runs that must happen. Monitor > KratosForge > Schedules shows each run and why it was skipped. |

## Source control

| What you see | What it means | What to do |
| --- | --- | --- |
| "Git isn't installed on this PC" | The app uses your own Git and couldn't find it. | Install [Git for Windows](https://git-scm.com/download/win) (it includes Git Credential Manager), then reopen Source control. |
| "Git couldn't sign in" | Git Credential Manager has no sign-in for that server, or it was cancelled. The app never asks for a password itself. | Run `git fetch` once in a terminal in that folder to sign in, or check **Repository settings > Signing in**. For Azure DevOps, check that your account can read the project. |
| "You don't have permission" | You're signed in, but that account can't read or push to the repository or branch. | Ask the repository owner for access, or check that Git Credential Manager is using the right account. |
| "The SSH key was refused" or "The server's SSH key isn't known yet" | Your key isn't loaded in the SSH agent, or this is the first connection to the server. The app doesn't answer SSH prompts. | Run `ssh-add` for your key, then `ssh -T git@github.com` (or your server) once in a terminal and accept the host key. |
| "The remote has commits you don't have" | Someone pushed first. | **Pull**, then push again. |
| "Your branch and the remote have both moved on" | A fast-forward pull isn't possible. | **Pull > Merge**, or keep working and merge later. |
| "This branch is protected" | The server refuses direct pushes to it. | Push to a new branch and **Create pull request**. |
| "Git needs your name and email" | No identity is set for commits. | **Repository settings > Your name on commits**. |
| "Commit blocked: this looks like a secret" | The secret check found a key, token or password in a staged file. | Move the value to Key Vault or a variable library, or **It isn't a secret, commit** if it's a false alarm. See [Secrets are checked](source-control.md#secrets-are-checked-before-every-commit). |
| "Another Git operation is still running" | A fetch, pull or push is still going in that repository. | Wait for it, or **Cancel** it in the band at the top of the page. |

## Still stuck

1. Open the bell and expand **Details** on the error.
2. **Report a problem** (account menu). It copies the build, channel, page and recent errors, and opens your organization's support contact or a new GitHub issue. Before anything is copied it takes out tokens, keys, passwords (including one in a web address such as `https://user:token@host`), Teams webhook addresses, email addresses, your Windows user name in file paths, and tenant, workspace and item ids. Table and workspace names in an error stay, so read it before you send it.
3. If you were asked for logs, make a **support bundle** instead of sending the raw log. In a terminal where the engine is installed, run `fabric-desktop support-bundle --out %USERPROFILE%\Desktop`. It saves a zip with the app and engine versions, where each setting comes from, and the last 2,000 lines of the engine's log, with the same details taken out. It also says whether your organization's policy file applies (or the last copy that could be read stands in) and which setting names it sets, never their values. A setting that holds a secret, such as a connection string, or an internal address (your help desk link, your package mirror) shows only whether it's set, and a temp folder on a network share shows only that it is one. `CONTENTS.txt` in the zip lists what's inside. Add `--preview` to see what the bundle would hold without saving it. On an agent server, add `--agent-home <its home folder>` to include the agent's log.
