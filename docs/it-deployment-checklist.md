# IT deployment checklist

One page to follow when you roll KratosForge out to a pilot group, from first download to the support path. Each step links to the page with the detail. Every setting, path and command named here is checked against the code by `sidecar/tests/test_it_checklist_doc.py`, so a renamed setting fails the build before this page goes stale.

## Before you start

- [ ] Read what is [not ready yet](#not-ready-yet). Pilot first.
- [ ] Check your Fabric tenant settings and your sign-in policies ([Configure Azure and Fabric](azure-setup.md#2-check-sign-in-policies)).
- [ ] Confirm sign-in works with your Conditional Access policies. On Windows the app signs in through WAM, so "Require a compliant or managed device" works on managed PCs. A policy that blocks Microsoft's developer tools also blocks the Fabric CLI app the app signs in with ([permissions](permissions.md#microsofts-fabric-sign-in-what-it-may-need-to-check)).

## Install

- [ ] Pick an update model. Self-updating is the default. Managed means `"auto_update": false`, and you deploy each build ([update models](enterprise-install.md#pick-an-update-model)).
- [ ] Download `kratosforge.exe` from the Stable release and check it ([deploy](enterprise-install.md#deploy), [check a download](install.md#check-a-download-yourself)).
- [ ] Package it per user as a Win32 app (Intune) or an application (Configuration Manager). No administrator rights are needed at run time, and the engine goes to `%LOCALAPPDATA%\FabricDesktop\engine` for each Windows user ([what you are deploying](enterprise-install.md#what-you-are-deploying)).

## Policy file

- [ ] Push `%ProgramData%\FabricDesktop\policy.json` as SYSTEM, so only administrators can change it ([deploy, step 4](enterprise-install.md#deploy)).
- [ ] Choose its keys ([all keys](azure-setup.md#4-push-settings-to-everyone)). The ones most pilots set:
    - `auto_update` and `channel` pin the update model and the release channel.
    - `allowed_tenants` restricts sign-in to your tenant's ids.
    - `show_preview` hides Preview features.
    - `allow_fabric_runs` keeps work on the PC.
    - `telemetry` turns usage data off for everyone. It's off by default.
    - `support_url` sets where Report a problem sends people.
    - `fabric_view_sign_in_hosts` is for federated sign-in (AD FS, Okta, Ping) in the Fabric pages.
    - `register_link_scheme` controls `kratosforge://` links.
- [ ] On one pilot PC, open **Settings > Help and about**. It lists any key that was misspelled or had the wrong kind of value, and says when the person using the PC can change the file. A broken file leaves the PC on the last copy it could read. A PC that never read a valid one fails closed.

## Pin a build and host the engine

- [ ] For a pinned build, set `"auto_update": false` and deploy the exe yourself ([keep the exe and the engine in step](enterprise-install.md#keep-the-exe-and-the-engine-in-step)).
- [ ] To keep PCs off GitHub, host the engine on an internal `https://` server and set `engine_download_url` to that build's folder. The app ignores the address unless only SYSTEM or Administrators can change the policy file ([host the engine yourself](enterprise-install.md#host-the-engine-yourself)).
- [ ] Know how to go back to an earlier build ([roll back](enterprise-install.md#roll-back)).

## Network

- [ ] Allow the hosts the app uses through your proxy or firewall ([network access](enterprise-install.md#network-access)).
- [ ] If your network inspects HTTPS, make sure your root certificate is in the Windows certificate store, as it is for Edge. The engine trusts that store as well as Python's own list. `REQUESTS_CA_BUNDLE` adds another certificate file ([proxies and HTTPS inspection](enterprise-install.md#proxies-and-https-inspection)).

## Unattended runs

- [ ] If schedules must run without anyone signed in, set up the headless agent on a server with a managed identity or service principal ([headless agent](agent.md)).

## Logs and support

- [ ] Point people at [Troubleshooting](troubleshooting.md#still-stuck).
- [ ] The engine's log is `%LOCALAPPDATA%\FabricDesktop\sidecar.log`. The audit trail is `%LOCALAPPDATA%\FabricDesktop\audit\audit-YYYY-MM.jsonl` ([where things are kept](install.md#where-things-are-kept-on-your-pc), [audit trail](user-guide.md#audit-trail)).
- [ ] When support asks for logs, ask for a support bundle instead of the raw log. `fabric-desktop support-bundle --out <folder>` saves a zip with tokens, email addresses, user names and ids taken out. `--preview` shows what it would hold.

## Not ready yet

These are the gaps the [enterprise readiness scorecard](audits/ENTERPRISE_READINESS.md#enterprise-deployment-10-20) lists for enterprise deployment. Plan your pilot around them:

<!-- Kept the same as "What's missing" under Enterprise deployment in audits/ENTERPRISE_READINESS.md; the test checks. -->
- The builds are unsigned.
- The MSI is new: it hasn't been tried in an Intune or Configuration Manager tenant yet, and it is unsigned like the exe.
- The policy file is advisory against a determined local user.
- Privacy statement and support terms aren't published yet.
