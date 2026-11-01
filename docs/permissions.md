# Permissions

KratosForge never gets more access than the person using it. It signs in as you and can only do what your own Fabric workspace roles let you do. It has no app-level (application) permissions of its own.

There are two ways it signs in:

- **Microsoft's Fabric sign-in (the default).** Nobody registers an app, grants consent or types an app ID. KratosForge signs in through the same Microsoft app that Fabric's own command-line tool (Fabric CLI, `fab auth login`) uses, which every Microsoft Entra tenant already trusts for Fabric and OneLake. On Windows it signs in through Windows itself (Web Account Manager), so you are often signed in with your Windows work account and see no prompt.
- **Your organization's own app registration (optional).** For organizations whose policy says every app must be registered in their own tenant, or that want to choose the exact permissions. See [Using your own app registration](#using-your-own-app-registration).

The [headless agent](#the-headless-agent) is separate: it runs unattended, so it signs in as a managed identity or service principal.

Permissions decide what the app may read. Once data is read to the PC for a local run, OneLake data access roles, row-level and column-level security no longer apply to that copy: see [Data copied to this PC](security.md#data-copied-to-this-pc).

## Microsoft's Fabric sign-in: what IT may need to check

Nothing, in most tenants. These are the things that can stop it:

| Tenant setting or policy | What happens | What IT can do |
| --- | --- | --- |
| Fabric tenant setting **Users can access data stored in OneLake with apps external to Fabric** is off | Every OneLake read and write is refused | Turn it on, for everyone or for a group ([azure-setup.md](azure-setup.md#1-check-your-fabric-tenant-settings)) |
| Conditional Access requires a compliant or managed device | Sign-in is refused on a PC your organization doesn't manage | Nothing to change if the PC is managed: Windows sign-in passes the device check. On an unmanaged PC, use a managed one |
| Conditional Access blocks Microsoft's developer tools (Azure CLI, Fabric CLI) for some users | Sign-in is refused with AADSTS53003 | Allow the **Fabric CLI** app for KratosForge's users, or use [your own app registration](#using-your-own-app-registration) |
| Your organization doesn't allow apps it hasn't registered | Policy, not a technical block | Use [your own app registration](#using-your-own-app-registration) |

### What each feature uses

With Microsoft's sign-in, KratosForge asks each Microsoft API for its default access (`/.default`), which Microsoft sets for the Fabric CLI app. The table says which API each feature calls. A feature that needs something Microsoft's app doesn't cover says so, and still works with your own app registration.

| Feature | API | Expected with Microsoft's sign-in |
| --- | --- | --- |
| Workspaces, lakehouses, notebooks, semantic models, variable libraries | Fabric | Yes |
| Reading and writing tables and files | OneLake (Azure Storage) | Yes |
| Run in Fabric, Fabric schedules, table maintenance | Fabric | Yes |
| Capacities (creating a workspace, the cost view) | Fabric | Yes |
| OneLake shortcuts and data access roles, cloud connections | Fabric | Yes |
| [Shortcut command center](shortcuts.md) health checks (listing one entry through each shortcut) | OneLake (Azure Storage) | Yes. Nothing new is asked |
| Giving someone a workspace role by email | Power BI | Yes. No directory permission is needed |
| Adding someone to a data access role who has no role in the workspace yet | Microsoft Graph | Possibly not. Give them a workspace role first, or paste their object ID |
| The [platform](platform.md): creating its SQL database and user data functions, and calling the functions for keys and the catalog | Fabric | Expected, not yet tried. Calls use the same run access as Run in Fabric |
| Importing Fabric's domains and subdomains into the platform (**Import from Fabric** on **Data mesh > Domains**) | Fabric admin API | Only for Fabric administrators, and not yet tried. Reading the domain list needs the Fabric administrator role; everyone else is refused |
| Reading the tenant settings history (`tenant_settings_read`, Preview: Optimize > Settings history) | Fabric admin API (`GET /v1/admin/tenantsettings`) | Only for Fabric administrators, and not yet tried. Anyone else's read is recorded as unreadable. It only reads; it never changes a setting |
| Reading capacity overrides (`capacity_settings_read` with **Include capacity overrides (administrators)** ticked, Preview: Optimize > Settings history) | Fabric admin API (`GET /v1/admin/capacities/delegatedTenantSettingOverrides`) | Only for Fabric administrators, and not yet tried. Asked for only when ticked; anyone else's read of the overrides is recorded as unreadable and their capacities are still read. It only reads |
| Reading a data product's endorsement (**Scan now** on a product's page, Preview: Data mesh > Products) | Power BI admin API (`https://api.powerbi.com/v1.0/myorg/admin/workspaces/getInfo`, `scanStatus`, `scanResult`; token for `https://analysis.windows.net/powerbi/api`) | Only for Fabric administrators, and not yet tried. Asked for only when they choose Scan now; anyone else is refused and nothing is saved. It only reads, and only the workspaces the platform's products, and the semantic models and reports grouped under them, live in |
| Seeing which Fabric domain a project's workspaces are in | Fabric | Yes. Any workspace member can read it |
| The [live test](live-testing.md): creating and deleting a throwaway lakehouse, OneLake files and a Delta table in a workspace marked for tests | Fabric, OneLake (Azure Storage) | Yes. Nothing new is asked; you need Contributor or higher on the test workspace |
| Queries on a Data warehouse or SQL analytics endpoint (Preview, [SQL execution](sql-execution.md#queries-on-a-warehouse-preview)) | Azure SQL (`https://database.windows.net/user_impersonation`) | Not yet tried with the app's own sign-in. On 2026-10-27 Fabric accepted an Azure SQL token for the same account from the Azure CLI, and the [tenant run](warehouse.md#tested-on-a-tenant) passed with it. If Microsoft's app is refused, warehouse queries need your own app registration with Azure SQL Database `user_impersonation` |
| A warehouse's Monitoring tab (Preview, [Warehouse](warehouse.md#monitoring-preview)): reading the workspace's monitoring KQL database | Kusto (`https://kusto.kusto.windows.net/.default`) | Not yet tried; the first Monitoring tab read checks it. If Microsoft's app is refused, monitoring reads need your own app registration with Azure Data Explorer `user_impersonation` |
| `notebookutils.credentials.getSecret` in notebooks on the PC | Azure Key Vault | Possibly not. Needs your own app registration |
| `notebookutils.credentials.getToken` in notebooks on the PC | The API the notebook names: `storage` (Azure Storage), `pbi` (Power BI Service), `keyvault` (Azure Key Vault), `kusto` (Azure Data Explorer) or any resource URL | For Azure Storage and Power BI Service, yes. For any other API, possibly not; with your own app registration, add that API's permission |

"Expected" means Fabric CLI itself does these things with the same app. None of it has been tried from KratosForge on a live tenant yet, apart from the warehouse queries noted above.

Endorsement (Certified, Promoted) isn't shown in KratosForge's own list, because it needs an admin-only permission. A data product's page (Preview) shows its endorsement as the last tenant scan read it: a Fabric administrator who is also a platform admin chooses **Scan now**, which reads Power BI's admin tenant scan of the workspaces the platform's products live in, and the platform keeps the result so everyone else sees it without that permission ([data-mesh.md](data-mesh.md#endorsement-and-business-terms)). Business terms on a product link to Microsoft Purview; KratosForge never calls Purview (Microsoft's Fabric sign-in can't get a Purview token). Fabric's admin API is used for two reads only, both by a Fabric administrator: domain names and each domain's admins, when they choose **Import from Fabric** (the names are then kept in the platform, so nobody else needs that permission), and tenant settings, for the tenant settings history (kept in the central lakehouse, where people with access to it read the history without the admin role). For any other item, **Open Fabric's catalog** on **OneLake > Catalog** shows endorsement in Fabric; that page's **Endorsed items** filter stays off.

## Using your own app registration

You only need this if your organization requires it. Everything below is optional.

1. **Register the app.** Entra admin center: **App registrations > New registration**, any name (for example `KratosForge`). For people from one organization choose single tenant; for several, **Accounts in any organizational directory**. Under **Authentication > Add a platform > Mobile and desktop applications**, add the redirect URI `http://localhost`.
2. **Add the delegated permissions** below. **API permissions > Add a permission**, then **Microsoft APIs**, or **APIs my organization uses** if you search by name. Choose **Delegated permissions** each time.
3. **Grant admin consent** (optional). None of these permissions requires an admin, so people can consent for themselves at first sign-in if your tenant lets users consent to apps. If it doesn't, select **Grant admin consent for <your tenant>** (Global Administrator, Privileged Role Administrator or Cloud Application Administrator).
4. **Give the app's ID to KratosForge**: see [Rolling out to your organization](#rolling-out-to-your-organization).

With your own app, KratosForge signs in with the browser (not Windows sign-in) and asks for each permission the first time a feature needs it.

| API (as Entra shows it) | Permission | Needed for | Asked |
| --- | --- | --- | --- |
| Azure Key Vault | `user_impersonation` | Notebooks reading secrets with `notebookutils.credentials.getSecret` on this PC | Optional, when a notebook asks |
| Azure SQL Database | `user_impersonation` | Queries on a Data warehouse or SQL analytics endpoint (Preview, [SQL execution](sql-execution.md#queries-on-a-warehouse-preview)), and changes to a warehouse you confirm, including deploys from a warehouse project (WH-10, WH-13, same scope; Fabric's own permissions on the warehouse decide what you can change) | On first use |
| Azure Data Explorer | `user_impersonation` | Reading a workspace's monitoring KQL database on a warehouse's Monitoring tab (WH-19, Preview). Building its monitoring views creates KQL querysets with Fabric's `Item.ReadWrite.All`, which sign-in already asks for | On first use |
| Azure Storage | `user_impersonation` | Reading and writing OneLake: tables, files, the Fabric runner bundle; listing the platform's landing zone | At first sign-in |
| Microsoft Graph | `User.ReadBasic.All` | Adding someone to a data access role who has no role in the workspace yet | Optional, on first use |
| Microsoft Graph | `Group.Read.All` | Finding an existing Entra ID group by name, and **Check in Entra**, in a domain's **Access groups** (Preview). Reads only. It needs an Entra admin's consent; without it, people link a group by pasting its object ID. The scripts **Access groups** writes are run by an admin, signed in as themselves: the Graph PowerShell one connects with `Group.ReadWrite.All` and `User.Read.All`. The app never asks for those ([Entra ID groups per domain](platform.md#entra-id-groups-per-domain-preview)) | Optional, on first use |
| Power BI Service | `Capacity.Read.All` | Listing capacities when you create a workspace, the size and status of each environment's capacity (Optimize > Cost and savings, Data mesh > Environments), and reading capacities for the capacity settings history (`capacity_settings_read`, Preview: Optimize > Settings history; it only reads), and the capacity repository's drift report (`git_capacity_diff`, Preview: Source control > Capacity; it only reads) | On first use |
| Power BI Service | `Connection.Read.All` | Cloud connections when you create a shortcut, and the Data Factory Connections tab (Preview) | On first use |
| Power BI Service | `Dashboard.Read.All` | Opening a dashboard or one of its tiles in Embed Studio (Preview, not on screen yet): its embed address and tiles, and the token Power BI's embed frame uses. The calls only read, but the token Power BI's embed frame uses is your Power BI access token: it can do anything you can do in Power BI with KratosForge's other Power BI permissions, write permissions included. It stays in memory only, goes only to `app.powerbi.com` and expires in about an hour ([Power BI embedding](architecture/power-bi-embedding.md)) | On first use |
| Power BI Service | `Dataset.Read.All` | Showing a report in Embed Studio (Preview, not on screen yet): the token Power BI's embed frame uses reads the report's semantic model. The calls only read; the token is the same one as for `Dashboard.Read.All` above | On first use |
| Power BI Service | `Gateway.Read.All` | Gateway names on the Data Factory Connections tab (Preview). Declined, the tab still lists the connections | On first use |
| Power BI Service | `Item.Execute.All` | Running or scheduling notebooks, starting pipelines, copy jobs and Dataflow Gen2 refreshes from Workloads, the Fabric runner and table maintenance in Fabric; calling the platform's functions (keys, catalog) | On first use |
| Power BI Service | `Item.ReadWrite.All` | Creating, updating and (in the [live test](live-testing.md) only) deleting lakehouses, notebooks, pipelines, semantic models, variable libraries, the platform's SQL database and functions; reading a notebook's cells for its preview, for Edit on this PC and for the check before Publish ([Notebook files](notebook-files.md)); publishing and running the best practices notebook ([Semantic model best practices](semantic-model-best-practices.md)); reading pipeline run history and activity runs for Monitor ([Data Factory](orchestration/data-factory.md), Preview); reading a semantic model's definition for the Direct Lake check, the data dictionary and Apache Ossie export and import preview (Preview); creating and updating a warehouse's monitoring views as a KQL queryset, after a confirm (WH-19, Preview); creating a landing Copy job when a platform admin approves a source request ([platform.md](platform.md#copy-jobs-from-requests-preview), Preview); reading a Fabric Environment item's published libraries and Spark runtime, and its unpublished changes, for `env-drift` ([Notebooks](notebooks.md#compare-with-a-fabric-environment-item-preview), Preview) | At first sign-in |
| Power BI Service | `OneLake.ReadWrite.All` | OneLake shortcuts and data access roles | On first use |
| Power BI Service | `Report.Read.All` | Opening a report or paginated report in Embed Studio (Preview, not on screen yet): its embed address, pages and semantic model, and the token Power BI's embed frame uses. The calls only read; the token is the same one as for `Dashboard.Read.All` above | On first use |
| Power BI Service | `Tenant.Read.All` | Importing Fabric's domains and subdomains (**Import from Fabric** on **Data mesh > Domains**), reading tenant settings for the tenant settings history, and, when chosen, each capacity's tenant setting overrides for the capacity settings history; and Power BI's admin tenant scan for a data product's endorsement (**Scan now**, Preview), asked for Power BI's address (`https://analysis.windows.net/powerbi/api/Tenant.Read.All`, the same permission). Only works for a Fabric administrator; leave it out if no Fabric administrator uses KratosForge | Optional, when a Fabric administrator imports domains, reads tenant settings or scans endorsement |
| Power BI Service | `Tenant.ReadWrite.All` | Creating a domain or subdomain in Fabric (**New domain** on **Data mesh > Domains**, with **Also create it in Fabric** ticked). Only works for a Fabric administrator; leave it out if no Fabric administrator creates domains from KratosForge | Optional, when a Fabric administrator creates a domain in Fabric |
| Power BI Service | `Workspace.ReadWrite.All` | Listing and creating workspaces, putting one on a capacity, workspace roles (also by email), including when approving a workspace or workspace access request | At first sign-in |

This table is every delegated permission the app can ask for: a test fails the build when the code asks for one it doesn't list, or the table lists one no code asks for.

`notebookutils.credentials.getToken` in a notebook asks for a token for the API the notebook names, and nothing more. For `kusto`, add **Azure Data Explorer** `user_impersonation`; for a resource URL, add that API's delegated permission. Leave them out if no notebook asks.

The Fabric permissions are listed under **Power BI Service** in Entra, even though the app calls `api.fabric.microsoft.com`. Only add what your people will use: without a permission, the feature that needs it says which one, and everything else works.

### "Could not grant admin consent. Your organization does not have a subscription (or service principal)..."

Your tenant has never used one of those Microsoft APIs, so Entra has no local record (service principal) of it to grant consent against. New and developer tenants often lack Azure Storage and Azure Key Vault. Create the missing ones once, then grant consent again. With the Azure CLI, signed in as an admin (`az login --allow-no-subscriptions`):

| API | Command |
| --- | --- |
| Azure Key Vault | `az ad sp create --id cfa8b339-82a2-471a-a3c9-0fc0be7a4093` |
| Azure Storage | `az ad sp create --id e406a681-f3d4-42a8-90b6-c2b029497af1` |
| Microsoft Graph | `az ad sp create --id 00000003-0000-0000-c000-000000000000` |
| Power BI Service | `az ad sp create --id 00000009-0000-0000-c000-000000000000` |

"Already exists" or "already in use" means that one is fine. Creating them needs Application Administrator, Cloud Application Administrator or Global Administrator. If you'd rather not add Key Vault, remove its permission instead; consent then succeeds for the rest.

### After adding a permission

Sign in again so your token carries it: close the app, delete `%LOCALAPPDATA%\FabricDesktop\msal_cache.bin`, and open it again. (Otherwise it takes effect within an hour.)

## Rolling out to your organization

With Microsoft's sign-in there is nothing to roll out: people download the app and sign in. To use your own app registration instead, set its ID in one of these; the first one found wins, in this order:

| Where | How | Best for |
| --- | --- | --- |
| Policy file | `%ProgramData%\FabricDesktop\policy.json`, pushed with Intune or Group Policy (see below) | IT-managed PCs. People see "Set by your organization" and can't change it |
| `FABRIC_DESKTOP_CLIENT_ID` | An environment variable for the Windows user | Existing installs set up with `setx` |
| Settings | **Settings > Account > App (client) ID** | One person or a pilot |
| Built in | Repository variable `FABRIC_CLIENT_ID` (**Settings > Secrets and variables > Actions > Variables**); every build after that carries it | A fork of this repository built for one organization |

Leave all of them empty to use Microsoft's sign-in.

`policy.json` can also fix the update channel, hide preview features, and say where **Report a problem** goes:

```json
{
  "client_id": "00000000-0000-0000-0000-000000000000",
  "channel": "stable",
  "show_preview": false,
  "support_url": "mailto:data-platform@contoso.com"
}
```

Every key is optional; leave out `client_id` to keep Microsoft's sign-in. `channel` is `stable` or `preview`; `support_url` is an `https://` link (a Teams channel, a ticket form) or a `mailto:` address.

Four more keys are for control rather than convenience:

| Key | Effect |
| --- | --- |
| `allow_fabric_runs: false` | The app doesn't start jobs on Fabric capacity (Run in Fabric, Fabric schedules, Optimize, the Fabric side of a proof). People can still turn Fabric schedules off. It doesn't change what a person's own Fabric roles allow in the browser |
| `allow_optimize_lakehouse: false` | The app doesn't create, connect to or write to shared optimization lakehouses ([Optimize](optimize/README.md)) |
| `allowed_tenants: ["<tenant id>", ...]` | Accounts from any other tenant are signed out again as soon as they sign in. With Microsoft's sign-in this is the only way to keep the app to your own tenant, because the sign-in itself is multitenant |
| `auto_update: false` | The app never replaces itself; you roll out each build. See [Enterprise install](enterprise-install.md) |

An invalid file or key fails closed: a file that can't be read leaves each PC on the last copy it could read, or, on a PC that never read a valid one, turns off runs in Fabric, Preview features, telemetry and AI tools until it's fixed. Sign-in still works there, and `allowed_tenants` isn't enforced until the file is fixed, so check the file on a pilot PC first. A wrongly typed restriction takes its most restrictive value. The full table, what fails closed, and how to check the file was read, is in [Configure Azure and Fabric](azure-setup.md#4-push-settings-to-everyone).

**People from more than one organization, with your own app.** Make the registration multitenant. If an organization doesn't let users consent, its Microsoft Entra admin approves the app once with the link in **Settings > Account > Admin approval** (`https://login.microsoftonline.com/organizations/adminconsent?client_id=<application-client-id>`).

**Updates.** The newest green build of `main` goes to the Preview channel at once; when merges land close together, only the build of the last one is published, one publish at a time. The Stable channel, which everyone gets by default, only moves when someone runs **Actions > Promote to stable** with the notes people will see as What's new. With `auto_update: false` nothing moves until you roll out a new exe.

## The headless agent

The agent runs with nobody signed in, so it can't use a person's sign-in. A **managed identity** needs no app registration at all: use it when the agent runs on an Azure VM, Azure Arc server or container. Elsewhere it needs a service principal (an app registration with a certificate). Either way its registration needs no entries on the **API permissions** page: Fabric gives app identities what their workspace roles allow. Set up, in alphabetical order:

| Where | Setting | Why |
| --- | --- | --- |
| Entra admin center | A managed identity on the server, or an app registration with a certificate (service principal) | The agent's identity. Steps in [agent.md](agent.md#set-up-the-identity) |
| Entra admin center | A security group containing that identity | The Fabric tenant setting below is granted to a group |
| Fabric admin portal | **Tenant settings > Developer settings > Service principals can use Fabric APIs**, enabled for that group | Without it, every Fabric and OneLake call from the agent is refused |
| Fabric workspace (each environment that takes [pipeline run requests](agent.md#run-from-a-fabric-pipeline)) | Whoever runs the pipeline (its owner, or the identity its Notebook activity uses) needs write access to `LH_Meta`, which **Contributor** gives | Writing the request file. Anyone who can write `LH_Meta` can ask the agent to run that environment, so keep Contributor for people and identities you trust with its runs |
| Fabric workspace (each environment the agent runs) | **Manage access > Add people or groups**: the identity as **Contributor** or higher | Reading and writing the layer lakehouses and `LH_Meta` |
| Fabric workspace (Keys, if the project uses surrogate keys) | The identity as **Contributor** or higher | Reading and writing `LH_Keys` |
| Key Vault (only if the agent reads secrets, roadmap M2) | **Key Vault Secrets User** role for the identity | Reading source secrets |

Schedules on a PC (Windows Task Scheduler) are different: they run as the person who set them up, with their saved sign-in, and need nothing here.

### The T-SQL contract job (maintainers only)

The scheduled [T-SQL contract job](live-testing.md#t-sql-contract-cases) isn't part of the app. It signs in as a service principal kept in the repository's secrets and asks for two tokens: Fabric (`https://api.fabric.microsoft.com/.default`, to read the test workspace and find the warehouse's server) and Azure SQL (`https://database.windows.net/.default`, to run read-only queries). Like the agent, its registration needs no entries on the **API permissions** page. Give the identity **Viewer** on the dedicated test workspace only, and enable **Service principals can use Fabric APIs** for a group containing it.

## External AI tools

People can connect AI tools they already use (Claude, Codex, Copilot, Cursor, any MCP client) to KratosForge through **Settings > Integrations**. This asks for **no new Entra permission**: an AI tool works only through the app, which uses the person's own sign-in, so it can never do more in Fabric than the person can. The app never hands a tool a token. But Python a tool runs (after the person approves it) runs as the person, like any notebook. It is **not sandboxed**: it can read and change the person's files, and it can use the person's sign-in: the token cache on the PC is encrypted for the person's Windows account, which stops a copy of the file being used elsewhere but not code running as that person, so it can decrypt the cache and act as them in Fabric. What it doesn't get is the app's environment variables: the process it runs in starts with only what Python needs (`PATH`, `PATHEXT`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, the processor variables `PROCESSOR_ARCHITECTURE`, `PROCESSOR_ARCHITEW6432`, `PROCESSOR_IDENTIFIER`, `NUMBER_OF_PROCESSORS`, `TEMP`, `TMP`, `TMPDIR`, `USERPROFILE`, `HOME`, the locale variables `LANG`, `LANGUAGE`, `LC_*`, and the network's proxy and certificate settings `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `CURL_CA_BUNDLE`), so a secret kept in an environment variable isn't visible to it. A proxy address keeps its host and port but loses any user name and password in it, so on a network whose proxy needs a password set that way, Python an AI tool runs can't reach OneLake through it (notebooks you run yourself are unchanged). Treat approving it like running a script you were sent (see [Security model](security.md)). Integrations are off until the person switches them on, and IT can keep them off.

Each connected tool gets some of these categories, in alphabetical order:

| Category | Lets the tool | In this release |
| --- | --- | --- |
| `DEPLOY_TO_FABRIC` | Publish, promote and deploy | Not offered |
| `EXECUTE_LOCAL_CODE` | Run Python and notebooks on the PC, as the person | Offered; the person approves every run |
| `MANAGE_CONNECTIONS` | Manage connections and credentials | Never offered |
| `MODIFY_FABRIC_ARTIFACTS` | Create or change Fabric items | Not offered |
| `READ_DATA` | Read rows: previews and query results | Offered; asks first unless the person pre-approves it for that tool |
| `READ_METADATA` | Read names, structure, schemas, checks and run status | Offered; every connected tool has it |
| `WRITE_LOCAL_FILES` | Create and change notebooks in the notebook folder on the PC | Offered; asks first unless pre-approved |
| `WRITE_ONELAKE` | Write tables and files in OneLake | Not offered |

Policy keys `integrations`, `integrations_http`, `integrations_editors`, `integrations_max_permissions` and `integrations_extensions` ([azure-setup.md](azure-setup.md#4-push-settings-to-everyone)) turn integrations off, cap the categories or limit which extensions can be turned on. Extensions add capabilities in these same categories; they add no new ones. The threat model is in [ai-integration-security.md](product/ai-integration-security.md).

A paired VS Code window ([Run cells from VS Code](vscode.md#run-cells-from-vs-code)) runs cells as you, with your sign-in, in notebooks inside the folder it paired from. The OneLake write question it gets is a guard rail, not a control; `integrations_editors: false` is the control, and it ends every pairing at the next call.

## Keeping this page right

When code asks for a new scope (`sidecar/fabric_desktop/auth.py`) or the agent needs a new role, add it to the tables here in its alphabetical place, in the same pull request, and say whether Microsoft's sign-in is expected to cover it.
