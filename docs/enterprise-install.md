# Enterprise install

This page is for IT teams that roll KratosForge out to many Windows PCs with Intune, Configuration Manager or Group Policy. One person trying the app needs only [Install](install.md).

> **What has been tested:** the policy keys on this page are covered by automated tests on every build. The deployment steps below have **not** been tried in an Intune or Configuration Manager tenant yet. Try them on a pilot group first.

## What you are deploying

| Part | Where it goes | How it gets there |
| --- | --- | --- |
| `kratosforge.exe` | Any folder. It is one file; there is no installer or MSI | You copy it |
| The local engine (Python and its packages, several hundred MB) | `%LOCALAPPDATA%\FabricDesktop\engine`, once per Windows user | The app downloads `fabric-desktop-engine.zip` from the release on its first start and checks its SHA-256 ([Security](https://github.com/KratosDataGod/KratosForge/blob/main/docs/security.md)) |
| `policy.json` (optional) | `%ProgramData%\FabricDesktop\policy.json` | You push it ([the keys](azure-setup.md#4-push-settings-to-everyone)) |

Nothing needs admin rights at run time, and nothing is written to the registry. The engine is per user, so a shared PC downloads it once for each person who uses the app.

## Pick an update model

| Model | Policy | Where the exe goes | Who moves people to a new build |
| --- | --- | --- | --- |
| **Self-updating** (default) | Leave out `auto_update` | A folder the user can write, such as `%LOCALAPPDATA%\Programs\FabricDesktop` | The app, at each start, from the channel's release |
| **Managed** | `"auto_update": false` | Anywhere, including `C:\Program Files\FabricDesktop` | You, by deploying a new exe |

A self-updating app replaces its own exe, so it must be in a folder the user can write. In `C:\Program Files` the update fails with "Could not move ... aside" and the app keeps running the old build. Use the managed model there.

With `"auto_update": false` the startup window says "Your organization rolls out updates", and when a newer build is out it names it, so you can see who is behind.

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

   If your tenant's sign-in goes through your own identity provider (AD FS, Okta, Ping and similar), add its host so people can sign in inside the app's Fabric pages, for example `"fabric_view_sign_in_hosts": ["adfs.contoso.com"]` ([the key](azure-setup.md#4-push-settings-to-everyone)).

5. On one pilot PC, open the app and check **Settings > Help and about**. Anything in the policy file that was misspelled or had the wrong kind of value is listed there. A broken file leaves each PC on the last copy it could read; a PC that never read a valid one fails closed, which turns off runs in Fabric, Preview features, telemetry and AI tools until the file is fixed; people can still sign in and work on their PC ([what fails closed](azure-setup.md#4-push-settings-to-everyone)).

## Keep the exe and the engine in step

The exe and the engine must come from the same build. Each build is kept as its own release, `build-<first 12 characters of its commit>`, for a while (the newest 20, and every Stable build). With `"auto_update": false` the app downloads its engine only from its own build's release, never the channel's newer one, so the exe you deploy keeps running the engine of its build:

- You can deploy any build that still has its release ([pinning a build](release-management/README.md#pinning-a-build)), not only the channel's current one.
- Once a build's release has been deleted (it fell out of the newest 20 and was never on Stable), a PC that has to download the engine (a new user or PC) is told this build no longer has an engine to download and to ask IT for the current exe. People whose engine is already installed are not affected.

Self-updating PCs download the engine from the channel's current release, and update the exe to match.

## Roll back

There is no one-click rollback yet.

- **Self-updating:** the app always moves to the channel's current build. To go back, a maintainer promotes the earlier build to Stable again, and PCs move to it at their next start.
- **Managed:** deploy the older exe. A PC that has to download the engine gets that build's engine from its `build-*` release while the release is kept (see above). Keep a copy of each exe and engine zip you deploy; installing the engine from your own copy is not supported yet.

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
| `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` | The update check and downloading the exe and the engine. Not needed after the engine is installed if `auto_update` is `false` |
| `login.microsoftonline.com` | Sign-in |
| `login.microsoft.com`, `login.windows.net`, `device.login.microsoftonline.com`, `autologon.microsoftazuread-sso.com`, `login.live.com` | Sign-in redirects in the Fabric pages embedded in the app (device checks, Seamless SSO, guests with a personal Microsoft account) |
| `api.fabric.microsoft.com` | Fabric workspaces, items, jobs and schedules |
| `onelake.dfs.fabric.microsoft.com` | Reading and writing OneLake files and tables |
| `app.fabric.microsoft.com` | The Fabric pages embedded in the app (Monitor, Fabric) |
| `app.powerbi.com` | Older Fabric item and report links in the embedded Fabric pages, which redirect through it |
| Your identity provider's hosts in `fabric_view_sign_in_hosts` | Only with federated sign-in in the Fabric view ([the key](azure-setup.md#4-push-settings-to-everyone)) |
| `api.powerbi.com` | Semantic models |
| `graph.microsoft.com` | Looking up people when granting access |
| `onelake.table.fabric.microsoft.com` | Listing OneLake tables and their schemas |
| Your Git hosts (`dev.azure.com`, `github.com`, `gitlab.com`, `bitbucket.org` or your own server) | Only if people connect notebooks to a repository |
| `*.vault.azure.net` | Only if notebook code reads Key Vault secrets with `notebookutils.credentials.getSecret` |
| Your Teams incoming webhook host | Only if someone sets up Teams alerts |
| The `IngestionEndpoint` host of the usage data connection string, such as `westeurope-5.in.applicationinsights.azure.com` | Only if usage data is on ([Usage data](telemetry.md)). `"telemetry": false` in the policy file turns it off for everyone |

The list comes from the hosts named in the source code. Notebooks run on the PC, so any host their own code calls is reached from the PC too. Microsoft can redirect sign-in or downloads to other hosts, so confirm with a pilot PC behind your proxy.

## Uninstall

Remove the packaged app, which deletes the exe. Each user's `%LOCALAPPDATA%\FabricDesktop` (engine, notebooks, settings, sign-in cache) stays until you remove it; a cleanup script can delete it at sign-out. Schedules on a PC live in Windows Task Scheduler under the `FabricDesktop` folder and should be removed first. Nothing in Fabric is removed. More in [Install: Uninstall](install.md#uninstall).
