# Personal data on a PC: inventory for privacy reviews

This page is for IT and privacy teams reviewing KratosForge. It lists every place the app stores **personal data**: names, sign-in names, Entra ids, Windows user names, machine names, pictures and free text people type. It says what the data is, why the app keeps it, how long it stays, and how to remove it. Paths are under `%LOCALAPPDATA%\FabricDesktop` unless they say otherwise. [Where things are kept on your PC](install.md#where-things-are-kept-on-your-pc) lists every file, and [Security model](security.md) covers tokens and encryption.

KratosForge has no service of its own. Nothing on this page leaves the PC unless a row says so. **UNVERIFIED** marks a statement the app's code can't confirm, because it depends on something outside the app.

## Summary

- **Who did what.** The audit trails record the signed-in account, the tenant id, the Windows user name and the PC's name on every line. This is the most complete personal record on the PC, and it is kept until someone deletes it ([Audit trail](user-guide.md#audit-trail)).
- **Sign-in.** The token cache holds the account's sign-in name and tokens, encrypted for the Windows account. Sign-in names are never stored in plain folder names: the Profile folder is a hash of the sign-in name.
- **What people type.** Notebook code and comments, saved SQL, Profile details and goals, and notes on risk acknowledgements can hold anything a person types.
- **Copies of lakehouse data.** File previews, staged writes, the download cache and replay bundle copies can hold personal data from your tables. They are deleted after `local_retention_days` ([Configure Azure and Fabric](azure-setup.md)). Local tables, notebook outputs and exports stay until someone deletes them.
- **Usage data** is off by default. When it's on, it holds only counts per action and a random install id, with no names, accounts or tenants ([Usage data](telemetry.md)).

## Inventory

| What | Personal data in it | Why | How long | Remove it |
| --- | --- | --- | --- | --- |
| `audit\audit-YYYY-MM.jsonl` | `who` (signed-in account), `tenant`, `host` (PC name), `os_user` (Windows user name), and the ids and names of what was run. Never code, SQL or data | Evidence of who ran or changed what | Kept until deleted, or `audit_retention_days` if IT sets it | Delete files. Uninstall with `-RemoveData` keeps them unless `-RemoveAuditTrail` is added |
| `integrations\audit\` | Host, Windows user name, the AI tool's name, and each request's arguments: ids, names and numbers, with code, SQL and long text shown as `(given)` and anything credential-shaped removed | Evidence for AI tool use | Kept until deleted, or `audit_retention_days` if IT sets it | As above |
| `integrations\integrations.sqlite` | Connected tools' names; for each approval request: the tool, a summary, and the request's arguments (up to 20,000 characters, which can hold names or text the tool sent) | Asking you before a tool acts | Requests that ended are deleted after 90 days | **Settings > Integrations**, or delete the file |
| `msal_cache.bin`, `fabric-view-signin.bin`, `fabric-view\`, `%LOCALAPPDATA%\com.kratosbi.fabricdesktop` | Sign-in name, Entra object and tenant ids, tokens, and the browser profiles' cookies | Staying signed in | Until sign-out | Sign out. Uninstall always removes them |
| `profile\<hash>\profile.json`, `avatar.png` | Display name, role and technologies the person entered; recorded accomplishments (notebook and check names); goals; report summary; a picture (re-encoded, so photo metadata such as location is dropped) | Profile page | Until deleted | **Profile > Delete my profile data** |
| Notebooks (`notebooks\*.ipynb`) | Code, outputs (which can show table rows), and comments. Each comment stores the author's sign-in name and Entra object id, and they travel with the notebook into Git | The person's own work | Until deleted | Delete the notebook. Copies in Git are the repository's to remove |
| `notebook-sync\<hash>\*.json` | The cells of a notebook as it was last opened or saved here (code, Markdown and cell settings; no outputs) | Merging a notebook changed in VS Code and here, cell by cell | The last 4 per notebook; dropped when the notebook is renamed or deleted | Delete the folder |
| `history.jsonl`, `schedule_runs.jsonl`, `fabric_runs.jsonl`, `side_by_side.jsonl`, `proofs.jsonl`, `validations.json` | Notebook, table and workspace names, and error text with secrets removed. **UNVERIFIED:** error text comes from Fabric and libraries, so it can echo values it was given | Run history | Capped (newest rows) | Delete the files |
| `receipts\` | Each proof's receipt names the signed-in account and tenant | A record of a run others can check | Kept until deleted, or `receipt_retention_days` if IT sets it | Delete the folder |
| `risk_acknowledgements.jsonl` | Signed-in account and the person's note (up to 500 characters) | A record that someone saw a risk check's warnings and went ahead | Kept until deleted | Delete the file |
| `organizations.json` | Tenant ids of organizations a guest account signed in to, the domain typed for each, and when | Recent organizations in **Switch organization** | The newest 5 | Delete the file |
| `workloads.sqlite` | Workload owners as people entered them, who changed an assignment and when, and who ran actions | Workload ownership and actions | Actions: newest 10,000. Assignment history: kept | Delete the file |
| `platform_outbox.sqlite`, `alerts_outbox.sqlite` | Notice titles and recipients (sign-in names as given, or roles); alert lines | Sending Teams and email notices | Messages waiting: 3 days and at most 500. Open notices: kept until they close. A closed notice's title and recipients: deleted when it closes. Sent ledger: closed and one-off entries deleted after 365 days | Delete the files |
| `alerts_outbox.sqlite.sent-not-removed.json` (and the same beside each outbox below) | Only row ids and times: no names or text | Keeping a notice already sent from being sent again while its outbox file was busy | Removed once the rows are removed | Delete the file |
| `schedule_alerts.sqlite3` | Alert titles and lines for this PC's schedules: schedule, notebook and table names, and error text with secrets removed | Sending a schedule's alert when Teams was down | Messages waiting: 3 days and at most 500 | Delete the file |
| `optimize\outbox.sqlite` | Optimize and Vacuum results to send: table and workspace names | Telling the people a table maintenance run concerns | Messages waiting: 3 days and at most 500 | Delete the file |
| `platform\platform-<env>.sqlite`, `platform\keys.sqlite` (only when no platform in Fabric is set) | The same as the platform in Fabric holds: requests, approvals, owners and change history with sign-in names | Trying the platform on this PC | Kept until deleted | Delete the folder |
| Incident list (`incidents/incidents.json` in each environment's control folder, on this PC or in OneLake) | Who opened, acknowledged, was assigned or escalated to, and notes people typed (up to 2,000 characters each), with sign-in names; incident titles name workloads and tables | Tracking problems until someone fixes them | Open incidents: kept. Resolved: the newest 200. Per incident: its first event and the newest 100 | **Monitor > Incidents** closes them; delete the file to clear the list |
| Agent home: `queue.sqlite3`, `alerts.sqlite3`, `job_alerts.sqlite3`, `shortcut_alerts.sqlite3` | Jobs: the environment, tables and the source that asked (a schedule or a person's name as given); alerts: titles and lines, with secrets removed | Running jobs and sending the agent's alerts | Alerts waiting: 3 days and at most 500. Finished jobs: `queue_retention_days` in the agent's config; queued and dead jobs stay | Delete the files with the agent stopped |
| Windows Application event log, source KratosForge (Windows only) | No personal data: a fingerprint of the audit folder's path, the trail's newest hash and its line count | A record of where the audit trail ends that the trail's folder can't rewrite | The event log's own size and retention | An administrator can clear the Application log |
| `settings.json` | Teams and flow addresses (sealed with Windows DPAPI), the mail server's host, sender and user name | Sending alerts and notices | Until changed | **Settings** |
| Windows Credential Manager `KratosForge/mail-server` | The mail server password | Sending email notices | Until removed | **Settings**, or uninstall |
| `keys\fingerprint.key` | No personal data: 32 random bytes | Keying the short fingerprints the app gives AI tools in place of role filters and members, so a guessed name or filter can't be confirmed away from this PC | Until deleted. Never in a backup. A restore or a deleted file makes a new one | Delete the file |
| `restore-safety\<time>` | Copies of whatever a restore replaced (any of the above) | Undoing a restore by hand | Newest 5 restores | Delete the folder |
| `files\`, `staging\`, `onelake-cache\`, `replays\`, `preview-files\` | Copies of lakehouse data, so whatever personal data your tables hold. Replay bundles can hold a sample of rows | Speed, and running a failed notebook again | `local_retention_days` (default 30); previews 1 hour; staged writes 1 day | `fabric-desktop clean-local` or **Settings > Advanced > OneLake writes** |
| `sql-runs\` | Query results (table data) | Paging and export on the SQL page | `sql_result_retention_hours` (default 24) | Closing the app, with 0 hours |
| `local-data\`, `usage_imports\`, `learn\`, `academy\` | Whatever the person loads or types | The person's own work | Until deleted | In the app |
| App storage in the window (`localStorage`) | SQL page history: the SQL of the last 200 runs per warehouse, and error text with values taken out | Opening a past query again | 200 runs per warehouse | **Clear history**. Uninstall removes it |
| `sidecar.log`, `sidecar.prev.log`, `integrations\http.log`, `http.prev.log`, agent `logs\agent.log` | **UNVERIFIED:** messages from libraries and Fabric can include sign-in names, paths or ids. The agent's log removes anything credential-shaped | Fixing problems | Engine: 20 MB, plus the run before's log (`sidecar.prev.log`). Local API: 5 MB, then one older copy. Agent: 20 MB and 10 older files, about 220 MB in all | Delete the files |
| `app-crash.log` | The time, build and place in the app's code of a crash. **UNVERIFIED:** a crash message can include a path, so a Windows user name | Fixing crashes | 256 KB, then it starts again | Delete the file |
| `telemetry\usage.json` (only when usage data is on) | A random install id, counts per action and error kinds. No names, accounts, tenants or paths | Usage data | Unsent days: 30 | Turn usage data off, which deletes what's waiting |
| Git configuration in a repository folder | The name and email the person sets in **Source control** | Commits | The repository's | Git |

## What leaves the PC

| What | Where to | Personal data |
| --- | --- | --- |
| Support bundle (made only when someone asks) | Downloads, for the person to send | Logs with email addresses, tenant names, user names in paths and tokens removed ([what the support bundle collects](enterprise-install.md#what-the-support-bundle-collects)). **UNVERIFIED:** a name in a form those patterns don't match stays |
| Backup zip | Where the person saves it | Everything in the data folder except tokens, the sign-in cache and webhook addresses ([Back up and restore](backup.md)) |
| Replay bundle (Save) | Downloads | Notebook code, the error, and a sample of rows when the person ticks it and policy `allow_replay_samples` allows it |
| Profile export and report | Downloads | The profile |
| Notices and alerts | Teams, a flow, or your mail server | Titles, recipients and lines as the notice says |
| Notebook comments | Git, with the notebook | Author sign-in name and Entra object id |
| Usage data (opt-in) | Application Insights: your organization's connection string, or the build's | Counts only, as above |
| Platform features (your organization's platform in Fabric) | Your tenant | Requests, approvals and owners carry sign-in names. `platform_presence` (policy, off unless set) records when each person last used the platform ([Security model](security.md)). This data is in your tenant, not on the PC |

## Requests from a person

- **See it:** the files above are JSON, JSONL or SQLite on the person's own PC. `fabric-desktop audit` prints the audit trail ([Audit trail](user-guide.md#audit-trail)).
- **Delete it:** **Profile > Delete my profile data** for the profile. Everything else on the PC: close the app and run `uninstall-kratosforge.ps1 -RemoveData` ([Uninstall](install.md#uninstall)). It keeps the audit trails unless you also pass `-RemoveAuditTrail`, so decide first whether your organization has to keep them. It removes only what is on this PC for that Windows account. It leaves:
  - A headless agent's folder (`%ProgramData%\FabricDesktop\agent` or a server, with its queue, alerts and logs). IT deletes it with the agent stopped.
  - Each environment's incident list, which is shared with the team in OneLake or the project folder.
  - The Windows event log, which only an administrator can clear.
  - Copies in Git.

  There's no single "remove my data" button in the app yet. A test (`test_delete_my_data_stores.py`) checks that every store above is either removed or named here.
- **Correct it:** Profile details and settings can be edited in the app. Audit lines are hash-chained, so they can't be edited without `fabric-desktop audit --verify` showing it.
- **Data in Fabric, Git or your platform** is outside the PC: use those systems' own processes.

The review behind this page was checked against the code in November 2026.
