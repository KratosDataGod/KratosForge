# Permissions

Fabric Desktop never gets more access than the person using it. It signs in as you and can only do what your own Fabric workspace roles let you do. It has no app-level (application) permissions of its own.

There are two ways it signs in:

- **Microsoft's Fabric sign-in (the default).** Nobody registers an app, grants consent or types an app ID. Fabric Desktop signs in through the same Microsoft app that Fabric's own command-line tool (Fabric CLI, `fab auth login`) uses, which every Microsoft Entra tenant already trusts for Fabric and OneLake. On Windows it signs in through Windows itself (Web Account Manager), so you are often signed in with your Windows work account and see no prompt.
- **Your organization's own app registration (optional).** For organizations whose policy says every app must be registered in their own tenant, or that want to choose the exact permissions. See [Using your own app registration](#using-your-own-app-registration).

The [headless agent](#the-headless-agent) is separate: it runs unattended, so it signs in as a managed identity or service principal.

## Microsoft's Fabric sign-in: what IT may need to check

Nothing, in most tenants. These are the things that can stop it:

| Tenant setting or policy | What happens | What IT can do |
| --- | --- | --- |
| Fabric tenant setting **Users can access data stored in OneLake with apps external to Fabric** is off | Every OneLake read and write is refused | Turn it on, for everyone or for a group ([azure-setup.md](azure-setup.md#1-check-your-fabric-tenant-settings)) |
| Conditional Access requires a compliant or managed device | Sign-in is refused on a PC your organization doesn't manage | Nothing to change if the PC is managed: Windows sign-in passes the device check. On an unmanaged PC, use a managed one |
| Conditional Access blocks Microsoft's developer tools (Azure CLI, Fabric CLI) for some users | Sign-in is refused with AADSTS53003 | Allow the **Fabric CLI** app for Fabric Desktop's users, or use [your own app registration](#using-your-own-app-registration) |
| Your organization doesn't allow apps it hasn't registered | Policy, not a technical block | Use [your own app registration](#using-your-own-app-registration) |

### What each feature uses

With Microsoft's sign-in, Fabric Desktop asks each Microsoft API for its default access (`/.default`), which Microsoft sets for the Fabric CLI app. The table says which API each feature calls. A feature that needs something Microsoft's app doesn't cover says so, and still works with your own app registration.

| Feature | API | Expected with Microsoft's sign-in |
| --- | --- | --- |
| Workspaces, lakehouses, notebooks, semantic models, variable libraries | Fabric | Yes |
| Reading and writing tables and files | OneLake (Azure Storage) | Yes |
| Run in Fabric, Fabric schedules, table maintenance | Fabric | Yes |
| Capacities (creating a workspace, the cost view) | Fabric | Yes |
| OneLake shortcuts and data access roles, cloud connections | Fabric | Yes |
| Giving someone a workspace role by email | Power BI | Yes. No directory permission is needed |
| Adding someone to a data access role who has no role in the workspace yet | Microsoft Graph | Possibly not. Give them a workspace role first, or paste their object ID |
| `notebookutils.credentials.getSecret` in notebooks on the PC | Azure Key Vault | Possibly not. Needs your own app registration |

"Expected" means Fabric CLI itself does these things with the same app. None of it has been tried from Fabric Desktop on a live tenant yet.

Endorsement (Certified, Promoted) isn't shown in Fabric Desktop's own list, because it needs an admin-only permission. The Fabric catalog embedded on the same page shows it.

## Using your own app registration

You only need this if your organization requires it. Everything below is optional.

1. **Register the app.** Entra admin center: **App registrations > New registration**, any name (for example `Fabric Desktop`). For people from one organization choose single tenant; for several, **Accounts in any organizational directory**. Under **Authentication > Add a platform > Mobile and desktop applications**, add the redirect URI `http://localhost`.
2. **Add the delegated permissions** below. **API permissions > Add a permission**, then **Microsoft APIs**, or **APIs my organization uses** if you search by name. Choose **Delegated permissions** each time.
3. **Grant admin consent** (optional). None of these permissions requires an admin, so people can consent for themselves at first sign-in if your tenant lets users consent to apps. If it doesn't, select **Grant admin consent for <your tenant>** (Global Administrator, Privileged Role Administrator or Cloud Application Administrator).
4. **Give the app's ID to Fabric Desktop**: see [Rolling out to your organization](#rolling-out-to-your-organization).

With your own app, Fabric Desktop signs in with the browser (not Windows sign-in) and asks for each permission the first time a feature needs it.

| API (as Entra shows it) | Permission | Needed for | Asked |
| --- | --- | --- | --- |
| Azure Key Vault | `user_impersonation` | Notebooks reading secrets with `notebookutils.credentials.getSecret` on this PC | Optional, when a notebook asks |
| Azure Storage | `user_impersonation` | Reading and writing OneLake: tables, files, the Fabric runner bundle | At first sign-in |
| Microsoft Graph | `User.ReadBasic.All` | Adding someone to a data access role who has no role in the workspace yet | Optional, on first use |
| Power BI Service | `Capacity.Read.All` | Listing capacities when you create a workspace, and capacity size for the cost view | On first use |
| Power BI Service | `Connection.Read.All` | Cloud connections when you create a shortcut | On first use |
| Power BI Service | `Item.Execute.All` | Running or scheduling notebooks, the Fabric runner and table maintenance in Fabric | On first use |
| Power BI Service | `Item.ReadWrite.All` | Creating and updating lakehouses, notebooks, semantic models, variable libraries; reading a notebook's cells for its preview | At first sign-in |
| Power BI Service | `OneLake.ReadWrite.All` | OneLake shortcuts and data access roles | On first use |
| Power BI Service | `Workspace.ReadWrite.All` | Listing and creating workspaces, workspace roles (also by email) | At first sign-in |

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

**People from more than one organization, with your own app.** Make the registration multitenant. If an organization doesn't let users consent, its Microsoft Entra admin approves the app once with the link in **Settings > Account > Admin approval** (`https://login.microsoftonline.com/organizations/adminconsent?client_id=<application-client-id>`).

**Updates.** Every green build of `main` goes to the Preview channel at once. The Stable channel, which everyone gets by default, only moves when someone runs **Actions > Promote to stable** with the notes people will see as What's new.

## The headless agent

The agent runs with nobody signed in, so it can't use a person's sign-in. A **managed identity** needs no app registration at all: use it when the agent runs on an Azure VM, Azure Arc server or container. Elsewhere it needs a service principal (an app registration with a certificate). Either way its registration needs no entries on the **API permissions** page: Fabric gives app identities what their workspace roles allow. Set up, in alphabetical order:

| Where | Setting | Why |
| --- | --- | --- |
| Entra admin center | A managed identity on the server, or an app registration with a certificate (service principal) | The agent's identity. Steps in [agent.md](agent.md#set-up-the-identity) |
| Entra admin center | A security group containing that identity | The Fabric tenant setting below is granted to a group |
| Fabric admin portal | **Tenant settings > Developer settings > Service principals can use Fabric APIs**, enabled for that group | Without it, every Fabric and OneLake call from the agent is refused |
| Fabric workspace (each environment the agent runs) | **Manage access > Add people or groups**: the identity as **Contributor** or higher | Reading and writing the layer lakehouses and `LH_Meta` |
| Fabric workspace (Keys, if the project uses surrogate keys) | The identity as **Contributor** or higher | Reading and writing `LH_Keys` |
| Key Vault (only if the agent reads secrets, roadmap M2) | **Key Vault Secrets User** role for the identity | Reading source secrets |

Schedules on a PC (Windows Task Scheduler) are different: they run as the person who set them up, with their saved sign-in, and need nothing here.

## Keeping this page right

When code asks for a new scope (`sidecar/fabric_desktop/auth.py`) or the agent needs a new role, add it to the tables here in its alphabetical place, in the same pull request, and say whether Microsoft's sign-in is expected to cover it.
