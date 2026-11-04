# Fabric App Studio (proposed)

**Status: proposed, Phase 1 in progress.** App Studio lets a person describe an application, have
their own AI model turn the description into a structured application specification, preview it
locally, change it by asking or by editing, save it as a Git-friendly project, and publish it to a
destination Microsoft actually supports. This page is the verified starting point: what already
exists to reuse, what Microsoft supports for each kind of application, the architecture, the stories
and the smallest end-to-end slice.

> Connect your AI. Describe your application. Build it. Test it. Save it. Publish it.

## How App Studio fits "AI-ready, not AI-dependent"

KratosForge today has **no AI of its own**: external tools connect to it over MCP
([AI integration](ai-integration.md)). App Studio is the first place KratosForge itself calls a model,
and only one the person connects, with their own key and billing. The rules that keep the rest of the
product AI-independent:

- App Studio is **off until a model connection is added**. Every other page works without one, and an
  App Studio project opens, previews, validates, saves and publishes without one too: the model only
  writes the specification, it is never needed to run it.
- The **specification is the source of truth**, not the conversation. Projects carry no transcript.
- What goes to the model is **schema and sanitized metadata** by default (table, column and measure
  names, types, descriptions). Sample rows need a per-connection consent and are redacted first.
- Model output is **untrusted input**: it is parsed into the specification's schema, validated, and
  applied as patch operations. It never runs as code and never calls engine methods itself.

## What already exists to reuse

Checked on `main` at 3c87ab0 (2026-11-03). Each row is a module that App Studio calls, not copies.

| Need | Reuse | Where | Notes |
| --- | --- | --- | --- |
| Fabric sign-in as the person | `Authenticator` | `sidecar/fabric_desktop/auth.py` | Fabric CLI public client + WAM, no app registration. |
| Power BI token for embedding | `pbi_embed_token` | `powerbi_embed_service.py` | Memory only, only to `app.powerbi.com` (security conditions in [Power BI embedding](power-bi-embedding.md)). |
| Embed a report, read pages | `pbi_embed_content`, embed host `pbiembed:` | `powerbi_embed.py`, `app/src-tauri/embed-host` | Embed Studio (#1136 in review) is the live preview for Power BI targets. |
| List semantic models, write TMDL | `list_semantic_models`, `save_semantic_model`, `get definition` | `fabric_api.py` | Same client Embed Studio and the metadata framework use. |
| Generate a Direct Lake model | `semantic.py` (TMDL) | `semantic.py` | Used when an app starts from lakehouse tables with no model. |
| Model checks | BPA, Direct Lake check, data dictionary | `model_bpa.py`, `model_directlake.py`, `model_dictionary.py` | Run against an app's model before publish. |
| Create any item with a definition | `save_item` | `fabric_api.py` | Create-or-update by name; publish adds an explicit "replace" confirm on top. |
| Local data and SQL | Local data engine, DuckDB | `localdata/`, `sql_exec.py`, `engines/` | Local preview data and data-binding checks. |
| Secrets in Windows | DPAPI, `credman:` references | `dpapi.py`, `connprofiles.py` | Model keys use Credential Manager references; never values in files. |
| Redaction | `redact.secrets`, `redact.one_line` | `redact.py` | Everything that leaves for a model, a log or an error passes through it. |
| Atomic project files | `write_text`, `read_json` | `jsonfile.py` | Never a half-written spec. |
| Audit trail | `audit.py`, `audit_chain.py` | engine | Material changes and every publish are audited. |
| Approvals for tools | integrations approvals | `integrations/` | External AI tools may later read App Studio projects through the existing MCP surface. |
| Page shell, design system | `features.js`, `styles.css`, `motion.js` | `app/ui` | One page layout; Preview tag; help behind ⓘ. |
| Git | Source control | `git/`, `git_service.py` | Projects are plain folders, so the existing Git page versions them. |

**Not found:** an existing "sales dashboard" implementation in the repository or the project files.
App Studio's reference template is therefore written from the brief, and swaps in Chris's artifacts
when he points to them (marked UNVERIFIED until then).

**Not present, built new:** a model-provider layer (no code calls Anthropic, OpenAI, Gemini or Ollama
today), an application specification, and an App Studio page.

## Microsoft capabilities (verified)

Checked against Microsoft Learn on 2026-11-03. "Verified" means the official page says so;
"UNVERIFIED" means it hasn't been confirmed from an official page or a live run yet.

| Capability | Status | Source |
| --- | --- | --- |
| Create a **Report** item from a definition (PBIR, default; or PBIR-Legacy, not both) | Verified | [Report definition](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/report-definition), [Create Report](https://learn.microsoft.com/rest/api/fabric/report/items/create-report) |
| Report binds to a workspace model by `byConnection` (`semanticmodelid=`) or a sibling folder by `byPath` | Verified | Report definition |
| Create/update a **Semantic model** from TMDL | Verified (already used by KratosForge) | [Item definitions](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/item-definition-overview) |
| **Org app** items: create, get, list, update, delete, get/update definition; audience definition | Verified that the API exists; definition format and publish behaviour UNVERIFIED | [OrgApp items](https://learn.microsoft.com/rest/api/fabric/orgapp/items) |
| Classic **workspace app** (Power BI app) publish by API | Not supported (Power BI REST only reads apps) | UNVERIFIED against current docs; treated as unsupported |
| Embed for your organization: view, filter, bookmarks, cross-filter, drillthrough, export | Verified (Embed Studio design) | [Power BI embedding](power-bi-embedding.md) |
| Embedded **create report on a dataset**, edit, `save`/`saveAs` | Verified; needs Build/Write on the dataset and write scopes | [Create, edit, save](https://learn.microsoft.com/javascript/api/overview/powerbi/create-edit-report-embed-view) |
| **Translytical task flows** (writeback through user data functions and a data function button) | Verified; functions must return `str`; embedded only in secure embed | [Overview](https://learn.microsoft.com/power-bi/create-reports/translytical-task-flow-overview) |
| User data function item definition by REST | Not listed on the item definitions page; UNVERIFIED | Item definitions |
| Dashboards (tiles) created by API | Not supported as a target; embedding existing dashboards is | Power BI embedding |
| Rendering a report without the Power BI service | Not possible; local preview is a **simulation** and is labelled so | Power BI embedding |

## Application targets

Targets are **separate adapters**, each declaring what it can do. The specification says which target
it is for; validation refuses components a target can't express instead of simulating them.

| Capability | A. Power BI report | B. Fabric org app | C. KratosForge-hosted app |
| --- | --- | --- | --- |
| Pages, navigation | Yes (report pages) | Yes (wraps reports) UNVERIFIED | Yes |
| Charts, tables, cards, slicers | Yes (PBIR visuals) | Through its reports | Yes (local renderer) |
| Cross-filter, drillthrough, bookmarks | Yes | Through its reports | Cross-filter yes; drillthrough planned |
| Measures (DAX) | Yes, in the semantic model | Through its reports | Evaluated locally on preview data only |
| Writeback, forms | Translytical task flows (Phase 5) | Through its reports | Phase 5, local engine only |
| Local preview | Simulation on sample data, labelled | No | Yes, full |
| Live preview | Embed Studio after publish to a dev workspace | Fabric portal | Yes |
| Publish | Create Report with PBIR definition | OrgApp create + definition (Phase 5) | Export a project bundle (no hosting target verified) |
| Uses capacity | Yes, on publish and live view | Yes | No (local) |

Phase 2 ships **target A only**. B and C stay "not available" in the target picker until their
adapters are verified.

## Architecture

```
 Describe ─▶ Model adapter ─▶ JSON plan ─▶ Spec validator ─▶ Patch ops ─▶ Canonical spec (app.json)
                 ▲ (BYO key, consent)                              │            │
   Metadata ─────┘ schema only by default                          ▼            ▼
                                                           Undo/redo log   Target adapter
                                                                           ├─ preview (local)
                                                                           ├─ validate
                                                                           └─ publish (official API)
```

- **Model adapters** (`appstudio/providers/`): one interface, `capabilities()` (structured output,
  tool calling, streaming, context size), `validate()`, `complete(request) -> JSON`. Anthropic,
  OpenAI, Azure OpenAI, Gemini, Ollama, and OpenAI-compatible endpoints. Keys are `credman:` references
  read only at call time. Endpoints are https except Ollama on loopback.
- **One generation contract.** The model is asked for a *plan* or a *patch list* in a fixed JSON
  schema; every provider returns the same shape. Free text is never applied.
- **Canonical specification** (`appstudio/spec.py`): app, data sources, theme, pages, components
  with stable ids, measures, interactions, permissions, refresh, targets, tests. Visual and AI edits
  both become the same patch operations.
- **Patch operations**: `add`, `remove`, `set`, `move`, each naming a component id. Applying a patch
  returns its inverse, which is what undo and rollback use. A patch that breaks validation is refused
  whole.
- **Project format** (`appstudio/project.py`): a folder `<name>.KFApp/` with `app.json` (spec),
  `kfapp.json` (format version, targets), `history.jsonl` (patches with inverses), `versions/`
  (named snapshots), `validation.json`, `deploy/history.jsonl`. No secrets, no rows, no transcript.
  A secret scan runs on every save.
- **Preview**: target C renders the spec with the app's own chart code on sample or local data;
  target A renders the same spec as a labelled simulation, and as the real report through Embed
  Studio once it has been published to a development workspace.
- **Publish**: shows sign-in, permissions, workspace, dependencies, capacity use, conflicts and the
  change list first. Existing items are never replaced without an explicit confirm naming them.
  Every publish is audited and recorded with the Fabric item ids.

## Security

- Model keys: Credential Manager references only; never in projects, logs, telemetry, audit lines
  or errors. A test scans a saved project and the log output for the key.
- Data to the model: metadata only by default; rows need consent per connection and pass redaction.
- Prompt injection: retrieved names and descriptions are passed as data in a delimited field; the
  model's reply can only produce spec patches; patches are validated against the schema and target
  capabilities; no generated code runs.
- Spend: per-connection monthly limit and per-request token estimate; warn at 80 %, stop at 100 %.
- Network: https only; no redirects to other hosts; Ollama only on `localhost`.
- Publish and any replace need a confirm; Fabric writes follow the usual typed-yes rule for live runs.

The provider layer is a security-review PR (credentials, outbound calls).

## Stories

Sizes: S under a day, M one to two days, L more. Each is one PR with tests.

| Id | Story | Phase | Size | Depends on | Acceptance |
| --- | --- | --- | --- | --- | --- |
| AS-01 | This page: inventory, capability matrix, stories | 1 | S | none | Merged; matrix cites official pages. |
| AS-02 | Canonical spec: schema, validation, stable ids, dependency index | 1 | M | AS-01 | Invalid specs refused with paths; dependency lookup tested. |
| AS-03 | Patch operations with inverses; undo/redo/rollback | 1 | M | AS-02 | Patch touches only named components; inverse restores byte-equal spec. |
| AS-04 | Project format: save, open, save as, clone, export, import, versions, secret scan | 1 | M | AS-02 | Round-trip tests; secret in spec refused; corrupt file set aside. |
| AS-05 | Model connections: adapter interface, Anthropic/OpenAI/Azure OpenAI/Gemini/Ollama/compatible, Credential Manager refs, validate, capabilities, spend limits, consent | 1 | L | AS-01 | Mocked-HTTP tests per provider; key never in output; security read. |
| AS-06 | Engine RPCs for projects and connections | 1 | S | AS-04, AS-05 | RPC param tests; audited writes. |
| AS-07 | App Studio page shell (Preview): Create / Open / Import, project list | 1 | M | AS-06 | UI tests; hidden when Preview is off. |
| AS-08 | Local preview renderer for spec pages (cards, bar/line, table, slicer, cross-filter) on sample data | 1 | M | AS-02, AS-07 | Browser test renders the reference template. |
| AS-09 | Discovery: semantic model tables, columns, measures as sanitized metadata | 2 | M | AS-06 | Reads only; no rows; redaction tested. |
| AS-10 | Generation: describe → plan (permissions, pages) → approve → spec | 2 | M | AS-05, AS-09 | Fake-model tests; bad JSON refused. |
| AS-11 | Conversational edit: request → change plan → patch → validate → preview | 2 | M | AS-03, AS-10 | Untouched components byte-equal after an edit. |
| AS-12 | Target A compiler: spec → PBIR report bound `byConnection` | 2 | L | AS-02 | Generated definition matches Microsoft's schema; golden files. |
| AS-13 | Publish to a workspace: readiness, conflicts, confirm, audit, history, failure recovery | 2 | M | AS-12 | Mocked Fabric tests incl. failure midway; one live run needs Chris's typed yes. |
| AS-14 | Live view of the published report in Embed Studio | 2 | S | AS-13, #1136 | Opens in Embed Studio. |
| AS-15 | Visual designer: select, move, properties, bindings → patches | 3 | L | AS-08 | Same patch log as AI edits. |
| AS-16 | Themes and version history UI | 3 | M | AS-04 | — |
| AS-17 | Template gallery (seven templates) | 4 | M | AS-12 | Each template validates and previews. |
| AS-18 | Policy: tenant/workspace rules for providers and targets | 4 | M | AS-05 | Sealed settings honoured. |
| AS-19 | Org app adapter (target B) | 5 | L | live verification | Only after the definition format is verified. |
| AS-20 | Writeback through translytical task flows | 5 | L | AS-12 | — |

## Smallest end-to-end vertical slice

AS-02 → AS-04 → AS-05 → AS-06 → AS-07 → AS-08 → AS-09 → AS-10 → AS-11 → AS-12 → AS-13:
connect one model, pick one semantic model, describe a sales dashboard, get a spec with two pages
(overview with cards, revenue by month line, revenue by region bar, region slicer; customers table),
preview it locally, ask "change the region chart to a column chart", save, reopen, publish one PBIR
report bound to the chosen model to a development workspace, and open it in Embed Studio.

## Ownership and reviews

App Studio owns `appstudio/` and its page. Embed Studio, the semantic-model tools and the engines
stay with their owners; App Studio calls them. Review route: owner → Second quality → Security
(credentials, model keys, outbound calls, CI gates) → Release management, who merges.
