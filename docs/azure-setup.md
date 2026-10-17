# Configure Azure and Fabric

Fabric Desktop by KratosBI needs no app registration: it signs people in as themselves through Microsoft's own Fabric sign-in, the same one Fabric CLI uses. What's left to set up is a few Fabric settings, and workspaces with capacity. This page walks through it in order. It is written for whoever administers Fabric in your organization. Most of it is done once.

Everything about sign-in and permissions, including how to use your own app registration if your organization requires one, is in **[permissions.md](permissions.md)**.

| Step | Who does it | Once or per person |
| --- | --- | --- |
| [1. Check your Fabric tenant settings](#1-check-your-fabric-tenant-settings) | Fabric administrator | Once |
| [2. Check sign-in policies](#2-check-sign-in-policies) | Entra or Conditional Access administrator | Once |
| [3. Only if required: your own app registration](#3-only-if-required-your-own-app-registration) | Entra Application Administrator | Once, and usually not at all |
| [4. Push settings to everyone](#4-push-settings-to-everyone) | IT | Once, optional |
| [5. Plan workspaces and capacity](#5-plan-workspaces-and-capacity) | Fabric or workspace admins | Once per project |
| [6. Give people access](#6-give-people-access) | Workspace admins | Per person or group |
| [7. Optional extras](#7-optional-extras) | Varies | As needed |
| [8. Check it works](#8-check-it-works) | One user | Once |

## 1. Check your Fabric tenant settings

In the **Fabric admin portal** (`app.fabric.microsoft.com` > gear > **Admin portal > Tenant settings**), check these. Most tenants already have them on.

| Setting | Where | Needed for |
| --- | --- | --- |
| **Users can access data stored in OneLake with apps external to Fabric** | OneLake settings | Everything that reads or writes tables and files. Fabric Desktop by KratosBI talks to OneLake from outside Fabric, so if this is off every read and write is refused. |
| **Users can create Fabric items** | Microsoft Fabric | Creating lakehouses, notebooks, variable libraries and semantic models. |
| **Create workspaces** | Workspace settings | Only if people will create Dev, Test or Prod workspaces from the app. |
| **Service principals can use Fabric APIs** | Developer settings | Only for the [headless agent](agent.md). Enable it for a security group that contains the agent's identity. |

Settings can be on for the whole organization or for named security groups. If you limit them to groups, make sure Fabric Desktop by KratosBI's users are in them.

## 2. Check sign-in policies

There is no app to register and no consent to grant. People sign in with their work account; on Windows that usually happens through Windows itself, with no prompt.

Two Conditional Access policies can still get in the way:

- **"Require a compliant or managed device"**: works on PCs your organization manages, because Windows sign-in proves the device. Unmanaged PCs are refused, as they are for Fabric in the browser.
- **Policies that block Microsoft's developer tools** (Azure CLI, Fabric CLI) for some users: Fabric Desktop by KratosBI signs in through the Fabric CLI app, so those users are refused too. Exclude the Fabric CLI app for Fabric Desktop by KratosBI's users, or use step 3.

[permissions.md](permissions.md#microsofts-fabric-sign-in-what-it-may-need-to-check) has the details and which features each Microsoft API covers.

## 3. Only if required: your own app registration

Skip this unless your organization requires every app to use a registration in your own tenant, or wants to pick the exact permissions. Then follow [permissions.md: Using your own app registration](permissions.md#using-your-own-app-registration): register a public client with the redirect URI `http://localhost`, add the delegated permissions in its table, and give its ID to everyone with step 4. None of those permissions needs admin consent, unless your tenant stops users consenting to apps.

## 4. Push settings to everyone

Nothing here is required. A policy file at `%ProgramData%\FabricDesktop\policy.json`, pushed with Intune or Group Policy, can fix the update channel or turn self-updates off, hide Preview features, keep work off Fabric capacity, limit sign-in to your own tenant, point **Report a problem** at your own support channel, and name your own app registration (step 3). People see "Set by your organization" and can't change those settings.

A policy file that sets everything an organization usually wants:

```json
{
  "channel": "stable",
  "show_preview": false,
  "support_url": "https://teams.microsoft.com/l/channel/..."
}
```

| Key | Values | Effect |
| --- | --- | --- |
| `allow_fabric_runs` | `true` (default) or `false` | `false` keeps work off Fabric capacity: Run in Fabric, Fabric schedules, Optimize and the Fabric side of a proof are refused with a message, and turning a Fabric schedule off still works. Runs on the PC are not affected |
| `allowed_tenants` | A list of tenant (directory) IDs | People can only sign in with accounts from these tenants. Any other account is signed out again with a message. Leave it out to allow any |
| `auto_update` | `true` (default) or `false` | `false` stops the app updating itself, so you roll out builds yourself ([Enterprise install](enterprise-install.md)) |
| `channel` | `stable` or `preview` | Fixes the update channel |
| `client_id` | Your own app's Application (client) ID | Only with step 3. Leave it out to keep Microsoft's sign-in |
| `kernel_process` | `true` or `false` | Runs notebook cells in a Python process of their own, so Stop and crashes end only that process (Preview; off by default) |
| `local_retention_days` | A whole number of days (default `30`; `0` keeps them) | Local copies of lakehouse data that haven't been used for this long are deleted when the app starts: file previews, tables staged before a publish, the metadata framework's download cache. Notebooks, settings and history are never deleted |
| `show_preview` | `true` or `false` | Shows or hides every feature tagged Preview |
| `support_url` | An `https://` link or a `mailto:` address | Where **Report a problem** sends people, instead of GitHub |
| `integrations` | `false` | Turns off external AI tools (MCP and the local API) on the PC; see [permissions.md: External AI tools](permissions.md#external-ai-tools) |
| `integrations_http` | `false` | Stops anyone switching on the local HTTP API; MCP over stdio still works if integrations are on |
| `integrations_max_permissions` | A list, for example `["READ_METADATA", "READ_DATA"]` | The most an external AI tool can be given |
| `integrations_extensions` | A list of extension ids, for example `["notebook-outline"]`; `[]` for none | Which extensions people can turn on in Settings > Integrations |

A key that is misspelled or has the wrong kind of value (`"false"` in quotes instead of `false`) is ignored, and **Settings > Help and about** lists what was ignored and why. Check there after you push a new file.

Every key is optional. More detail is in [permissions.md: Rolling out to your organization](permissions.md#rolling-out-to-your-organization).

> With about 5,000 people, consider `"show_preview": false` for most of them and a pilot group with Preview features on. Preview features haven't been tried on a live tenant ([what is tested](README.md#what-is-tested-and-what-isnt)).

## 5. Plan workspaces and capacity

Notebooks and SQL queries can use any lakehouse a person can open. The **metadata framework** expects a particular shape, so plan it before a team starts.

| What | How many | Holds |
| --- | --- | --- |
| **Environment workspaces** | One each for Dev, Test and Prod, per project | One lakehouse per layer (`LH_Bronze`, `LH_Silver`, `LH_Gold`, or whatever layers the project has), plus `LH_Meta` for run logs, checks and watermarks. |
| **Keys workspace** | One per environment, shared by every project that shares business keys | `LH_Keys`: one table per business key (`sk.customer`, `sk.product`) and the source system configuration. |

Things to know:

- **Dev can stay on the PC.** A new project's Dev keeps its tables in a `.local` folder on the developer's PC. It needs no workspace until someone assigns one in **Data mesh > Environments and promote**.
- **Test and Prod lakehouses are created by the app** on the first promote. You don't create them by hand.
- **Capacity.** Lakehouses need a workspace on a Fabric capacity (F SKU, or a trial). Runs on a PC use none of it. Features that deliberately run in Fabric do: Run in Fabric, Fabric schedules, environments set to **Runs on: Fabric**, Optimize (Fabric's table maintenance job), and semantic models. The Monitor **Cost** panel estimates how much.
- **Workspaces can be created from the app** (**Data mesh > Environments and promote > Assign workspace > Create a new workspace**) if people are allowed to create workspaces.

## 6. Give people access

Fabric Desktop by KratosBI never gets more access than the person using it. It uses their own Fabric workspace roles.

| Person | Role they need | On |
| --- | --- | --- |
| Someone exploring data and running notebooks | **Viewer** to read, **Contributor** to write tables | The workspaces whose lakehouses they use |
| A data engineer building a metadata project | **Contributor** or higher | Each environment workspace they run, and the Keys workspace |
| Someone who promotes to Test or Prod | **Contributor** or higher | The target environment's workspace and the Keys workspace |
| Someone who manages access from the app's **OneLake > Access** tab | **Admin** | The workspace |

Using security groups for these roles makes [working across teams](teams.md) much easier than adding people one by one.

## 7. Optional extras

| Extra | What to set up | Used by |
| --- | --- | --- |
| **Secrets in notebooks** | An Azure Key Vault; give people the **Key Vault Secrets User** role on it; may need your own app registration with the Azure Key Vault permission ([permissions.md](permissions.md#what-each-feature-uses)) | `notebookutils.credentials.getSecret` on the PC |
| **Teams alerts** | In a Teams channel, add the **Workflows** template "Post to a channel when a webhook request is received" and copy its address | **Settings > Notifications** or Freshness on an entity: new incidents (late and failed tables) are posted to the channel |
| **OneLake shortcuts to ADLS Gen2 or Amazon S3** | A cloud connection in Fabric (**Settings > Manage connections and gateways**) | **OneLake > Shortcuts > New shortcut** |
| **OneLake security (data access roles)** | Turn on OneLake security on the lakehouse in Fabric | **OneLake > Access** table-level roles |
| **A headless agent on a server** | A managed identity (no app registration), or an app registration with a certificate; the **Service principals can use Fabric APIs** tenant setting; Contributor on the workspaces | [agent.md](agent.md) |

## 8. Check it works

Have one person do this on a PC with the app installed:

1. Start Fabric Desktop by KratosBI and **Sign in**. Expected: Home says "Welcome back".
2. Open **Workspaces**. Expected: the workspaces they can open in Fabric. If it's empty, they have no workspace roles yet.
3. Open **OneLake**, expand a lakehouse, and choose **SQL query**. Run `SELECT * FROM <a table> LIMIT 10`. Expected: rows. This proves OneLake access from outside Fabric works.
4. **Create > Notebook**, **Add data items**, attach a lakehouse they can write to, and run:

   ```python
   import polars as pl
   from fabric_desktop import lakehouse
   lakehouse.write_table(pl.DataFrame({"ok": [1]}), "dbo.fabric_desktop_check")
   ```

   Expected: the table shows in Fabric (refresh the lakehouse there if it shows under **Unidentified**). Delete it afterwards.
5. If anything fails, the bell (Activity) has the error with its details. [Troubleshooting](troubleshooting.md) lists the common ones.
