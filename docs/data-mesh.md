# Data mesh

Fabric Desktop lets many teams build on one platform without stepping on each other. A central team runs the [platform](platform.md): one business key system and one landing zone for everyone. Bronze processing is decentralized: each team builds its own Bronze from the landing zone, and everything after it; domain teams own their projects, publish **data products**, and read each other's products through **links**. Everything a team builds stays in its own Git repository.

!!! note "Preview"
    The Mesh tabs are tagged Preview: they're tested on the PC but not yet on a live tenant.

## The pieces

| Piece | What it is | Where it's kept |
| --- | --- | --- |
| **Landing zone** | Raw files from every source, landed once for everyone. Entities read it with `[source] landing = "<source>/<entity>/*.csv"` | `LH_Landing` in the Platform workspace |
| **Surrogate keys** | One key per business key across every team | `SQL_Platform` |
| **Domain** | An area of the business that owns its data, like Sales or Finance. Subdomains go one level deep (Sales > EMEA) | The platform's catalog |
| **Project** | A metadata project (one Git repository) and the domain and team it belongs to | `project.toml` in the project, and the catalog |
| **Workspace designation** | What a workspace is for: Ingest, Product, Serve or Sandbox | `environments.toml` and `layers.toml` |
| **Data product** | The tables (ports) other teams may read from a project, with an owner, a description and who may read it | `metadata/products/<name>.toml`, published to the catalog on promote |
| **Link** | A project's reference to another team's product port, read where it lives with no copy | `metadata/links/<alias>.toml` |
| **Catalog text** | Descriptions, tags, classification, glossary terms and steward of tables and columns | The platform's catalog (starter text can live in the entity file) |

Logic lives in Git and goes through review and promote. Catalog text lives in the platform, so anyone can describe a table without a pull request.

## Join the mesh

**Metadata > Mesh > Domain and workspaces.** Pick your domain (and subdomain, if any), your team and a description, then choose **Join the mesh**. That writes `project.toml` at the top of the project:

```toml
[project]
name = "crm"
domain = "sales"
subdomain = "sales-emea"
team = "CRM analytics"
format = 2
```

Nothing else in the project changes, and a project without `project.toml` works exactly as before. Domains come from the platform; the central team creates them in **Your organization's domains** on the same tab.

## Workspaces by designation

One environment can spread its lakehouses over several workspaces, so access follows purpose:

| Designation | Typical layers | Who has access |
| --- | --- | --- |
| Ingest | Bronze | The team only |
| Product | Silver, Gold | The team; consumers read through links and OneLake access roles |
| Serve | Semantic models, reports | Report readers |
| Sandbox | Anything | Whoever is trying something |

On **Domain and workspaces**, pick or create a workspace for each designation and environment (a new one is named like `Sales Ingest - Test`). On **Design > Layers**, set each layer's **Workspace**. A layer with no designation stays in the environment's own workspace, and `LH_Meta` always does.

## Data products

**Metadata > Mesh > Data products > New data product.** Choose the tables to share (each becomes a **port**, with the name consumers use), an owner, and who it's **open to**:

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

**The product gate.** A product open to anyone or to the domain can't be published, and its project can't be promoted, until it has a description and an owner and every port has a table description and a description on every column. The entity's **About** card shows what's missing.

## Links

**Data products > Other teams' products > Link**, or **Links > New link.** Pick a product and port, a name for it, and which of their environments each of yours reads. Dev usually reads their Prod, so you build against real data without copying it. The catalog refuses a link to a draft, to a private product, or to a domain product from another domain.

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

## Catalog text (About)

Every entity page has an **About** card: description, tags, classification (Public, Internal, Confidential, Restricted), glossary terms, steward, and a description and classification per column. **Save** writes them to the catalog; no Git change is needed.

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

**Glossary** (Metadata > Mesh > Glossary) holds shared terms like *Active customer*. It also shows recent changes in the catalog and who made them.

## At scale

- **Hundreds of projects.** Each project is its own repository and its own workspaces; the platform holds only small catalog rows and keys, and every write is one function call.
- **Many teams, one key.** Keys are issued by the platform for everyone, so Customer ABC has one key in every team's tables (see [The platform](platform.md#how-keys-are-issued-at-scale)).
- **No copies.** Links read the producer's table where it lives. Use OneLake data access roles on the Product workspace to control what each consumer may read.
- **Safe change.** A producer can't silently break consumers: ports are named, deprecations carry a date, and the catalog lists who reads each product.

## Related

- [The platform](platform.md)
- [Metadata framework guide](metadata-guide.md)
- [Working across teams](teams.md)
