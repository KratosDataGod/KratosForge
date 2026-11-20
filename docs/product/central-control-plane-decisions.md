# Central control plane: architecture decisions

Decisions for the [central control plane plan](central-control-plane-plan.md). Each one says what
was decided, why, and what would change it. Status: **Accepted** means this thread proceeds on it;
**Proposed** means it waits for Chris.

## CP-ADR-001 Reuse the two existing central stores; don't build a third

**Accepted.** The control plane is an identity, a setup flow and a contract over the Platform
database (`central/core.py` via `UDF_Platform`) and the central lakehouse families
(`optimize/schema.py`). No new database, lakehouse or table family is created for the control plane
itself.

Why: the audit found both stores already have the properties the brief asks for: server-side
authorization, idempotent writes, additive versioned migrations, read-only on a newer schema, a
durable local outbox, random installation ids. A new store would break guardrails 1, 2 and 9.

Would change if: a live run shows `UDF_Platform` can't be called with the Fabric CLI public client
(the open risk in `docs/platform.md`). Then the operational provider needs another transport, but
the schema in `core.py` still stands.

## CP-ADR-002 Name it "KratosForge Environment"; keep "environment" for Dev/Test/Prod in code

**Accepted.** In the UI it is a *KratosForge Environment*. In code the package is `controlplane`
and the type is `CentralEnvironment`, with ids prefixed `kfenv_`. Dev, Test and Prod stay
*deployment environments* (`meta/project.ENVIRONMENTS`), and Fabric Environment items keep their
name.

Why: "environment" already means three things in this codebase (gap G9). A distinct code name
prevents a `env="prod"` argument being read as an environment id.

## CP-ADR-003 Identify resources by id, never by name; adopt the brief's default names for new environments

**Accepted.** The manifest stores GUIDs. Display names are labels. New environments default to
workspace `KratosForge`, lakehouse `KratosForgeLakehouse`, operational database
`KratosForgeMetadata`; every name is editable. Existing installs keep `FabricDesktop_Central`,
`SQL_Platform` and their Platform workspace names.

Why: the brief requires names not to be the sole identifier; today's Platform join finds items by
name (gap G2), which breaks on rename.

## CP-ADR-004 The manifest lives in the analytical store as a file

**Accepted.** `Files/_kratosforge/environment.json` in the environment's lakehouse, written with an
ETag-conditional write. A local cached copy lets the app work offline.

Why: the lakehouse is the one store every environment has (the operational store is optional), it
is readable with the OneLake access the app already uses, and a file avoids a new table family.
Discovery reads one small file per lakehouse instead of listing tables.

Alternatives considered: a row in `fdopt_config` (ties the environment to Optimize's family and
needs a Delta read to discover); a row in `SQL_Platform` (not present in lakehouse-only
environments); a Fabric item tag or description (not reliable, editable by anyone with access).

## CP-ADR-005 Operational store: reuse the Prod Platform database when one exists

**Proposed (recommendation; proceeding on it for design, nothing built depends on it in Phase 1).**
When an organization already runs a Platform, the environment's operational store is the Prod
`SQL_Platform` reached through `UDF_Platform`, where the catalog already lives (`CATALOG_ENV =
"prod"` in `central/service.py`). Only an organization with no Platform gets a new
`KratosForgeMetadata` database, and only when a feature that needs it is turned on.

Why: guardrail 9 (reuse a supported database), guardrail 2 (no competing registries), and the
catalog is already Prod-scoped.

Open question for Chris: should a brand new organization get the Platform layout (a Platform
workspace per Dev/Test/Prod) or the single `KratosForge` workspace from the brief? This plan
recommends the single workspace for the environment, with Platform workspaces added later per
deployment environment exactly as today.

## CP-ADR-006 Lakehouse-only is a valid environment

**Accepted.** `operational_store` may be empty. Optimize, savings, assessments, history and the
cost view work without it. Keys, catalog, requests and policies say "needs the operational
database" with the cost and permission it needs, and offer to add it.

Why: a Fabric SQL database and user data functions need an F2 or larger capacity (or trial); the
brief says never silently create paid resources and determine whether the database is needed.

## CP-ADR-007 Databricks and Snowflake: declared, contract-tested, not offered

**Accepted.** Both providers get a capability declaration (every capability *unsupported*, with a
`planned` note) and contract-test skeletons that skip with a reason. The setup screen lists them as
*Not available yet*. A provider becomes selectable only after it passes the contract suite and a
recorded live end-to-end run, and `platforms.py` evidence rules apply.

Why: the brief forbids claiming parity without testing; `engines/capabilities.py` already enforces
the same rule for execution.

## CP-ADR-008 No new sync channel; families keep their own opt-in

**Accepted.** The control plane does not upload anything itself except the manifest it was asked
to write. What a feature syncs, and whether it syncs automatically, stays with that feature's
existing rules (ESI D4: auto-sync off by default, one opt-in per installation). The control plane
shows one combined sync status.

Why: guardrail 14 (don't centralize sensitive data automatically) and the existing outbox already
handles retries, idempotency and conflicts.

## CP-ADR-009 Migrations lock through the manifest, run per store

**Accepted.** An upgrade sets `migration.state = running` with the installation id and an expiry,
through an ETag-conditional manifest write, then runs each store's own migrations
(`core.ensure_*`, `setup.initialize(approve=True)`), then clears the state. A stale lock past its
expiry can be taken over after a confirm. Destructive migrations are not allowed (both stores are
additive-only today).

Why: two installations upgrading at once is the remaining concurrency gap; each store's steps are
already idempotent, so the lock only prevents wasted work and confusing status, not corruption.
