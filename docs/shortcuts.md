# Shortcut command center

**OneLake > Shortcuts** is where you manage every OneLake shortcut you can see, in every workspace and lakehouse, in one place. It shows whether each one works, who can read it, what changed and who changed it, and the requests for new ones.

It's a preview feature: it hasn't been tried on a live Fabric tenant yet.

## What's on it

The tab has five views, picked with the buttons under the title.

| View | What it shows |
| --- | --- |
| **All shortcuts** | Counts at the top (shortcuts, broken, read with a connection, changed in the last 7 days, requests waiting on you), then one row per shortcut: where it lives, what it points at, its health, the managed tables that read it, and its last change. Broken shortcuts are listed first. Select a row to open its panel. |
| **Health** | Broken shortcuts grouped by cause (or by the lakehouse they lost), and shortcuts that work but are worth a look, each with a fix |
| **Access** | Shortcuts whose readers and target don't match, and the platform's shortcut rules |
| **Changes** | Every shortcut added, repointed, removed, broken or fixed, with Undo and Recreate |
| **Requests** | Shortcut requests waiting on you and the ones you sent, and **shortcut needs**: shortcuts that need changing |

**New shortcut** makes one in any lakehouse you can write to. **Refresh** reads every lakehouse again. **Check all now** checks every shortcut's health.

## One shortcut's panel

Select a shortcut to see:

- **Overview:** what it points at (lakehouse and path, or the storage location and connection), the data product it points at if any, its environment, when it was first seen, and the managed tables that read it.
- **Who can read:** everyone who can read the shortcut, and whether the target lets each of them in.
- **History:** every change to this shortcut.

From the panel you can **Check now**, **Repoint** it, **Open in Fabric** (its lakehouse) or **Remove** it. A shortcut that crosses environments also offers its [fixes](#a-crossed-environment), and one whose risk was accepted shows who accepted it and why, with **Withdraw**. Removing a shortcut never touches the data it points at, and you can recreate it from **Changes**.

## Health

A health check lists one entry of the shortcut's folder through OneLake, with your own sign-in. If the listing works, the target is there and Fabric can reach it. Nothing else is read, and nothing is written.

| Health | Means | What to do |
| --- | --- | --- |
| **Healthy** | The check listed the target | Nothing |
| **Broken**, target isn't there | The table or folder it points at was removed, renamed or moved | **Repoint** it at the new place, or **Remove** it. When you can't open the lakehouse at all, see [A lakehouse you can't open](#a-lakehouse-you-cant-open) |
| **Broken**, storage refused the connection | An ADLS Gen2, S3 or other storage shortcut whose connection was refused: an expired key or secret, or the account lost its rights | The connection's owner updates it in Fabric (**Settings > Manage connections and gateways**), then **Check now** |
| **Unreachable** | You have no access to a OneLake target, so the check can't tell whether it works for others | Ask for access to the target, or ask someone who has it to check |
| **Not checked** | Not checked yet, you can't read the lakehouse it's in, or OneLake was busy (it throttled the check, or failed it for a OneLake target, after a few tries; for an ADLS, S3 or other storage target a 500, 502 or 504 shows as broken, since the storage itself may be down) | **Check all now**, or wait for the next check |

Every shortcut is checked when the page opens. To stop that, untick **Check every shortcut when this page opens** on the Health view.

Results are saved to the [platform](platform.md), so everyone sees the same health. A shortcut that breaks, or works again, shows on **Changes**, and is posted to the platform's Teams routes when one takes `shortcut.broken` or `shortcut.fixed` ([governance guide](governance-guide.md#teams-notices-for-requests-and-shortcuts)).

### On a schedule (Preview)

So that a break is found without anyone opening this page, turn on `[shortcut_health]` in a [headless agent](agent.md#shortcut-health). Every `shortcut_health.minutes` (60 by default) it checks the shortcuts in its environments' workspaces the same way, with its own identity, and posts each new break to its Teams channel once: a shortcut that stays broken isn't posted again, a fix is posted once, and a later break is posted again. It only reads (it lists lakehouses, shortcuts and one entry of each folder), runs no Fabric job, and writes nothing to Fabric or the platform; what it posted is kept on the agent's machine. When several agents check one environment, one of them posts. The agent's results don't show on this page: check here to see them. It's a setting in the agent's `agent.toml`, not a screen, so Settings > Preview features doesn't hide it.

The Health view also lists shortcuts that work but are **worth a look**:

- **Crossing environments:** a shortcut in a Dev or Test workspace that points at Prod (a Dev run reads live data), or one in Prod that points at Dev or Test (Prod depends on data that isn't released). The environment comes from the metadata project's environment workspaces and the platform's workspaces, else from a word in the workspace name (dev, test, uat, qa, prod, production).
- **Duplicates:** two shortcuts in one workspace that point at the same place.

## Fixing shortcuts

These fixes are in preview, with their own Preview tag.

### A lakehouse you can't open

When a OneLake shortcut is broken and the lakehouse it points at isn't in any workspace you can open, the lakehouse may be gone (deleted, or made again somewhere else), or you may only have lost access to it. The Health view groups those shortcuts by that lakehouse: **LH_Gold in Sales Old isn't in a workspace you can open**. The name is the one KratosForge last saw the lakehouse with; a lakehouse that was gone before KratosForge first saw it shows as **The lakehouse it points at**.

KratosForge then looks for a lakehouse with the same name among the lakehouses you can list, and asks OneLake whether each shortcut's table or folder is there, the way a health check does. Nothing else is read, and nothing changes.

- **A lakehouse called LH_Gold is now in Sales New, and all 6 tables are there** (or **4 of 6**): **Repoint all 4 there** repoints the ones that are there.
- **Repoint all to another lakehouse...** is always there: pick any lakehouse you can open.

Each shortcut in the list also has **Repoint**, to point it somewhere else on its own, and **Remove**.

### Repoint many at once

Repointing several shortcuts asks once: **Repoint 6 shortcuts?** Each keeps its name and its table or folder path, and only the lakehouse changes. They're repointed one at a time, as [Repoint](#repointing) does it: one that fails is put back as it was, and the rest carry on. The message at the end says how many were repointed and why any weren't. Each one is on **Changes**, with **Undo**.

### A crossed environment

A shortcut that crosses environments (Dev reading Prod) shows what to do on Health, on **Shortcut needs** and in its panel:

- **Repoint to Dev's LH_Gold:** when the metadata project or the platform says which workspaces are the shortcut's own environment, and one of them has a lakehouse with the same name as the target, this repoints the shortcut there, keeping its table. Otherwise **Repoint** opens the usual dialog.
- **Accept...** (platform admins only): see below.

### Accept a crossed environment

Sometimes Dev reading Prod is on purpose, for example a read-only price list. A platform admin selects **Accept...**, says **Why it's fine** (required), and selects **Accept**. The shortcut then leaves **Shortcut needs** and **Worth a look**, and shows **Accepted by** *who*: *why* instead of the warning.

- Repointing or removing the shortcut ends the acceptance (in KratosForge, or seen at scan when done in Fabric), so a new target is judged afresh.
- **Withdraw**, in the shortcut's panel, takes it back: the shortcut is flagged again.
- Accepting, withdrawing and an acceptance ending are each in **Platform > Audit**, with who did it.

Acceptances are kept by the [platform](platform.md), so the whole team sees them. A platform in Fabric needs **Platform > Settings > Finish or repair** once after this update, so `UDF_Platform` has the functions; its table and columns are added on first use, and nothing already there changes.

## Who can read a shortcut

Shortcuts decide who reads them in one of two ways:

- **OneLake shortcuts:** each reader needs access in both places, the lakehouse with the shortcut and the target. Nobody reads more than the target allows. Someone with access to the shortcut's lakehouse but not the target sees it empty.
- **Storage shortcuts** (ADLS Gen2, Amazon S3, Azure Blob and others): Fabric reads the storage with the connection's key or account. Anyone who can read the lakehouse reads the storage, even if the storage itself would refuse them. To limit who reads it, add a OneLake data access role on the shortcut's folder (**Add data access role** opens the Access tab).

The **Access** view lists every shortcut where readers and target don't match. Readers come from workspace roles and OneLake data access roles, as on the [Access tab](user-guide.md#access-preview). Workspace Viewers read tables only through data access roles.

If a lakehouse's data access roles can't be read (no rights to see them, throttling, a network error), the list of readers says **Not complete** with the reason, because people let in by those roles may be missing; the Access view lists that shortcut too. Only Fabric answering that OneLake security isn't turned on for the lakehouse means it has no data access roles.

## Shortcut rules

Platform admins set rules for shortcuts on the Access view. Each rule is **Off**, **Warn** (the person sees a warning and can go on) or **Block** (KratosForge won't make it).

| Rule | Default |
| --- | --- |
| A shortcut may not point at another environment | Block |
| A storage shortcut should have a data access role on its folder | Warn |
| A shortcut should not point at a deprecated data product | Warn |
| Storage shortcuts use only the allowed connections (a list of connection IDs) | Off |
| Who may create shortcuts from KratosForge: anyone with the workspace role, or only platform admins (everyone else sends a request) | Anyone |

The rules apply to what KratosForge makes: **New shortcut**, **Repoint**, Undo and Recreate, and approving a request. Fabric itself still lets any workspace Contributor make a shortcut in Fabric; anything made there that breaks a rule shows on **Health**.

New shortcut and Repoint check the rules as you fill in the dialog. When a rule blocks a new shortcut, the button becomes **Send as a request instead**.

**When the rules can't be read** (the platform doesn't answer, or the call fails), nothing is made: the dialog says the rules couldn't be checked and its button stays off, and approving a request stops the same way. Missing rules are treated as unknown, never as "no rules". Try again once the platform answers.

## Changes

**Changes** lists every change in the last 7, 30 or 90 days, or the last year, newest first:

- **Added**, **Repointed** and **Removed**, with what it pointed at before and after.
- **Broke** and **Fixed**, from health checks.

A change made in KratosForge says who made it, and why when it came from a request. Fabric doesn't record who changes a shortcut, so KratosForge compares what it sees with what it saw last time each time it reads the shortcuts. Changes made in Fabric, or by other tools, show as **Seen at scan**. The first time a lakehouse is read sets the starting point, so shortcuts already there aren't listed as added.

Anyone's scan adds new shortcuts, and anyone's health check can mark one broken. A change that could hide a problem (a shortcut removed or repointed, or one no longer broken) changes the inventory only when a platform admin or a member of a project with a data product in that lakehouse sees it. From anyone else, a repoint (seen at scan or made in KratosForge) is listed as *reported* and the shortcut, its health and its accepted risks stay as they were, a removal waits, a health check records only a break, and the page says what wasn't recorded. A lakehouse no project has a product in is a platform admin's to update. Each scan and check is recorded with who made it on **Platform > Govern > Audit**.

Every change keeps the shortcut's full definition:

- **Undo** on a repointed shortcut points it back; on an added one, removes it. It first checks the shortcut in Fabric is still as that change left it (pointing at the same target, or for a removed one, nothing in its place). If it has changed again since, Undo says so and changes nothing. Undo and Recreate show only on a change that was applied and made by a platform admin or a member of a project with a product in that lakehouse; a reported change, or one logged by someone else, has neither.
- **Recreate** on a removed shortcut makes it again, with the same name, place and target.

Both ask first, follow the shortcut rules, and never touch the data the shortcut points at. The history is kept by the platform, so the whole team shares one history.

## Repointing

Fabric can't change where a shortcut points, so **Repoint** removes the shortcut and makes it again with the same name and place, pointing at the new target. Whatever reads it carries on reading it by the same name. If making the new one fails, the old one is put back. If even that fails, the change history has its definition and **Recreate** makes it again. Runs that read the shortcut in the moment between may fail.

## Requests and shortcut needs

**Request a shortcut** asks for a shortcut you can't make yourself, for example into a lakehouse where you have no role, or when the rules say only platform admins make shortcuts. Say which lakehouse, where in it, its name, what it points at, and why.

Shortcut requests are the [platform's requests](platform.md#requests), next to key tables, sources and access to data products. They show on **Shortcuts > Requests** and **Data mesh > Requests**.

- A **platform admin** decides any shortcut request. When the shortcut points at a data product, its **owner** can decide it too.
- **Approve** makes the shortcut first, as the person approving, then marks the request approved. If making it fails (no role on that workspace, or a rule blocks it), the request stays waiting and the error says why.
- If an approval stopped after Fabric made the shortcut (a timeout, or the platform couldn't be reached), approving again finds it. If it points exactly where the request asks, the request is approved and the shortcut is recorded once. If a shortcut there points anywhere else, the request stays waiting and the error names its target.
- The person who sent it can withdraw it while it waits.

**Shortcut needs** lists shortcuts that need changing:

- Broken shortcuts that a managed table in the open metadata project reads (its runs fail until it's fixed).
- Shortcuts pointing at a deprecated data product.
- Shortcuts crossing environments, unless a platform admin [accepted](#accept-a-crossed-environment) it.

## Permissions

Nothing new. The command center uses what the Shortcuts and Access tabs already used: listing, creating and removing shortcuts and data access roles (Fabric), and listing folders through OneLake for health checks. Finding a lakehouse that moved lists workspaces and lakehouses with your sign-in, as the OneLake catalog does, and asks OneLake the way a health check does. See [Permissions](permissions.md).

## What isn't there yet

- The desktop app checks only when the page opens or when you ask; the scheduled check runs in the [agent](agent.md#shortcut-health), and its results aren't saved to the platform or shown on Health.
- An approved request makes the shortcut in one lakehouse, not in each environment.
- Only a crossed environment can be accepted, not a duplicate. Finding a lakehouse that moved compares names only, and looks at five lakehouses with that name at most.
- Not tried on a live Fabric tenant yet. In particular, the error codes OneLake returns for a broken target or a refused connection haven't been seen on a real tenant, so a broken shortcut may show the wrong cause.
