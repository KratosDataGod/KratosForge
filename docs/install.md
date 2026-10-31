# Install KratosForge

This page takes you from nothing to a running app on one Windows PC. There is no app registration to set up: you sign in with your work account and that's it. If you look after Fabric for a whole organization, [Configure Azure and Fabric](azure-setup.md) lists the few tenant settings worth checking.

> **Before you start:** KratosForge is new. The parts that only talk to your own PC are tested on every build. Several features that talk to Fabric are marked **Preview** in the app because nobody has tried them on a live Fabric tenant yet. See [What is tested and what isn't](README.md#what-is-tested-and-what-isnt).

## What you need

| You need | Why |
| --- | --- |
| Windows 10 or 11 (64-bit) | The desktop app is Windows only. The [headless agent](agent.md) also runs on Linux. |
| Microsoft Edge WebView2 Runtime | The app's window is WebView2. It is already on every up-to-date Windows 11 and most Windows 10 PCs. If the app opens a blank window, install it from [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/). |
| A work account with Microsoft Fabric | You sign in with the same account you use at `app.fabric.microsoft.com`. |
| A Fabric capacity on your workspaces | Only for creating lakehouses and for the features that run in Fabric. Work that runs on your PC starts no Fabric job; only its OneLake reads and writes use capacity. |
| Disk space | The engine takes several hundred MB, plus local copies of the tables you work on. |

You do **not** need Python, Spark, Java or admin rights on the PC. The app brings its own engine.

## Download and start

1. Download **kratosforge.exe** from the [Stable release](https://github.com/KratosDataGod/KratosForge/releases/tag/stable) (a release made before the name changed has it as `fabric-desktop.exe`, the same app, which takes the new name after its first update).
2. Put it anywhere you like, for example a `Tools` folder or your Desktop. It is a single file; there is no installer.
3. Double-click it.
4. **Windows SmartScreen** may say "Windows protected your PC" because the exe isn't code-signed yet. Choose **More info > Run anyway**. You only see this the first time.
5. The first start installs the local engine. A **Checking components** window shows three lines:

   | Line | What happens |
   | --- | --- |
   | KratosForge | Checks GitHub for a newer build on your channel and installs it straight away, then restarts. |
   | Python | Comes with the engine. Nothing to install. |
   | Local engine | Downloads `fabric-desktop-engine.zip` (Python plus every package) and unpacks it into `%LOCALAPPDATA%\FabricDesktop\engine`. |

   This takes a minute or two the first time. Later starts go straight to Home unless something is updating. Most updates change only the engine's own code, so they download a few MB (`fabric-desktop-engine-update.zip`) rather than the whole engine; the whole engine is downloaded again only when Python or a package changes. The engine is always the one built from the same commit as the exe, so the two match; an engine-only update is swapped into a copy of the installed engine (hard links, so no extra space).
6. **Sign in.** Click **Sign in** on Home (or the **?** circle at the top right). Windows asks which work account to use, or signs you in with the one you use on Windows. There is nothing to paste and no app ID to ask for; KratosForge signs in through Microsoft's own Fabric sign-in, and only ever does what your own Fabric access allows ([Permissions](permissions.md)).

That's it. Home shows a **Get started** list until you set up a metadata project. Go on to the [user guide](user-guide.md).

## If Windows blocks the engine

On some PCs Windows refuses to load parts of the engine. The startup window then says **Windows Smart App Control blocked it** or **An App Control policy on this PC blocked it**, and names the blocked packages.

This isn't a broken install, so reinstalling won't help. The engine's Python packages (pyarrow, Polars, DuckDB and others) aren't code-signed by their makers, and these Windows features only let signed code load.

| What blocked it | What to do |
| --- | --- |
| **Smart App Control** (Windows 11 on a home or personal PC) | It can't allow just one app. Either use a PC without it, or turn it off in **Windows Security > App & browser control > Smart App Control settings** and click **Retry**. Some Windows versions can't turn it back on without a reset, so decide that first. |
| **An App Control policy** (set by your organization) | Ask IT to allow the Python packages in the folder the window names. They see each block in **Event Viewer > Microsoft > Windows > CodeIntegrity > Operational**. Then click **Retry**. |

The lasting fix is code signing: CI signs every file when the repository has an Azure Trusted Signing account set up ([Code signing](https://github.com/KratosDataGod/KratosForge/blob/main/CONTRIBUTING.md#code-signing)). That isn't in place yet.

## Updates

KratosForge updates itself. Each time it starts it asks GitHub whether a newer build is out on your channel. If one is, it downloads it, checks it is the right file, swaps the exe in place and restarts. The account menu shows which build is running. Then a full-window splash shows what changed (you can turn it off in Settings > Updates). You never run an old build by accident.

| Channel | Who it's for | What you get |
| --- | --- | --- |
| **Stable** (default) | Everyone | Builds a maintainer has tried and promoted with the **Promote to stable** workflow. |
| **Preview** | Testers | Every build of `main` as soon as CI is green. Things may change or break. |

Change it in **Settings > Updates**. The change takes effect the next time the app starts. An organization can fix the channel for everyone with a policy file, or turn self-updates off and roll out builds itself ([Enterprise install](enterprise-install.md)).

To update without waiting for the next start, choose **Check for updates** in the account menu or in **Settings > Updates**. It runs the same check, download and swap on demand, and its **Download manually** link opens the channel's release page ([Preview](https://github.com/KratosDataGod/KratosForge/releases/tag/latest), [Stable](https://github.com/KratosDataGod/KratosForge/releases/tag/stable)).

The update check uses GitHub's public API, which allows 60 checks an hour from one internet address. In a large office behind one address you may see "Could not check for a newer build" now and then. The app keeps working on the build you have and checks again at the next start.

## Check a download yourself

Every build of `main` comes with proof of where it came from and a list of what is inside it. Security teams can check both before allowing the app.

- **Build provenance.** GitHub signs a statement that `kratosforge.exe` and `fabric-desktop-engine.zip` were built by this repository's CI workflow from a named commit. With the [GitHub CLI](https://cli.github.com/), check a file you downloaded:

    ```powershell
    gh attestation verify kratosforge.exe -R KratosDataGod/KratosForge
    ```

    It prints the workflow and commit that built it, or fails if the file was changed. A file promoted to Stable is the same file, so the same check works. Builds made before the repository was renamed (October 2026) name the old repository, so check those with `-R KratosDataGod/fabric-desktop`.

- **Software bill of materials.** The Preview (`latest`) release carries `sbom-engine.cdx.json` (every Python package in the engine, read from the engine itself) and `sbom-app.cdx.json` (every Rust crate in the exe), in CycloneDX format. Most vulnerability scanners and asset tools read it. One piece of third-party code ships inside the engine package instead of as its own package, so it isn't a separate SBOM entry: the Apache Ossie Microsoft converter (Apache License 2.0, upstream commit `82b9eff`, in `fabric_desktop/_vendor/ossie_microsoft` with its LICENSE and NOTICE).

- **Microsoft's SQL driver.** The engine includes `mssql-python` (MIT), which carries Microsoft ODBC Driver 18 for SQL Server and the Visual C++ runtime (`msvcp140.dll`) under Microsoft's own license terms. Their license files are in the engine at `Lib\site-packages\mssql_python_odbc\libs\windows\x64\`. Nothing connects to a warehouse until you run a warehouse query.

- **Vulnerability scanning.** CI checks the engine's pinned Python packages (`pip-audit`) and the exe's Rust crates (`cargo audit`) on every pull request.

These prove where a file came from; they don't replace code signing, which is still being set up ([If Windows blocks the engine](#if-windows-blocks-the-engine)).

## Where things are kept on your PC

| Path | What it is |
| --- | --- |
| The folder you put `kratosforge.exe` in | The app. The update replaces this file in place. A copy saved as `fabric-desktop.exe` (its name before KratosForge) renames itself to `kratosforge.exe` after its next update, and its shortcuts follow ([Legacy names](kratosforge.md#the-program-file)). |
| `%LOCALAPPDATA%\FabricDesktop\engine` | The local engine: Python and its packages. |
| `%LOCALAPPDATA%\FabricDesktop\notebooks` | Your notebooks, as `.ipynb` files. Back them up with the rest of this folder ([Back up and restore](backup.md)) or keep them in Git. |
| `%LOCALAPPDATA%\FabricDesktop\msal_cache.bin` | Your sign-in token cache, encrypted for your Windows account. Delete it to force a fresh sign-in. |
| `%LOCALAPPDATA%\FabricDesktop\fabric-view-signin.bin` | The saved sign-in of the embedded Fabric pages, encrypted for your Windows account. |
| `%LOCALAPPDATA%\FabricDesktop\app-settings.json` | What you chose in Settings. |
| `%LOCALAPPDATA%\FabricDesktop\sidecar.log` | The engine's log. Attach it to a problem report if asked. |
| `%LOCALAPPDATA%\FabricDesktop\local-data\` | Local data projects (Preview): each project's settings, registered folders and database. See [Local data projects](local-data.md). |
| `%LOCALAPPDATA%\FabricDesktop\usage_imports\` | Measured usage you imported from Capacity Metrics app and Azure Cost Management exports (Preview), one file per import. See [Measured usage](finops/usage-import.md). |
| `%LOCALAPPDATA%\FabricDesktop\audit\audit-YYYY-MM.jsonl` | The audit trail: one line for each thing you ran or changed. See [Audit trail](user-guide.md#audit-trail). |
| `%LOCALAPPDATA%\FabricDesktop\files`, `onelake-cache` | Local copies of lakehouse data: files notebooks and the metadata framework read. Deleted when unused for 30 days (your organization can change that), or any time with `fabric-desktop clean-local`. |
| `%LOCALAPPDATA%\FabricDesktop\staging` | Nothing, normally. Each table write checks its schema in an empty table here and deletes it straight after; full copies older builds kept are deleted on that table's next write. A notebook's write kept on this PC for a background upload (`staging\_writes`) is deleted when its upload ends, succeeded, failed or cancelled; one left by an engine ended mid-upload goes a day later, whatever the retention setting. Skip unchanged keeps nothing here: its fingerprint is in the table's Delta log in OneLake. **Settings > Advanced > OneLake writes > Files on this PC** shows what's here, opens the folder and has **Delete left-overs**. |
| `%TEMP%\tpch_*` | Nothing, normally. The TPC-H data notebook generates its data here and deletes it when its write cell ends, whether or not the writes worked, or when the engine closes. A folder is only left when the engine was ended mid-run; **Settings > Advanced > OneLake writes > Files on this PC** lists it, opens it and deletes it. |
| `%LOCALAPPDATA%\FabricDesktop\integrations\` | External AI tools: connected tools and approvals (`integrations.sqlite`) and their audit trail (`audit\audit-YYYY-MM.jsonl`, one line per request a tool made). See [Connect your AI tool](ai/README.md). |
| `%LOCALAPPDATA%\FabricDesktop\*.json`, `*.jsonl` | Run history, favorites, pinned workspaces, schedules on this PC, the Estate X-ray's last scan. |
| `%ProgramData%\FabricDesktop\policy.json` | Settings your organization fixed, if any. |
| `Documents\FabricDesktop\metadata` | The default folder for a new metadata project. You can pick another, and you should keep it in Git. |

## Uninstall

1. If you added schedules on this PC, remove them in **Monitor > KratosForge > Schedules** first. They live in Windows Task Scheduler under the `FabricDesktop` folder.
2. Close the app and delete `kratosforge.exe` (or `fabric-desktop.exe` on a copy that kept that name).
3. Delete `%LOCALAPPDATA%\FabricDesktop` to remove the engine, your notebooks, settings and sign-in. Copy the `notebooks` folder somewhere first if you want to keep them.

Nothing in Fabric is removed. Lakehouses, tables and notebooks you published stay where they are.

## For developers: run from source

To work on KratosForge itself, see [CONTRIBUTING.md](https://github.com/KratosDataGod/KratosForge/blob/main/CONTRIBUTING.md). In short:

```powershell
cd sidecar
python -m pip install -e ".[dev]"
python -m pytest -q                                          # tests, no Fabric needed
$env:FABRIC_DESKTOP_PYTHON = (Get-Command python).Source    # the app uses this Python
cd ..\app\src-tauri
cargo run                                                    # needs Rust and the Tauri prerequisites
```

Builds you make yourself skip the update check and leave the sidecar as you installed it.

To run the sidecar with a Python of your own instead of the app's engine, set `FABRIC_DESKTOP_PYTHON` to its full path and pip-install the sidecar there. With it set, the startup check pip-installs the sidecar into that Python instead (it must be 3.12 or later), and leaves an editable (`pip install -e`) install alone.
