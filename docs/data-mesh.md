# Data mesh

KratosForge lets many teams build on one platform without stepping on each other. A central team runs the [platform](platform.md): one business key system and one landing zone for everyone. Bronze processing is decentralized: each team builds its own Bronze from the landing zone, and everything after it; domain teams own their projects, publish **data products**, and read each other's products through **links**. Everything a team builds stays in its own Git repository.

!!! note "Preview"
    The Domains tabs are tagged Preview: they're tested on the PC but not yet on a live tenant.

## Who does what

| | Central team (platform admins) | Domain teams |
| --- | --- | --- |
| Landing zone | Land every source once, register sources | Read it in their own Bronze; request a new source |
| Business keys | Create key tables (in every environment at once) | Use them; request a new one |
| Domains | Create domains and subdomains, name their owners | Ask to join one (its owners approve); owners keep its glossary terms |
| Projects | Change any project's members | Owners add and remove members; members publish, link and change the domain |
| Data products | | Publish their own; decide who else may read them |
| Links | | Link to products open to them; request access to others |
| Catalog text | Shared glossary terms | Describe their tables and columns |

All of it happens on the **Platform** page (central team, and requests from everyone), **Data mesh > Project > Products and links** (a project's own products and links) and **Data mesh > Products** (finding other teams' products). See [The platform](platform.md#who-can-do-what).

## Home: the mesh for who you are (Preview)

**Data mesh > Home** is where the page opens. It shows the same catalog three ways, one for each of the people who run a mesh; pick one at the top right and KratosForge remembers it on this PC. The first time, it starts on **My team** if you own a data product or have a project open, on **On call** if you're a platform admin, and on **Organization** otherwise. Home needs no metadata project open: a CDO or an on-call engineer can use it without one.

| View | For | What it shows |
| --- | --- | --- |
| **My team** | A team lead | Requests waiting on you, your own asks and what happened to them, your products (readers, owners, and what each needs: an incident upstream, no second owner, an access review due, a deprecation), and the products your project links to or you follow |
| **On call** | Whoever is on call | Active incidents that reach a data product, with its readers and owners, who to wake, landing sources late in Prod, shortcuts broken at their last check, and owner gaps |
| **Organization** | The CDO and domain leads | Totals, one row per top-level domain (subdomains count toward their parent), and requests that have waited more than 5 days |

Home only reads: the catalog (products, requests, follows, access reviews, domains, shortcuts, owner coverage), the landing zone on On call, and the incident lists of the project open on this PC. Each card opens the page that acts: **Decide** opens [Requests](#requests-preview), a product opens its page, an incident opens it in Monitor. Something it couldn't read is named at the top and its card says so, rather than showing a zero. Incidents need a project open, because incident lists belong to a project's environments.

## Products (Preview)

**Data mesh > Products** is every team's published data products, for producers and readers alike, and works with no project open. It was **OneLake > Data products**; that tab and every link now open it here. Open a product for its page: endorsement and business terms, its checklist, who has access (with access reviews for its owner), Follow, and Ask for a change. For a product of the project open on this PC, **Edit definition** opens **Project > Products and links**, where its definition file is edited. See [Data products](user-guide.md#data-products-preview).

## Requests (Preview)

**Data mesh > Requests** is everyone's inbox for the mesh, and works with no project open. **Waiting on you** lists what you can decide: a data product's owner decides who else may read it and changes to it, a domain's owners answer requests for a new product from their domain, and platform admins decide the rest and can decide any request. **Sent by you** lists what you asked for, who decided it and their note. **Everything** shows every request in the catalog. It opens on Waiting on you when something waits on you, and on Sent by you otherwise.

It was Platform > Requests, and older links open it here. Approve does what was asked (for access, it gives the project access in the catalog), Accept on a data request records your reply and builds nothing, Decline records your note, and Withdraw takes back your own request. **Ask for data** and **New request** send one.

## Domains (Preview)

**Data mesh > Domains** has three tabs. **Domains** lists every domain and subdomain with its owners, the projects that joined it and their published data products, and **Access groups** for its Entra ID groups. Everyone sees it, with or without a project open; only platform admins see **Import from Fabric**, **New domain**, **Edit** and **Delete** (see [Domains come from Fabric](#domains-come-from-fabric)). It was Platform > Set up > Domains, and older links open it here. **Map** shows how domains feed each other (below). **This project** is the open project's own domain and workspaces (see [Join the mesh](#join-the-mesh)).

## The open project

**Data mesh > Project** is the editor for the metadata project open on this PC: **Design**, **Runs**, **Quality**, **Products and links** (the project's own data products and its links to other teams') and **Deploy**. It opens on the tab you last used there. **New entity**, **Schedule** and **Run all** show on Project's tabs; problems that block runs and promote show on every tab.

## From domains to tables

**Data mesh > Domains > Map** reads the platform's catalog and changes nothing:

1. **Domains.** Every domain, its subdomains, and how many projects and products it has. **How domains feed each other** lists, for each pair of domains, the products one domain's projects read from the other's.
2. **A domain.** Its projects, filtered by subdomain, each with its products, who reads them and what it reads. The project open on this PC also shows its layers with a table count; other projects' tables show when their folder is opened on this PC.
3. **A layer.** The layer's tables. Pick one to see what it comes from, which tables and products read it, and **Open in Design** to edit it.
4. **A table's lineage.** Every hop both ways, one column per hop. Upstream it starts at the files, landing source or link the chain begins with. Downstream it ends at the data products it feeds and the other projects that read them. **Only what it reads** and **Only what reads it** narrow it. Pick any table in a column to put it in the middle, and choose **Columns** to see where each column comes from. Tables come from the project open on this PC, and other projects come from the catalog's links. It reads only and changes nothing.

## The pieces

| Piece | What it is | Where it's kept |
| --- | --- | --- |
| **Landing zone** | Raw files from every source, landed once for everyone. Entities read it with `[source] landing = "<source>/<entity>/*.csv"` | `LH_Landing` in the Platform workspace |
| **Surrogate keys** | One key per business key across every team | `SQL_Platform` |
| **Domain** | An area of the business that owns its data, like Sales or Finance. Subdomains go one level deep (Sales > EMEA). Imported from Fabric's domains | The platform's catalog, linked to the Fabric domain |
| **Project** | A metadata project (one Git repository) and the domain and team it belongs to | `project.toml` in the project, and the catalog |
| **Workspace designation** | What a workspace is for: Ingest, Product, Serve or Sandbox | `environments.toml` and `layers.toml` |
| **Data product** | The tables (ports) other teams may read from a project, with an owner, a description and who may read it | `metadata/products/<name>.toml`, published to the catalog on promote. Once a product is in the catalog, only its owners, whoever last published it and platform admins publish it again, so the person who promotes a project must be one of them: otherwise the promote stops at that product and says who its owners are. A first publish whose owners name people (emails) must name the publisher among them |
| **Link** | A project's reference to another team's product port, read where it lives with no copy | `metadata/links/<alias>.toml` |
| **Catalog text** | Descriptions, tags, classification, glossary terms and steward of tables and columns | The platform's catalog (starter text can live in the entity file) |

Logic lives in Git and goes through review and promote. Catalog text lives in the platform, so a project's members describe its tables without a pull request.

## Join the mesh

**Data mesh > Domains > This project.** Pick your domain (and subdomain, if any), your team and a description, then choose **Join the mesh**. That writes `project.toml` at the top of the project:

```toml
[project]
name = "crm"
domain = "sales"
subdomain = "sales-emea"
team = "CRM analytics"
format = 2
```

Nothing else in the project changes, and a project without `project.toml` works exactly as before. Domains come from the platform; platform admins create them on **Data mesh > Domains > Domains**.

### Members and joining a domain (Preview)

A project in the catalog has **owners** and **members**, listed in the **Members** card on **Data mesh > Domains > This project**. Only a project's members, or a platform admin, can:

- record or change the project in the catalog (Save, Join the mesh, Promote),
- publish its data products,
- make its links to other teams' products, and
- ask to move it to another domain.

Owners also **Add member**, **Make owner** and **Remove**. A project always keeps at least one owner, and platform admins can change any project's members. Whoever records a project name the catalog has never seen is its first owner, so a platform admin corrects the members if someone took a name meant for another team. A project from before members starts with the people the catalog already names for it (whoever registered it, published its products or made its links, and the product owners given as an email) as its owners, the next time one of them changes it. A project the catalog names nobody for (one recorded signed out on a PC, say) waits for a platform admin to name its first owner. Everyone who uses the platform can see each project's members, by their sign-in.

Picking a domain you don't own asks that domain's owners (and the subdomain's, if you picked one): the request is on **Data mesh > Requests** for them, and the Members card shows **Waiting on <domain>'s owners** with **Withdraw** for whoever asked. A domain with no owners waits for a platform admin. Until they approve, the project stays in its current domain in the catalog and `project.toml` keeps it too. Once they approve, the catalog has the new domain and **This project** offers **Use it** to write it to `project.toml`. A domain's owners, and platform admins, move their own projects in directly.

What it writes: `catalog.project_member` (one row per person and project) and, for a domain ask, a `join_domain` row in `catalog.request`. Every change is on **Platform > Govern > Audit**. The checks run in the platform (UDF_Platform in Fabric), so a platform set up before this release needs **Platform > Settings > Finish or repair** once; until then the Members card says so and the checks don't apply.

## Domains come from Fabric

The mesh's domains and subdomains are the ones defined in Fabric (Admin portal > Domains), so a project's domain here is the same one its workspaces have in Fabric.

Each domain's roles (owners, contributors, workspace admins, creators, readers, viewers, stewards) go to Entra ID security groups: **Access groups** on a domain suggests a name for each, links groups you already have, and writes the scripts to create them. See [Entra ID groups per domain](platform.md#entra-id-groups-per-domain-preview).

| Step | Who | Needs |
| --- | --- | --- |
| **Import from Fabric** on **Data mesh > Domains** copies every Fabric domain and subdomain into the platform's catalog | A platform admin who is also a **Fabric administrator** | Fabric's domain list is an admin API (`GET /v1/admin/domains`, `Tenant.Read.All`). KratosForge asks for it only at this step. Each domain's admins are read too (`GET /v1/admin/domains/{id}/roleAssignments`, same permission). No app registration |
| **New domain** on **Data mesh > Domains** creates the domain or subdomain in Fabric, then keeps it in the platform linked to it (Preview) | A platform admin who is also a **Fabric administrator** | `POST /v1/admin/domains` with `Tenant.ReadWrite.All`, asked for only at this step. The platform's checks (name, parent, platform admin) run first, so a domain the platform would refuse is never created in Fabric. Someone who isn't a Fabric administrator is told so, and can save the domain in the platform only. Not yet tried against a real tenant |
| Seeing the domains, joining one, and the mesh map on Home | Everyone | Nothing: the names are read from the platform's catalog |
| **Data mesh > Domains > This project** shows which Fabric domain each of the project's workspaces is in, with **Use it** to pick it | Anyone who can see the workspace | Nothing new: each workspace says its domain to its members. Without an import, only the domain's id is known, so the tab asks for a Fabric administrator to import |

How the import works:

- A Fabric domain matches the catalog domain already linked to it, else one with the same name (domains created by hand before are kept and linked, so their projects don't change), else it's added with an id made from its name (`Finance & Risk` becomes `finance-risk`).
- Imported domains follow Fabric: rename or move one in Fabric, then import again; the catalog id stays, so projects keep their domain. Their name and parent can't be changed in KratosForge. Owners and description are the platform's and stay as you set them.
- Each domain's admins in Fabric are kept by name (no email) and shown as owners marked **From Fabric** on Data mesh > Domains and Home's mesh map, until you set the platform's own owners; then both show. They're for reading only: only the platform's owners can keep a domain's glossary terms or apply its policies. A domain whose admins can't be read keeps what the last import read, and the import says which.
- A domain deleted in Fabric is reported and kept in the catalog, because projects may still use it. Delete it on **Data mesh > Domains** once nothing uses it: no subdomain, project, landing zone, policy assignment or active exception, Entra group link or waiting request names it. Delete says which ones still do.
- Fabric allows one level of subdomains, like the mesh. Anything deeper is skipped and reported.
- **New domain** on **Data mesh > Domains** still works for organizations that don't use Fabric domains; those show as "Only here".
- Domains are created, imported and edited only on **Data mesh > Domains**, by platform admins. Everyone else sees the same list, read only, with each domain's owners, projects and published products. It was Platform > Set up > Domains; older links open it here.

## Workspaces by designation

One environment can spread its lakehouses over several workspaces, so access follows purpose:

| Designation | Typical layers | Who has access |
| --- | --- | --- |
| Ingest | Bronze | The team only |
| Product | Silver, Gold | The team; consumers read through links and OneLake access roles |
| Serve | Semantic models, reports | Report readers |
| Sandbox | Anything | Whoever is trying something |

On **Domains > This project**, pick or create a workspace for each designation and environment (a new one is named like `Sales Ingest - Test`). On **Design > Layers**, set each layer's **Workspace**. A layer with no designation stays in the environment's own workspace, and `LH_Meta` always does.

## Data products

**Data mesh > Project > Products and links > Data products > New data product.** Choose the tables to share (each becomes a **port**, with the name consumers use), an owner, and who it's **open to**:

| Open to | Who may link to it |
| --- | --- |
| Everyone | Any project |
| My domain | Projects in the same domain |
| This project | Nobody else (a product you're still shaping) |

A product is a file you review like any other change:

```toml
name = "customer_360"
title = "Customer 360"
description = "One row per customer, from every source"
owner = "crm-team@contoso.com"
access = "domain"
status = "published"
version = 1

[[port]]
name = "customers"
entity = "gold.dim_customer"
```

**Status** is `draft`, `published` or `deprecated` (with `deprecated_on`, the date it stops). Promote publishes every non-draft product to the catalog with where its ports live in the target environment; **Publish to the catalog** does the same by hand.

**History.** Each publish that changes a port's `version` or columns adds a row to the port's history in the catalog (`catalog.port_version`), one per environment: the version, the columns and types, when and by whom. Publishing the same thing again adds nothing. The platform's `product_history` operation lists a product's rows, newest first; it only reads.

**The product gate.** A product open to anyone or to the domain can't be published, and its project can't be promoted, until it has a description and an owner and every port has a table description and a description on every column. The entity's **About** card shows what's missing.

**Breaking changes.** On promote, KratosForge compares each port's columns with what the catalog holds for that port in the target environment.

- **What blocks the promote.** A column dropped, or a type changed (other than widening, such as `int32` to `int64` or `float32` to `float64`), while another project reads that environment. The message names the projects, for example *port customers drops email, and 1 other project reads it in test (finance)*.
- **What doesn't.** Added columns pass. So does anything nobody else reads yet. A reader whose link lists the columns it relies on (its `[columns]`) is only broken by a change to one of those: dropping a column no reader relies on is a note, not a block.
- **How to make a breaking change on purpose.** Raise `version` in the product's file. The promote then goes through and says what the new version breaks, unless a reader's link doesn't accept the new version (see [Links](#links)): then it blocks, naming each such link and saying its project can widen `accepts`. Readers whose links don't say `accepts` get the note, as before.
- **After a deprecation date.** When the product was deprecated with a `deprecated_on` that has passed, its readers have had their notice, so a link that doesn't accept the new version is a note, not a block.
- **If the platform can't be reached.** The check says so and doesn't block.

A reader that hasn't listed its columns counts as relying on every column of the port it links to.

**Deprecating a product.** Set `status = "deprecated"` and `deprecated_on`, then promote or publish. The first publish as deprecated, or one with a new date, sends one Teams notice (`product.deprecated`) to the projects that link to it, through the platform's Teams routes (see [the governance guide](governance-guide.md#teams-notices-for-requests-and-shortcuts)); the publish's answer lists who was told. A notice that can't be sent never stops the publish: the answer says so. From the date on, the catalog takes no new links to it, and a project that still links to it can't promote until it moves the link (before the date, its promote gets a note saying when). Runs aren't stopped: a running pipeline keeps reading until its team moves it.

## Follow a product (Preview)

**Follow** on a product's page (**Data mesh > Products >** the product) tells you in Teams when a promote to Prod changes it: it's deprecated (and when it stops), it has a new version, or a port's columns are added, removed or retyped. Whoever links a project to a product follows it without asking, so its readers hear too. **Following** on the list shows only the products you follow; choose **Following** on a product's page to stop.

Only Prod counts, because that's what readers' projects read. Each environment keeps what it last published (`catalog.product_env`), so a change is told once, when it reaches Prod, not again on the next promote. The notice goes to the platform's Teams routes as `product.changed`, naming the people it's for ([governance-guide.md](governance-guide.md)), except whoever promoted it. Who follows a product is personal data: only its owners and platform admins can list its followers. People a deprecation was already sent to as `product.deprecated` don't get it again in `product.changed`; other followers do. A platform made before following shows no Follow button until a platform admin runs **Finish or repair** once.

## Endorsement and business terms

Preview. **Data mesh > Products**, then select a product's name. The product's page shows two trust signals next to its name.

**Endorsement** (Certified, Promoted or Master data) is set in Fabric, on the item's settings. KratosForge reads it and never sets it. The items are the lakehouse each port lives in, in the highest environment the product is published to (Prod, else Test, else Dev), and the semantic models and reports its owner grouped under it ([Tables, semantic models and reports](#tables-semantic-models-and-reports-preview)). When a product has a semantic model, the badge by its name is the model's endorsement.

- **Only Fabric administrators can read it.** Fabric shows endorsement through an API only in Power BI's admin tenant scan (`POST /v1.0/myorg/admin/workspaces/getInfo`, then `scanStatus` and `scanResult`); Fabric's Get Item doesn't return it. **Scan now** shows to platform admins. It works for one who is also a Fabric administrator; anyone else is told so and nothing is saved.
- **What a scan reads and writes.** It scans only the workspaces the platform's published products, and their semantic models and reports, live in, at most 100 a call, and asks for no lineage, data sources, schemas, expressions or users. It writes what it read for each product item to `catalog.endorsement` in the platform's catalog: the endorsement, who certified it when Fabric says, when, and who scanned. Each scan is in the platform's audit log, without the certifier's name.
- **Everyone sees the last scan.** The badge shows with "From the tenant scan" and its age. Before any scan, or for an item the latest scan didn't report, the badge is a grey **Endorsement unknown**: KratosForge never guesses, and a later scan that doesn't confirm an old badge takes it away. **See it in Fabric** opens the item there. Under the product's name, **Last tenant scan** says when the newest of its items was read. When a product's items differ (say one lakehouse is Certified and another unknown), the badge by its name is a grey **Endorsement varies**, and the Endorsement card lists each item with its own badge.
- **To change it,** open the item's settings in Fabric. Scan again to update the badge.

**Business terms** are a term's name and a link to it in Microsoft Purview, so people can read the definition where your organization keeps it. **Add a term** asks for the term and **Link to the term in Purview**: copy the address from Purview. It must be an `https` link on `purview.microsoft.com` or `web.purview.azure.com`; anything else is refused. KratosForge doesn't read Purview: its sign-in can't get a Purview token, so the terms are what your stewards type. The product's owner or a platform admin adds and removes them (removing asks first); each change writes `catalog.product_term` and the platform's audit log. The term itself stays in Purview.

An older UDF_Platform doesn't have these functions yet. The page then says: *After an update, a platform admin runs Platform > Settings > Finish or repair once.*

## Tables, semantic models and reports (Preview)

A data product's page groups what it offers under **What it offers**: **Tables**, **Semantic models** and **Reports**, each with how many. Each semantic model and report shows its own endorsement badge, as the last tenant scan read it (**Endorsement unknown** until a scan has read it). Tables show their lakehouse and environment and have no badge of their own: their lakehouse's endorsement is on the Endorsement card.

- **Tables** are the product's ports in the highest environment it's published to. They come from the product's definition, so **Add a table** shows only for the product of the metadata project open on this PC and opens **Project > Products and links**.
- **Add a model** and **Add a report** show to the product's owner and platform admins. Pick a workspace, then a semantic model (or report) in it. The dialog lists only the workspaces and items you can see in Fabric, with Fabric's read-only list calls. The product records the item's id, its type, its workspace id and its display name in `catalog.product_item`, and an `add product item` entry in the platform's audit log (`catalog.audit`). Nothing in Fabric changes. Adding the same item again updates its name.
- **Remove** asks first. It deletes the item's `catalog.product_item` row and logs `remove product item`; the item stays in Fabric, as it is.
- **The product's badge** by its name, here and on the products list, is its semantic model's endorsement when it has one, since that's what Fabric's scan reports. With no model, the products list shows **Endorsement unknown** and the product's page keeps its lakehouses' badge. When its models differ, it shows **Endorsement varies**.
- **Find products by what they offer.** **Data mesh > Products** has **All**, **Has tables**, **Has models** and **Has reports** above the list, and each product says what it offers, like *3 tables, 1 model, 2 reports*.
- **Scan now** reads the semantic models' and reports' endorsement too: their workspaces are added to the tenant scan.
- **Limitation: the name is the owner's app's.** The catalog keeps the item name the owner's app sent when it added the item, and doesn't check it against Fabric. A product could therefore show a misleading name for a real item id. The endorsement badge always comes from the tenant scan for that id, never from the name.

The product's checklist (`contract.endorsed`) still reads the lakehouses its ports live in. An older UDF_Platform doesn't have these functions yet: the card says *After an update, a platform admin runs Platform > Settings > Finish or repair once*, and the products list shows no filters until then.

## Links

**Data mesh > Products > Link**, **Data products > Other teams' products > Link**, or **Links > New link.** Pick a product and port, a name for it, and which of their environments each of yours reads. Dev usually reads their Prod, so you build against real data without copying it. The catalog refuses a link to a draft, to a private product, or to a domain product from another domain, unless the product's owner let your project in: **Data mesh > Products > Request access** sends them a request, and once they approve it on **Data mesh > Requests**, **Link** works. Approving lets the link be made; the owner still gives read access in OneLake (a data access role on their Product workspace). The owner can take that access away later on the product's page under **Access given**; a link already made stays.

**Ask for data (Preview).** When the product you need doesn't exist yet, or needs a change, ask the team that would make it: **Data mesh > Products > Ask for data**, or **Ask for a change** on a product's page. Say what you need it for and, if it matters, by when. A new product goes to its domain's owners, a change to the product's owner, and either can be answered by a platform admin. Whoever accepts replies with how and when, and you see the answer on **Data mesh > Requests**. Nothing is built for you: accepting is the team's promise, recorded in the platform's audit log.

An entity reads a link like any source:

```toml
[source]
link = "crm_customers"
```

At run time the link resolves, through the catalog, to the producer's table in the environment you read, and is read where it lives. To pin an address instead (for example while the producer has no platform yet), add it to the link file:

```toml
[location.prod]
table = "abfss://<workspace id>@onelake.dfs.fabric.microsoft.com/<lakehouse id>/Tables/dbo/dim_customer"
```

The **Links** tab shows where each link points in the picked environment, and which entities use it. A link that an entity still reads can't be removed.

**Which versions a link accepts.** A link can say which versions of the product it can read, and which columns it relies on:

```toml
accepts = "2"        # only version 2; "2-3" is a range, "2+" is version 2 or later

[columns]
customer_id = "int64"
email = "string"
```

A new link saved without `accepts` gets the product's current version from the catalog, so a later breaking version doesn't reach it unnoticed: the producer's promote stops until you widen `accepts` (for example to `"2-3"`) once your project reads the new version. Links made before this have no `accepts` and read any version, as they always did. Anything other than these three forms fails the save gate. Both are kept in the catalog with the link, so the producer's promote can check them.

To widen `accepts` or change `[columns]`, edit `links/<alias>.toml`. Your next promote (or **Publish**) sends each link whose file differs from the catalog, so the producer's promote checks what the file says; a link the catalog refuses (a product no longer open to you, say) is listed as a problem and the publish still goes through. Adding a link again with the same alias and product from **New link** keeps its `accepts` and `[columns]`.

An older UDF_Platform keeps neither. The link still saves, with `accepts` in its file, and says: *A platform admin runs Platform > Settings > Finish or repair once.* The same goes for a product's history: the publish works, and says the history needs **Finish or repair**.

When a table behind a product has an incident, the incident page lists the products built on it, the projects linked to them and who to tell, with **Tell them** to post it in Teams. See [Who's affected](user-guide.md#whos-affected-preview).

## Catalog text (About)

Every entity page has an **About** card: description, tags, classification (Public, Internal, Confidential, Restricted), glossary terms, steward, and a description and classification per column. **Save** writes them to the catalog; no Git change is needed. The project's members (and platform admins) change its catalog text, once the project is in the catalog ([Join the mesh](#join-the-mesh)). **Certified** is changed only by the owners of the project's domain or a platform admin; they don't need to be members to certify. A domain's owner who is also a member of a project can move it into their domain directly and then certify its tables: a domain's owners are trusted to vouch for what's in their domain, so give domain ownership to people you'd trust to certify.

A team can keep starter text with the code, and it shows until someone writes their own:

```toml
[catalog]
description = "One row per customer version"
tags = ["customer", "gold"]
classification = "internal"

[catalog.columns.email]
description = "Primary email address"
classification = "confidential"
```

**Glossary** (**Platform > Set up > Glossary**) holds shared terms like *Active customer*. Platform admins keep shared terms and a domain's owners keep their domain's. Tag a table or column with terms on the entity's About tab. Recent changes in the catalog, and who made them, are on **Platform > Govern > Audit**.

## At scale

- **Hundreds of projects.** Each project is its own repository and its own workspaces; the platform holds only small catalog rows and keys, and every write is one function call.
- **Many teams, one key.** Keys are issued by the platform for everyone, so Customer ABC has one key in every team's tables (see [The platform](platform.md#how-keys-are-issued-at-scale)).
- **No copies.** Links read the producer's table where it lives. Use OneLake data access roles on the Product workspace to control what each consumer may read.
- **Safe change.** A producer can't silently break consumers: ports are named, deprecations carry a date, and the catalog lists who reads each product.

## Related

- [The platform](platform.md)
- [Metadata framework guide](metadata-guide.md)
- [Working across teams](teams.md)
