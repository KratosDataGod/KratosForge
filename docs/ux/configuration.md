# Configuration elimination matrix

Every setting, file and typed input a person or an administrator can supply, and what it should become. Taken at main `3bee0db` from `app_settings.py`, `settings.js`, the shell (`main.rs`, `build.rs`), the agent config and every dialog in `app/ui`.

**Recommendation** is one of:

- **Automatic**: the app knows or discovers it; remove the input.
- **Default**: keep it, with a default nobody needs to change; don't ask.
- **Optional**: shown where it's used, never required.
- **Advanced**: behind an Advanced section or policy only.
- **Mandatory**: genuinely needed and can't be inferred.
- **Picker**: keep the choice but replace typing with a list.

## Standard first run

What stands between download and first useful work today. Target: nothing but sign-in and the person's own choices.

| Step | Today | Required? | Recommendation |
| --- | --- | --- | --- |
| Download one exe, no installer | Manual | Yes | Keep |
| SmartScreen "Run anyway" | Manual, outside the app | Until code signing | Automatic once signed (PR #28, blocked on a Trusted Signing account) |
| Smart App Control or App Control blocks the engine | Manual, outside the app, on many PCs | Until code signing | Automatic once signed. Until then the startup dialog's guidance is good |
| Engine download (about 150 MB) | Automatic | Yes | Keep. Show size and time remaining |
| Self-update check | Automatic | Yes | Keep. Don't block first paint on it (UX-020) |
| Sign in | One click | For Fabric work | Keep. One prompt on Home instead of three (UX-005) |
| Sign in again inside embedded Fabric pages | Second sign-in | For Monitor's hub and the Fabric page | Investigate passing the account hint (`login_hint`) so it is silent where WAM allows. *Unverified* whether it already is |
| Pick a workspace | Not required; lists | No | Keep |
| Metadata project folder | Typed path | Only for the metadata framework | Picker, with the default path pre-filled (UX-015) |

**Result:** no tenant ID, client ID, workspace ID or connection string is needed for the standard path. This meets the brief's acceptance criterion for sign-in and browsing. The exceptions are documented below.

## Sign-in and identity

| Setting | Where | Default | Today | Recommendation | Why |
| --- | --- | --- | --- | --- | --- |
| App (client) ID | `settings.js:207-216`, `app_settings.py:71` | Microsoft's Fabric sign-in app (`auth.py:27`) | Text box in Settings > Account, shown to every user | **Advanced** | Almost nobody should change it; it's the second row a user sees. Show the row only under an Advanced section or when policy or environment sets it (UX-006) |
| `FABRIC_DESKTOP_CLIENT_ID` environment variable | `app_settings.py:206` | Unset | Overrides the Settings value silently; Save still says "Saved" | **Advanced**, and say so | Four ways to set one value (policy, env, Settings, build). When env or policy wins, disable the field and name the source (UX-006) |
| `FABRIC_DESKTOP_BUILTIN_CLIENT_ID`, `FABRIC_CLIENT_ID` (build) | `app_settings.py:117`, `build.rs:10` | Unset | Not in docs; `build.rs:8-9` comment still says "people paste one in Settings" | Document in `permissions.md`; fix the comment | Undocumented behavior |
| Authority | `auth.py:25` | `/organizations` | Fixed | **Optional** (advanced): tenant choice for guest accounts | Guests (B2B) can't pick another tenant; sovereign clouds unsupported. Document as a limit now |
| Account | `auth.py:136` | First cached account | No switch | **Optional**: account switcher in the account menu | The brief asks for multi-account support and easy switching |
| Token cache | `auth.py:86-131` | `%LOCALAPPDATA%\FabricDesktop\msal_cache.bin`, encrypted with DPAPI for the Windows account | Encrypted | **Automatic**, OS-protected | Done (HD-013) |
| Scopes | `auth.py:29-49` | Requested on first use | Automatic | Keep | Matches "request only what the operation needs" |
| `allowed_tenants` | policy, `app_settings.py:163` | Any | GUIDs typed in a policy file | **Advanced** (policy) | Admin-only; GUIDs are acceptable there. Accept tenant domain names too |
| Service principal (agent) | `agent_config.py:260-268` | None | Typed in `agent.toml` | **Optional**, agent only | Never needed for the desktop. The brief's "guided setup" for app identity applies to the agent; propose a `fabric-desktop agent init` that writes the file |

## App settings

| Setting | Where | Default | Recommendation | Note |
| --- | --- | --- | --- | --- |
| Update channel | Settings > Updates | Stable | Default | Clear, well explained |
| Show preview features | Settings | On | Default | The list of preview names in the description is 26 items long; replace with a count and a link |
| Kernel in its own process | Settings | Off | **Advanced** | Should become the default once proven (Prove program) |
| Cell time limit, kernel memory limit | Settings | 0 (none) | **Advanced** | |
| SQL page rows, time limit, memory %, concurrency, result retention | Settings > SQL queries | 1000, 10 min, 60%, 2, 24 h | **Advanced** (collapse the section) | Good defaults; five rows of numbers on the main Settings page |
| SQL temp folder | Settings, typed absolute path | App data | **Advanced**, **Picker** | |
| Usage data (telemetry) | Settings | Off | Optional | Correct: opt-in |
| Teams webhook URL | Data mesh alerts dialog; stored in plain `settings.json` | Empty | Optional | Store protected like the token cache; the agent refuses secrets in config but the desktop stores one in plain text (`dataops_service.py:322`) |
| Cost: price per CU hour, currency, CU per run | `estate.js:336-345` | 0.18 USD | **Automatic** where FINOPS can read the capacity SKU and region; keep as an override | |
| Support URL, auto_update, allow_fabric_runs, local_retention_days | Policy only | — | Advanced (policy) | Fine |
| Integration policy keys (5) | `integrations/policy.py:6-13` | — | Advanced (policy) | **Bug:** the policy check reports these documented keys as "isn't a setting" (`app_settings.py:143-145`), on Settings and in Ready to work |

**Two settings files:** `app-settings.json` (settings) and `settings.json` (project path, webhook, cost, platform, X-ray state). Only the first is documented. Recommendation: document both in `install.md#where-files-live`; in time rename `settings.json` to say what it holds (state, not settings).

## Typed inputs where the app already has a list

| Input | Where | Today | Recommendation | Owner |
| --- | --- | --- | --- | --- |
| AI tool "Only these workspace ids" | `integrations.js:271` | Comma-separated IDs, exact match | **Picker** (multi-select of workspaces). Names typed today silently block every call | AI integration (resolved thread; route via coordinator) |
| Shortcut rule allowed connections | `shortcuts.js:363` | Comma-separated connection IDs | **Picker**: `shortcut_connections` already lists them by name (`shortcuts.js:557-563`) | Shortcut center |
| Access grant to a group | `estate.js:443`, `533` | "name@contoso.com, or a group's object ID" | **Picker** with search by name (Power BI or Graph lookup) | Estate |
| Governance assignment target (table scope) | `governance.js:335` | `crm:silver.customer` typed | **Picker**: project, then entity | Governance |
| Entity source and key columns | `meta.js:222-224` | Typed, with a datalist | **Picker** from the source's columns when the source is readable | Data mesh |
| Domain owners | `mesh.js:155` | Emails typed | Picker with lookup | Data mesh |
| Source system Key Vault secret names | `platform.js:185-194` | Three free-text names, vault not named | Name the vault, check the secret exists, link to docs (none today) | Platform |
| Metadata project folder, Git open, init, clone folders, SQL temp folder | `meta.js:160`, `git.js:679-698`, `settings.js:340` | Typed paths, no folder picker anywhere | **Picker**: a native folder dialog (Tauri dialog plugin), path box kept for pasting | Shared shell |
| Schedule times | `app.js:2138` | "06:00, 18:00" typed | Time pickers with add/remove | Schedules |
| Platform policy `{env: {workspace_id, functions_id, sql_id, meta_id}}` | `platform.md:42-44` | Four GUIDs per environment | **Automatic** from `workspace_id`: the Join flow already discovers the other three (`central/service.py:95-98`) | Platform |

## Environment and build variables

| Variable | Documented? | Recommendation |
| --- | --- | --- |
| `FABRIC_DESKTOP_PYTHON` | Yes | Developer only. The startup error tells everyone to install Python or set it (`app.js:2503`), which contradicts "you don't need Python" in `install.md`. Show it only for developer builds |
| `FABRIC_DESKTOP_TELEMETRY_CONNECTION_STRING` | No | Document in `telemetry.md` |
| `FABRIC_DESKTOP_BUILTIN_CLIENT_ID`, `FABRIC_RELEASE_COMMIT` | No | Document in the contributor notes |
| `FABRIC_DESKTOP_AGENT_CONFIG`, `FABRIC_DESKTOP_HOME` | Yes (`agent.md`) | Keep |

## Summary

| Recommendation | Count | Items |
| --- | --- | --- |
| Automatic | 4 | Platform IDs from one workspace, cost from the capacity SKU, token cache protection, SmartScreen and App Control once signed |
| Picker | 9 | The typed IDs and paths above |
| Advanced | 7 | App ID, env override, kernel process, cell limits, SQL limits, SQL temp folder, authority |
| Optional | 4 | Account switcher, tenant choice for guests, Teams webhook, agent service principal |
| Mandatory | 1 | Sign-in |

Target after the backlog: **zero manual configuration for install and first run, and no raw ID typed anywhere in the desktop app.** Admin policy files and the headless agent's config may keep IDs, because admins have them and need exact control.
