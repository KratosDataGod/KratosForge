# Enterprise install

This page is for IT teams that roll KratosForge out to many Windows PCs with Intune, Configuration Manager or Group Policy. One person trying the app needs only [Install](install.md). To follow it step by step, use the [IT deployment checklist](it-deployment-checklist.md).

> **What has been tested:** the policy keys on this page are covered by automated tests on every build. The deployment steps below have **not** been tried in an Intune or Configuration Manager tenant yet. Try them on a pilot group first.

## What you are deploying

| Part | Where it goes | How it gets there |
| --- | --- | --- |
| `kratosforge.exe` | Any folder. It is one file | You copy it |
| or `kratosforge.msi` (the same exe, packaged) | `%LOCALAPPDATA%\Programs\KratosForge` for each Windows user, with a Start menu shortcut | You assign it per user in Intune or Configuration Manager ([Deploy the MSI](#deploy-the-msi)) |
| The local engine (Python and its packages, several hundred MB) | `%LOCALAPPDATA%\FabricDesktop\engine`, once per Windows user | The app downloads `fabric-desktop-engine.zip` from the release, or from [your own folder](#host-the-engine-yourself), on its first start and checks its SHA-256 ([Security](https://github.com/KratosDataGod/KratosForge/blob/main/docs/security.md)) |
| `policy.json` (optional) | `%ProgramData%\FabricDesktop\policy.json` | You push it ([the keys](azure-setup.md#4-push-settings-to-everyone)) |

Nothing needs admin rights at run time. The app writes to the registry only for the signed-in Windows user: the `kratosforge://` link registration under `HKCU\Software\Classes\kratosforge` (unless `"register_link_scheme": false`), and, when Academy exercises run in the Windows AppContainer, the profile Windows keeps for that container. The MSI adds its own uninstall entry and two values under `HKCU\Software\KratosBI\KratosForge`. The engine is per user, so a shared PC downloads it once for each person who uses the app.

## Pick an update model

| Model | Policy | Where the exe goes | Who moves people to a new build |
| --- | --- | --- | --- |
| **Self-updating** (default) | Leave out `auto_update` | A folder the user can write, such as `%LOCALAPPDATA%\Programs\FabricDesktop` | The app, at each start, from the channel's release |
| **Managed** | `"auto_update": false` | Anywhere, including `C:\Program Files\FabricDesktop` | You, by deploying a new exe |

A self-updating app replaces its own exe, so it must be in a folder the user can write. In `C:\Program Files` the update fails with "Could not move ... aside" and the app keeps running the old build. Use the managed model there.

With `"auto_update": false` the startup window says "Your organization rolls out updates", and when a newer build is out it names it, so you can see who is behind.

## Deploy the MSI

Each build's release also has `kratosforge.msi`, built by CI from the same exe the release publishes. It installs **per user** (no administrator rights, nothing in Program Files or HKLM) to `%LOCALAPPDATA%\Programs\KratosForge`, with a Start menu shortcut, and Windows lists it under Installed apps so it uninstalls cleanly. A newer MSI replaces an older one.

- In Intune, add it as a **Line-of-business app** (or a Win32 app) and assign it to **users**, not devices. Install behaviour: User. Silent install: `msiexec /i kratosforge.msi /qn`.
- That folder is the user's, so the app can still update itself there. To control versions yourself, also set `"auto_update": false` and deploy each new MSI.
- The MSI is unsigned until code signing lands, the same as the exe. Expect the same SmartScreen and policy checks.
- Each build's MSI has a higher version than the one before. Going back to an earlier build (a Stable rollback) gives a lower version, which the MSI refuses over a newer one: uninstall KratosForge first, then assign the earlier MSI. Settings and the engine stay, since they live outside the install folder.

### Silent install, uninstall and detection

These come from the MSI's source (`app/installer/kratosforge.wxs`) and how CI builds it (`.github/workflows/ci.yml`, "Build the MSI"). **UNVERIFIED:** none of these commands has been run in an Intune or Configuration Manager tenant yet.

| What | Value |
| --- | --- |
| Install, silently, for the signed-in user | `msiexec /i kratosforge.msi /qn /norestart`. Add `/l*v "%TEMP%\kratosforge-msi.log"` for a log |
| Install context | **User** (`Scope="perUser"`). Run it as the user, not as SYSTEM: as SYSTEM it would install for the SYSTEM account |
| Uninstall, silently | `msiexec /x kratosforge.msi /qn /norestart` with the same build's MSI, or `msiexec /x {ProductCode} /qn /norestart`. The product code is new for each build (WiX makes one), so read it from `Software\Microsoft\Windows\CurrentVersion\Uninstall` on a pilot PC rather than hard-coding it. Look in both HKCU and HKLM: on CI's Windows runner, whose account is an administrator, Windows Installer keeps this per-user install's entry in HKLM. An Intune line-of-business app handles this for you |
| Upgrade code (the same for every build) | `9b7d27af-a699-475e-a9e3-c3f09833ddda`. A newer MSI replaces an older one through it (`MajorUpgrade`), and the same version can be installed again |
| Version | `0.<n>.<run>` from the CI run number, so each build's MSI is higher than the one before. An older MSI over a newer install stops with "A newer KratosForge is already installed. Uninstall it first to go back to this one." |
| Installs | `%LOCALAPPDATA%\Programs\KratosForge\kratosforge.exe` and a Start menu shortcut, plus `InstallFolder` and `StartMenuShortcut` under `HKCU\Software\KratosBI\KratosForge` |
| Detection rule (Win32 app) | The registry value `HKCU\Software\KratosBI\KratosForge\InstallFolder` exists. Don't detect on the exe's size or version: a self-updating app replaces the exe, so it stops matching the MSI's copy |
| Per-machine install | Not available. The MSI is per user only; don't pass `ALLUSERS` or `MSIINSTALLPERUSER`. For one copy per PC, copy `kratosforge.exe` into `C:\Program Files\...` yourself with `"auto_update": false` ([Pick an update model](#pick-an-update-model)) |
| The exe's own switches | None for installing: `kratosforge.exe` isn't an installer, and starting it is the whole install. The only arguments it takes are a `kratosforge://` link, the ones Windows Task Scheduler passes for a schedule, and the one it passes itself after an update |
| Repair | None runs on its own. The shortcut isn't advertised, so starting the app never triggers a repair that would undo a self-update |

CI checks the MSI on a Windows runner for every build ([`app/installer/test-msi-lifecycle.ps1`](https://github.com/KratosDataGod/KratosForge/blob/main/app/installer/test-msi-lifecycle.ps1)). It installs the current Preview MSI silently and puts this build's MSI over it, which leaves one entry in Installed apps. It then tries the Preview MSI again and checks that it's refused with the message above. Last, it uninstalls silently and checks that the program folder, the shortcut and the two values are gone. A settings file, a notebook and a sign-in cache in `%LOCALAPPDATA%\FabricDesktop` stay unchanged through every step. The app isn't started. If the Preview MSI can't be downloaded or doesn't install, the upgrade and downgrade checks are skipped with a warning, so a broken Preview never blocks the build that fixes it.

**UNVERIFIED on Windows:**
- an uninstall while the app is open;
- an uninstall after the app has updated itself in that folder (a leftover `kratosforge.exe.old` would keep the folder from being removed).

Close the app first, and check the folder on a pilot PC.

The MSI removes only what it installed. The app's scheduled tasks, its `kratosforge://` link handler, its engine and every saved sign-in and token are removed by [`deploy/windows/uninstall-kratosforge.ps1`](https://github.com/KratosDataGod/KratosForge/blob/main/deploy/windows/uninstall-kratosforge.ps1), run as the user after the MSI's uninstall. It keeps the person's notebooks, settings and stores unless you pass `-RemoveData`, keeps the audit trail even then unless you also pass `-RemoveAuditTrail`, and never touches `%ProgramData%\FabricDesktop`. [Uninstall](install.md#uninstall) lists what it removes and keeps.

## Deploy

1. Download `kratosforge.exe` from the release you want: [Stable](https://github.com/KratosDataGod/KratosForge/releases/tag/stable) unless your pilot group follows Preview.
2. Check the download before you package it ([Install: Check a download yourself](https://github.com/KratosDataGod/KratosForge/blob/main/docs/install.md#check-a-download-yourself)).
3. Package it as a Win32 app (Intune) or an application (Configuration Manager) that copies the exe into the folder from the table above and adds a Start menu shortcut. Detection rule: the file exists with the size you packaged.
4. Push `policy.json` to `%ProgramData%\FabricDesktop\` so that only administrators can change it. Intune and Group Policy write it as SYSTEM, which does that. Create the folder from the same push, because a folder someone made first themselves stays theirs. **Settings > Help and about** says when the person using the PC can change the file. A typical managed file:

   ```json
   {
     "auto_update": false,
     "channel": "stable",
     "allowed_tenants": ["00000000-0000-0000-0000-000000000000"],
     "show_preview": false,
     "support_url": "https://teams.microsoft.com/l/channel/..."
   }
   ```

   Save the file as UTF-8 (with or without a byte order mark). Windows PowerShell 5's `Out-File` and `>` write UTF-16 by default, which the app can't read, so the PC treats it as a broken file (the last copy it could read applies, or it fails closed); use `Set-Content -Encoding UTF8` or `Out-File -Encoding utf8`.

   Settings come only from this file. KratosForge reads no registry policy (nothing under `HKLM\Software\Policies` or `HKCU\Software\Policies`) and has no ADMX template, so Group Policy deploys the file (a Files preference item) rather than setting values.

   If your tenant's sign-in goes through your own identity provider (AD FS, Okta, Ping and similar), add its host so people can sign in inside the app's Fabric pages, for example `"fabric_view_sign_in_hosts": ["adfs.contoso.com"]` ([the key](azure-setup.md#4-push-settings-to-everyone)).

5. On one pilot PC, open the app and check **Settings > Help and about**. Anything in the policy file that was misspelled or had the wrong kind of value is listed there. A broken file leaves each PC on the last copy it could read; a PC that never read a valid one fails closed, which turns off runs in Fabric, Preview features, telemetry and AI tools until the file is fixed; people can still sign in and work on their PC ([what fails closed](azure-setup.md#4-push-settings-to-everyone)).

## Changes to the policy file

Each time the engine starts, and each time someone opens Settings, KratosForge compares `policy.json` with what it saw last and writes any difference to the PC's [audit trail](user-guide.md#audit-trail):

| What happened | What is recorded |
|---|---|
| The first look on this PC | `policy_file_seen`, with the file's SHA-256 |
| The file changed | `policy_file_changed`, with the SHA-256 before and after |
| The file broke and the last copy that could be read applies | `policy_file_changed`, with `state` `last_good` (`fail_closed` when there's no good copy) |
| The file was removed | `policy_file_changed`, with no SHA-256 and `state` `none` |
| Nothing changed | Nothing |

Each entry says `managed: false` when the account signed in to the PC could change or replace the file at the time, which isn't how you push it. **Settings > Help and about** shows the file's SHA-256 and its last change. Compare it with `Get-FileHash` of the file you shipped. A [support bundle](troubleshooting.md) carries the same hash and last change in `about.json` (`policy_record`); making one only reads them.

An [agent](agent.md#operate) on the same machine does the same at its start and every 15 minutes, in its own audit log.

This makes a change visible; it doesn't stop one. Nothing is blocked. A local administrator can change the file, and their change looks the same as your push except for the hash. The account signed in to the PC can delete or edit the audit trail and the record of what was last seen (`%LOCALAPPDATA%\FabricDesktop\policy-seen.json`), and so could hide a change, so ship the audit files to a log store when that matters.

## Keep the exe and the engine in step

The exe and the engine must come from the same build. Each build is kept as its own release, `build-<first 12 characters of its commit>`, for a while (the newest 20, and every Stable build). With `"auto_update": false` the app downloads its engine only from its own build's release, never the channel's newer one, so the exe you deploy keeps running the engine of its build:

- You can deploy any build that still has its release ([pinning a build](release-management/README.md#pinning-a-build)), not only the channel's current one.
- Once a build's release has been deleted (it fell out of the newest 20 and was never on Stable), a PC that has to download the engine (a new user or PC) is told this build no longer has an engine to download and to ask IT for the current exe. People whose engine is already installed are not affected.

Self-updating PCs download the engine from the channel's current release, and update the exe to match.

## Host the engine yourself

On a network that can't reach GitHub, or to keep 5,000 PCs from each downloading the engine from the internet, keep it on your own web server:

1. Download the files of the build you deploy from its `build-<first 12 characters of its commit>` release: `fabric-desktop-engine.zip`, `version.json` and `fabric-desktop-engine-update.zip`. Check them as in [Install: Check a download yourself](https://github.com/KratosDataGod/KratosForge/blob/main/docs/install.md#check-a-download-yourself).
2. Put them, unchanged and with the same names, in one folder on an internal `https://` server that every PC can read without signing in. Use one folder per build, so PCs on the old exe keep finding their engine while you roll out the new one.
3. In `policy.json`, set `"auto_update": false` and `"engine_download_url"` to that folder, for example `"https://files.contoso.com/kratosforge/build-0123456789ab"`. Push the file as SYSTEM (Intune, Group Policy) or from an elevated prompt: the app ignores the address when the file or its folder is owned by anyone other than SYSTEM or Administrators, or when their permissions let anyone else change or replace the file, and then downloads from GitHub ([why](security.md#threats-and-what-stops-them)).

The app installs only an engine whose `version.json` names the app's own build and whose zip matches the checksum in it and the checksum built into the exe, and it follows redirects only to `https`. **Settings > Help and about** says when the address isn't one it will use, and why. Not yet tried on a real network.

## Keep the engine in a folder only administrators can write (Preview)

AppLocker's default rules and App Control for Business path rules allow programs only in folders standard users can't write. The engine normally lives in the user's own `%LOCALAPPDATA%\FabricDesktop\engine`, so it needs a hash rule for every build. Instead, you can install the engine yourself under `C:\Program Files`:

1. Download `fabric-desktop-engine.zip` and `version.json` from the build's `build-<first 12 characters of its commit>` release, and check the zip as in [Install: Check a download yourself](https://github.com/KratosDataGod/KratosForge/blob/main/docs/install.md#check-a-download-yourself).
2. From an elevated prompt, unpack the zip into a folder named with the full `commit` from `version.json`. For example, `python.exe` ends up at `C:\Program Files\KratosForge\engine\0123456789abcdef0123456789abcdef01234567\python.exe`. Keep the permissions `C:\Program Files` gives new folders.
3. In `policy.json`, set `"auto_update": false` and `"engine_folder": "C:\\Program Files\\KratosForge\\engine"`, the folder above the build folders. Push the file as SYSTEM or from an elevated prompt.
4. For each new exe you roll out, add its build's folder first, then deploy the exe. Remove old build folders once no PC runs their exe.

The app uses the folder only when all of these hold:
- the policy file passes the same check as for `engine_download_url`;
- the path is a full local path (no network share);
- the folder, the build's folder, `python.exe`, `pythonw.exe`, `python3*.dll`, `python3*._pth`, `python3*.zip`, `pyvenv.cfg`, `DLLs`, `Lib`, `Lib\site-packages` and each folder directly in it are owned by SYSTEM, Administrators or TrustedInstaller, and nobody else may create, change or delete anything in them;
- every folder above it, up to the drive, is owned by one of those and lets nobody else delete, rename or replace what is in it (creating new folders there is fine, as on `C:\`).

Otherwise the engine doesn't start, and the app says why. It never installs or repairs an engine there, and it never falls back to the per-user engine, because that is the one your rules block. **Settings > Help and about** shows a setting it can't use.

The app checks those files and folders, not every file below them, so leave the permissions the folders pass down unchanged. Notebook-environment packages still install in the user's profile; see `notebook_package_folder`.

This is Preview. It's a setting in the policy file, not a screen, so Settings > Preview features doesn't list it. It hasn't yet been tried on a PC with AppLocker or App Control for Business enforced.

## Roll back

There is no one-click rollback yet.

- **Self-updating:** the app always moves to the channel's current build. To go back, a maintainer promotes the earlier build to Stable again, and PCs move to it at their next start.
- **Data:** a rolled-back build opens what the newer one saved, unless the newer one raised the data format mark, in which case it refuses to start and says so ([Going back to an older build](install.md#going-back-to-an-older-build)). No build has raised it yet, and builds from before the mark (including the current Stable build and its rollback target) don't read it.
- **Managed:** deploy the older exe. A PC that has to download the engine gets that build's engine from its `build-*` release while the release is kept (see above). Keep a copy of each exe and its engine files; with [your own engine folder](#host-the-engine-yourself) a rolled-back exe gets its engine from there.

## If an update fails

| What you see | What to do |
| --- | --- |
| "Could not move ... aside" or "Could not put the new build in place" | The exe's folder isn't writable, or something kept the exe locked for more than 15 seconds (OneDrive syncing a Desktop or Documents folder, antivirus). Move the exe to a folder that isn't synced, such as `%LOCALAPPDATA%\Programs\FabricDesktop`, or set `"auto_update": false` and deploy builds yourself |
| "The update didn't take" | Download `kratosforge.exe` again from the release and replace the file |
| "The engine download doesn't match the checksum its release gives" | The download was changed or cut short on the way, or a new build was being published. Click **Retry**; if it keeps happening, a proxy may be rewriting downloads ([Security](https://github.com/KratosDataGod/KratosForge/blob/main/docs/security.md)) |
| Any other startup error | **Settings > Help and about > Report a problem** gives a report to send ([Troubleshooting](troubleshooting.md)) |

## Network access

The app talks to these hosts over HTTPS (port 443). Allow them through your proxy or firewall:

| Host | Used for |
| --- | --- |
| `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` | The update check and downloading the exe and the engine. Not needed after the engine is installed if `auto_update` is `false`. With `engine_download_url`, the engine comes from your own server instead ([Host the engine yourself](#host-the-engine-yourself)) |
| `codeload.github.com` | Only for a build without a bundled engine (built from source): the engine's source download, which `github.com` redirects to |
| `pypi.org`, `files.pythonhosted.org` | Installing Python packages: `%pip install` in notebooks and environments, DuckDB's tpch extension when `extensions.duckdb.org` is blocked, installing Databricks Connect for a runtime line (Preview, [Engines](engines.md#databricks-connect-preview)), and the engine's own packages for a build without a bundled engine. Your own package index instead, if pip is set up to use one or `dbconnect_index_url` names one |
| `extensions.duckdb.org` | Only for the TPC-H data notebook: DuckDB's tpch extension, which makes the test data ([The TPC-H benchmark](side-by-side.md#the-tpc-h-benchmark)). If it's blocked, the notebook gets the same extension from `pypi.org` |
| `login.microsoftonline.com` | Sign-in |
| `login.microsoft.com`, `login.windows.net`, `device.login.microsoftonline.com`, `autologon.microsoftazuread-sso.com`, `login.live.com` | Sign-in redirects in the Fabric pages embedded in the app (device checks, Seamless SSO, guests with a personal Microsoft account) |
| `api.fabric.microsoft.com` | Fabric workspaces, items, jobs and schedules |
| `onelake.dfs.fabric.microsoft.com` | Reading and writing OneLake files and tables |
| `app.fabric.microsoft.com` | The Fabric pages embedded in the app (Monitor, Fabric) |
| `app.powerbi.com` | Older Fabric item and report links in the embedded Fabric pages, which redirect through it |
| The Fabric and sign-in pages' own content hosts (for example `aadcdn.msauth.net` and `aadcdn.msftauth.net` for the sign-in page) | The scripts, styles and images the embedded Fabric pages load. The app filters only which pages the view opens, not what a page loads, so allow the hosts Microsoft lists for Fabric and Power BI |
| `*.datawarehouse.fabric.microsoft.com` | Only for queries and changes on a Fabric warehouse or SQL analytics endpoint (Preview, [Warehouse](warehouse.md)) |
| `*.kusto.fabric.microsoft.com` | Only for a warehouse's Monitoring tab (Preview): reading the workspace's monitoring KQL database ([Warehouse](warehouse.md#monitoring-preview)) |
| Your Databricks workspace host (`adb-<id>.<n>.azuredatabricks.net`) | Only if a connection profile runs SQL on a Databricks SQL warehouse or Spark through Databricks Connect (Preview, [Engines](engines.md#databricks-sql-warehouse-preview)) |
| `kratosforge://` links | On by default: each start, the app registers the scheme for the signed-in Windows user (`HKCU\Software\Classes\kratosforge`, nothing machine-wide). Set `"register_link_scheme": false` in `policy.json` to stop it; the app then removes its registration and opens no link listener on `127.0.0.1` ([Links into KratosForge](vscode.md#links-into-kratosforge)) |
| Your identity provider's hosts in `fabric_view_sign_in_hosts` | Only with federated sign-in in the Fabric view ([the key](azure-setup.md#4-push-settings-to-everyone)) |
| `api.powerbi.com` | Semantic models, workspace access, and reading endorsement for a Fabric administrator |
| `graph.microsoft.com` | Looking up people when granting access, and Entra ID groups on the Platform page |
| `onelake.table.fabric.microsoft.com` | Listing OneLake tables and their schemas |
| `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com`, your Azure OpenAI resource (`<name>.openai.azure.com`) or your own OpenAI-compatible server | Only if someone adds a model connection in App Studio (Preview, not in the app yet): their own key and billing, schema and metadata only unless they allow sample rows ([App Studio](architecture/app-studio.md)). Ollama connections stay on the PC (`localhost`) |
| Your Git hosts (`dev.azure.com`, `github.com`, `gitlab.com`, `bitbucket.org` or your own server) | Only if people connect notebooks to a repository |
| `*.vault.azure.net` | Only if notebook code reads Key Vault secrets with `notebookutils.credentials.getSecret` |
| Your Teams incoming webhook host | Only if someone sets up Teams alerts |
| The `IngestionEndpoint` host of the usage data connection string, such as `westeurope-5.in.applicationinsights.azure.com`, or `dc.services.visualstudio.com` when the string names none | Only if usage data is on ([Usage data](telemetry.md)). `"telemetry": false` in the policy file turns it off for everyone |

The list comes from the hosts named in the source code. Notebooks run on the PC, so any host their own code calls is reached from the PC too. Microsoft can redirect sign-in or downloads to other hosts, so confirm with a pilot PC behind your proxy.

### Proxies and HTTPS inspection

The app uses the proxy Windows uses, or `HTTPS_PROXY` when it's set. That includes a proxy auto-configuration (PAC) file, automatic detection (WPAD), and a proxy IT set for the whole PC: as the app starts, it asks Windows which proxy it uses for the Microsoft, GitHub and PyPI addresses and starts the engine with `HTTPS_PROXY` and `NO_PROXY` set to that answer, so the engine, notebooks, `pip`, delta-rs and gRPC all use it. If your network inspects HTTPS, it re-signs each site with your company's root certificate. The engine checks certificates against the Windows certificate store as well as Python's own list, and so does a notebook's own HTTPS (its kernel does the same), so once that root certificate is in Windows (as it is for Edge), sign-in and the calls to Fabric, OneLake, Graph and the Databricks REST API work. To add another certificate file on top, set `REQUESTS_CA_BUNDLE` to it. Delta table reads and writes (delta-rs) and the warehouse driver make their own HTTPS connections, which also check the Windows store. Databricks Connect (Spark on a Databricks cluster, Preview) uses gRPC, which reads a certificate file instead, so the app writes the roots Windows trusts for HTTPS, plus the public list gRPC would otherwise use, to `%LOCALAPPDATA%\FabricDesktop\runtimes\windows-roots.pem` and points `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` at it, unless you set that variable yourself. DuckDB's extension downloads, used only by the TPC-H benchmark notebook, use DuckDB's own list, and that notebook falls back to PyPI when they fail. The app never offers to turn certificate checks off. Confirm these on a pilot PC behind the proxy.

Each outbound call, what it uses, and how long it waits:

| Call | Proxy | Certificates | Gives up after |
| --- | --- | --- | --- |
| Sign-in (MSAL: browser, device code, service principal) | Windows proxy or `HTTPS_PROXY` | Windows store | 60 seconds per call to Entra; the browser or Windows prompt itself waits for the person |
| Sign-in with Windows (WAM) | Windows' own network settings | Windows store | Windows decides |
| Fabric REST API, OneLake, Graph, Power BI | Windows proxy or `HTTPS_PROXY` | Windows store | 60 seconds unless the call names its own |
| Managed identity on an Azure VM | Windows proxy or `HTTP_PROXY` | Not HTTPS | 60 seconds |
| Update check and the exe download (the shell's updater) | Windows proxy or `HTTPS_PROXY` | Windows store (Python's standard library loads it) | 5 to 120 seconds, by step |
| Engine download and engine update (the shell, with Windows' own `curl.exe`) | `HTTPS_PROXY` when it's set, else the proxy Windows uses for the download's address (its PAC file or WPAD answer, else its fixed proxy, skipping hosts on its bypass list), which the app passes to curl | Windows store (curl uses Windows' TLS) | 20 seconds to connect; stops when slower than 1 KB/s for 60 seconds. When the network stops the small engine update, the whole engine isn't tried from the same host |
| OneLake tables (delta-rs) | Windows proxy or `HTTPS_PROXY` | Windows store | The table call's own limits (`storage_options`) |
| Databricks Connect session (gRPC, Preview) | `HTTPS_PROXY`, which the app sets from Windows' proxy when you haven't (gRPC doesn't read Windows' setting itself; inferred, unverified) | Windows' HTTPS roots plus the public list, copied to a file for gRPC | gRPC's own |
| Notebook packages and Databricks Connect (`pip`) | Windows proxy or `HTTPS_PROXY` | Windows store (pip uses it) | 15 seconds per try, 3 tries per address; the whole install stops after 15 or 20 minutes |

Set up the network like this:
- A PAC file, WPAD, or a proxy set for every user (Group Policy's per-machine proxy setting) works without anything else (**UNVERIFIED** on a real PAC or WPAD network). The engine gets one proxy: the one Windows gives most of those addresses. Addresses the PAC file sends direct go in `NO_PROXY`. Other addresses, such as your Databricks or Snowflake host, private endpoints or the hosts a notebook's own code calls, get that same proxy even where the PAC file would send them direct or elsewhere: add them to `NO_PROXY` (the app keeps a `NO_PROXY` you set and adds to it). If your PAC file sends Microsoft and GitHub to different proxies, set `HTTPS_PROXY` and `NO_PROXY` as machine environment variables (Group Policy or Intune): the app then uses those and asks Windows nothing. `NO_PROXY=*` turns this off and sends the engine direct. Windows signs in to a PAC server with the person's account only for the PAC address IT set, and only when that server asks; never to one found by WPAD. When Windows can't run its PAC file or find a WPAD server, the app uses its fixed proxy, if any, as before.
- A bypass list goes to `NO_PROXY` as far as it can: `*.contoso.com`, single hosts and `10.*`-style networks. `<local>` and other patterns can't be written there, so for a proxy set for the whole PC add those hosts to `NO_PROXY` yourself.
- A proxy that asks for a Windows sign-in of its own (a 407 answer asking for NTLM, Negotiate or Kerberos) isn't supported. Allow the addresses in [Network access](#network-access) without proxy sign-in. **UNVERIFIED:** a proxy that takes Basic sign-in should work only with the user name and password written into `HTTPS_PROXY` (`http://user:password@proxy:8080`), where every program the person runs can read them, so don't do that on shared or managed PCs.
- On an Azure VM that signs in with its managed identity, add `169.254.169.254` to `NO_PROXY` or the proxy's bypass list, so the machine's identity endpoint isn't sent to the proxy.

When a call fails on the network, the error starts with one line that says what to do, then the detail:
- "HTTPS was refused because the server's certificate isn't trusted on this PC": add the root certificate to Windows.
- "The proxy server refused the connection or couldn't be reached": check the proxy address and that it doesn't ask for a sign-in of its own.
- "This PC couldn't reach the server": the PC is offline, the name didn't resolve, or the firewall refused the connection. Connect, or allow the host in [Network access](#network-access).
- "The server didn't answer in time": the proxy or firewall dropped the call without answering.

The update check and the engine download say the same in their own words, naming the host they couldn't reach. When `pip` can't reach the package index (offline, or PyPI blocked), the install log ends with a line saying so, rather than pip's own "No matching distribution found", which reads as if the package didn't exist. pip tries each address 3 times, not its default 6, so a blocked index fails within about a minute. On a PC that can't reach PyPI, use `notebook_package_folder` for notebook packages and `dbconnect_index_url` for Databricks Connect ([the keys](azure-setup.md#4-push-settings-to-everyone)). No call waits forever: `sidecar/tests/test_corporate_network.py` fails when an outbound call is written without a timeout, and runs sign-in, a Fabric call and the update check through a local proxy with a made-up company root certificate, a proxy that answers 407, and one that never answers. These tests run on Linux in CI and stand in for the Windows proxy setting, so the Windows proxy and certificate store themselves are UNVERIFIED until a pilot PC behind your proxy confirms them.

With no network, or a proxy or firewall that blocks `login.microsoftonline.com`, the app still starts and still shows who is signed in, because it reads the saved sign-in from the PC. Local notebooks, local SQL and local tables work as usual. What needs the network says so: the update check, signing in, the engine download, and anything that reads Fabric or OneLake. On Home, **Ready to work** shows you as signed in and says that Fabric couldn't be reached. A network that drops the sign-in traffic without answering makes a call wait 15 seconds at most before it says so. Sign-in then keeps trying in the background, and local work doesn't wait for it. **UNVERIFIED** on Windows: this was tested on Linux with the network blocked inside the engine.

### Environment variables

The app reads these from the PC's environment. None is needed for a normal rollout.

| Variable | Effect |
| --- | --- |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | The proxy, and the hosts that skip it, instead of the Windows proxy ([Proxies and HTTPS inspection](#proxies-and-https-inspection)) |
| `REQUESTS_CA_BUNDLE` | A certificate file trusted on top of the Windows certificate store |
| `FABRIC_DESKTOP_CLIENT_ID` | Your own app registration's ID for this PC, between the policy file's `client_id` (which wins) and the person's own setting ([Configure Azure and Fabric](azure-setup.md#3-only-if-required-your-own-app-registration)) |
| `FABRIC_DESKTOP_PYTHON` | For developers only. The app runs its engine from this Python instead of the one it downloads, and pip-installs the engine's packages into it. Don't set it on managed PCs |
| `FABRIC_DESKTOP_TELEMETRY_CONNECTION_STRING` | For maintainers only. Where usage data goes when the policy file names no `telemetry_connection_string`, instead of the destination built into the release. Ignored when the policy file turns usage data on with `"telemetry": true`. It doesn't turn usage data on; `telemetry` does |

The [headless agent](agent.md) reads its own: `FABRIC_DESKTOP_HOME`, `FABRIC_DESKTOP_AGENT_CONFIG`, and the variables `agent.toml` names for secrets.

### FIPS mode

The app runs on a PC with Windows' FIPS policy turned on ("System cryptography: Use FIPS compliant algorithms"). Windows applies the policy to its own TLS (Schannel), which the app window, Windows sign-in and the engine download (`curl.exe`) use. Those connect with FIPS-approved cipher suites, which Microsoft's and GitHub's servers offer. The engine's Python doesn't read the policy: its OpenSSL has no FIPS module, so its TLS and hashes behave as on any other PC. Everything the app uses to protect data is FIPS-approved: SHA-256 for checksums, receipts, the audit chain and tokens; HMAC-SHA256 for the editor and link handshakes; DPAPI for the saved sign-in. MD5 and SHA-1 are used only for ids, change checks and a query that asks for them (Databricks SQL's `hmac()`), and each says so (`usedforsecurity=False`), so they also work where Python's OpenSSL is in FIPS mode, such as the agent on a Linux host with FIPS turned on. The engine's lint (ruff's S324 rule) refuses one that doesn't, and `sidecar/tests/test_fips.py` runs them as a FIPS-only OpenSSL would. The engine's own libraries (DuckDB, Polars, delta-rs) don't use FIPS-validated modules. They aren't blocked by the policy, but if your rules require validated modules for all data in transit, check with your security team. **UNVERIFIED** on a Windows PC with the policy turned on.

## Application control

This covers AppLocker and App Control for Business (WDAC). It comes from reading the code and hasn't yet been tested on a PC with either enforced, so run it in audit mode on a pilot PC first.

Until releases are code signed, Windows' default rules block the app on a standard user's PC:

| What | Where | AppLocker default rules | App Control default policies |
| --- | --- | --- | --- |
| The MSI | Wherever you deploy it from | Blocked (unsigned) | Blocked |
| `kratosforge.exe`, self-updating | The user's `AppData\Local\Programs` folder | Blocked (not a default path) | Blocked |
| `kratosforge.exe`, managed | `C:\Program Files\...` | Allowed | Blocked (unsigned) |
| The engine's `python.exe`, `pythonw.exe` and packages | `%LOCALAPPDATA%\FabricDesktop\engine\<build>` | Blocked | Blocked, except files signed by the Python Software Foundation once you allow that signer |
| Packages installed into notebook environments | `%LOCALAPPDATA%\FabricDesktop\environments` | Allowed unless DLL rules are on | Blocked if they contain unsigned `.pyd` or `.dll` files |
| `%%cmd` cells | A script file in `%TEMP%` | Blocked by script rules | Not enforced |

What we suggest until signing lands:

- Use the [managed update model](#pick-an-update-model): copy `kratosforge.exe` to `C:\Program Files\KratosForge\` with `"auto_update": false`. Under App Control, add a hash rule for each build.
- Allow the engine by hash from a reference PC after the app has installed its engine once, and again for each engine build that changes packages (the release notes say when). Under AppLocker, `Get-AppLockerFileInformation` piped to `New-AppLockerPolicy -RuleType Publisher,Hash` does this. Under App Control, add a signer rule for the Python Software Foundation and `New-CIPolicy -Level FilePublisher -Fallback Hash -UserPEs` on the engine folder. A path rule on the engine folder is simpler but lets the user run anything they put there, because they can write to it.
- Or install the engine yourself in a folder only administrators can write, so a path rule covers it ([Keep the engine in a folder only administrators can write](#keep-the-engine-in-a-folder-only-administrators-can-write-preview), Preview).
- For notebook packages, use `notebook_package_folder` and allow its wheels' files by hash, or turn the features off with `"allow_notebook_packages": false` and `"allow_dbconnect_install": false`.
- Run shell commands with `%%powershell` or `!command` rather than `%%cmd`; don't allow `%TEMP%`.
- Under script enforcement PowerShell runs in Constrained Language Mode, so "also run while signed out" on a schedule can't be saved. Ordinary schedules are unaffected.

When something is blocked, the app says so. It names the engine folder when the engine's Python is blocked, names the packages when engine or notebook-environment files are blocked, and suggests `%%powershell` when a `%%cmd` cell is blocked. A notebook's own Python process, a SQL worker or the local API that the policy refuses, or a package file a cell imports, gets the same kind of line, naming AppLocker or App Control (WDAC), rather than `[WinError 1260]` or "DLL load failed". A scheduled run whose engine is blocked ends with the policy's own code, so Task Scheduler's **Last Run Result** shows it (`0x000004EC` for AppLocker). These messages are tested with Windows' error codes simulated, and are UNVERIFIED on a PC with a real policy.

## What the support bundle collects

When your help desk needs logs, ask for a support bundle: **Settings > Help and about > Save support bundle**, or `fabric-desktop support-bundle --out <folder>`. It saves a zip to the PC and never sends it anywhere. This is what it holds, from `sidecar/fabric_desktop/support_bundle.py`:

| File | What it holds |
| --- | --- |
| `about.json` | The app and engine versions, Python and Windows versions, and where each setting comes from. The policy file's state: whether it applies, or the last copy that could be read stands in, and the names of the settings it sets, never their values. The file's SHA-256 and the last change this PC recorded (`policy_record`), to compare with `Get-FileHash` of the file you shipped. Under `paths`, facts about the app's data folder that can trip programs up, without the path itself: its length, whether its name has non-ASCII letters or a space, whether it's on a network share, and whether Windows allows long paths |
| `sidecar.log` | The last 2,000 lines of the engine's log |
| `sidecar.prev.log` | The last 2,000 lines of the run before, when the engine has restarted |
| `app-crash.log` | When the app itself has crashed: the time, the build, the thread and the place in the code of each crash |
| `agent.log` | On an agent server, with `--agent-home`: the last 2,000 lines of the agent's log |
| `CONTENTS.txt` | What is in the zip and what was taken out |

Every file goes through the same redaction before it is written. It takes out:
- tokens, keys and passwords;
- email addresses;
- the tenant's `onmicrosoft.com` and SharePoint names, and the organization's name on OneDrive folders;
- user names in paths;
- tenant, workspace and item ids.

A setting that holds a secret, or an internal address such as your help desk link or package mirror, shows only whether it's set. No table data, notebook contents or sign-in cache is read. Your organization's own domain isn't recognized as personal data, so read the zip before it leaves your company.

### Crashes

KratosForge never uploads crash reports. A crash leaves its report on the PC, in the files the support bundle collects:

- **The app.** A crash in the app is written to `app-crash.log`: the time, the build, and where in the code it happened, with the first 1 KB of its message.
- **The engine.** When the engine stops on its own, the app adds a line to `sidecar.log` saying when, after how long, and with which exit code. A crash in native code, such as an access violation, is named in that line. The engine also writes a traceback of every thread to the log first: file and function names only, never a value.
- **Crash loops.** The engine restarts on the next action. But if it stops 3 times in a row within a minute of starting, KratosForge stops starting it for 5 minutes. Every action then says why, instead of starting a new engine for each one. It tries once more after the pause, or when KratosForge is restarted. Your files and settings are untouched. The support bundle can't be made while the engine won't start, so collect `sidecar.log`, `sidecar.prev.log` and `app-crash.log` from `%LOCALAPPDATA%\FabricDesktop` yourself. The app takes tokens, email addresses, ids and the user name out of `app-crash.log` as it writes it, and the engine does the same for most of what goes in `sidecar.log`. But they don't get the bundle's full redaction, so read them before they leave your company. The tests behind this plant each kind of secret in the logs, the settings and the policy file, and check that none of them reaches the zip (`sidecar/tests/test_support_bundle.py`).

### Crash dumps

KratosForge writes no crash dumps of its own. If the engine crashes, Windows Error Reporting can save a dump of its memory, and that memory can hold live sign-in tokens. Set WER by Group Policy the way your organization handles any app that signs in: turn off local dumps for `kratosforge.exe` and the engine's `python.exe` under `HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps`, or keep them in a folder only administrators can read, and set **Do not send additional data** if dumps may leave the PC.

## User profiles

KratosForge keeps everything in `%LOCALAPPDATA%\FabricDesktop`: the engine, notebooks, settings, the sign-in cache, local tables and caches. A classic roaming profile doesn't copy that folder between PCs, so on a PC that keeps the profile everything stays there, and on one that doesn't (below) the person starts again each time. Backups, notebooks and local Delta tables are tested under a profile path with spaces and non-ASCII letters, but not yet on a Windows PC with such a user name.

- **Exports and support bundles** go to the Downloads folder Windows reports. The default metadata folder is under the Documents folder Windows reports. Both follow OneDrive's known folder move and folder redirection.
- **Long paths.** The engine's tables and caches sit a few folders deep under `%LOCALAPPDATA%`, and a long table name with partition folders can pass 260 characters. Turn on **Enable Win32 long paths** (Computer Configuration > Administrative Templates > System > Filesystem), which sets `LongPathsEnabled`. The support bundle's `about.json` says whether it's on.
- **Profiles on a network share.** `kratosforge://` links and **Open in VS Code** open notebooks only on a drive letter. A `\\server\share` path is refused, so a link can't make Windows contact another host with the person's sign-in. Where Documents is redirected to a share, map it to a drive letter.

### Virtual desktops and shared PCs

On Azure Virtual Desktop, Citrix and other multi-session hosts, and on non-persistent desktops:

- **Keep the whole profile.** Use an FSLogix profile container (or anything that keeps `%LOCALAPPDATA%`). With only a classic roaming profile on a non-persistent desktop, notebooks saved in KratosForge's own folder, settings and the sign-in are gone at sign-out. Notebooks in a folder of their own (a Git repository, OneDrive) are kept wherever that folder is.
- **Size the profile disk for the engine.** The engine is about 950 MB and 25,000 files per person (a 315 MB download), plus the browser data of the app's window. A new build usually updates it with a few MB, but a new Python or package set downloads it whole. To save the download on each host, [host the engine yourself](#host-the-engine-yourself) on the same network. Excluding `FabricDesktop\engine` from the container means each new session downloads it again.
- **A full or read-only profile disk** (FSLogix can attach one read-only, for example to a second session of the same person) stops saving: the app says the disk is full, or that Windows won't let it write its data folder, and changes nothing. It doesn't fall back to another folder. When this happens as the app starts, restart it once there is room.
- **Room for the engine.** Before it downloads a whole engine, the app checks that the drive has about 1.5 GB free, and refuses with the space it found rather than fail halfway. **Ready to work** on Home has a **Your data folder** row: it warns when that drive has less than 2 GB free, so a profile disk can be grown before an update needs the room.
- **AppData on a network share.** If `%LOCALAPPDATA%` is redirected to a share (`\\server\share`, or a drive letter mapped to one), the app still runs, but notebooks, local tables and its own stores are slower there and their file locks can fail. **Your data folder** in Ready to work warns about it. Keep `%LOCALAPPDATA%` on the PC; an FSLogix profile container counts as on the PC.
- **Ports.** The app and its engine talk over pipes, and the `kratosforge://` link listener and the VS Code channel take a free port each, so any number of people can run the app on one host. The optional local API uses one fixed port (47615): on a shared host only the first person to turn it on gets it. The others are told so and can add their tools as MCP tools, which need no port.
- **Memory and CPU.** Each person runs the app's window, the engine and, with `kernel_process` (on by default), one process per open notebook (about 0.8 GB each before any data). A local query can use most of the host's free memory and every core. On a shared host, set `kernel_process`, `kernel_memory_limit_gb`, `kernel_max_sessions`, `kernel_idle_minutes` and `sql_memory_limit_percent` in the [policy file](azure-setup.md#4-push-settings-to-everyone), and leave CPU sharing to the host (fair-share CPU scheduling on multi-session Windows).
- **Schedules** live in the person's Windows Task Scheduler on that host and run only there, so on a non-persistent or pooled desktop they may not run at all. Use Fabric's own schedules there.

**UNVERIFIED:** none of this has run on an Azure Virtual Desktop, Citrix or FSLogix host yet.

## Uninstall

Remove the packaged app, which deletes the exe. Each user's `%LOCALAPPDATA%\FabricDesktop` (engine, notebooks, settings, sign-in cache) stays until you remove it; a cleanup script can delete it at sign-out. Schedules on a PC live in Windows Task Scheduler under the `FabricDesktop` folder and should be removed first. Nothing in Fabric is removed. If Academy exercises ran in the Windows AppContainer, the engine folder keeps a read permission for KratosForge's own exercise sandbox, which nothing else can use; to remove it first, choose **Remove exercise permissions** under Settings > Advanced > Exercise sandbox before uninstalling. More in [Install: Uninstall](install.md#uninstall).
