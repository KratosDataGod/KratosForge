# Semantic engineering: Apache Ossie, Fabric Semantic Views, TMDL and parity

Status: Phase 0 plan for review (2026-11-18). Owner: TMDL/Ossie thread. Requested by Chris
(semantic engineering master prompt, 2026-11-18 23:37Z; go given 2026-11-18). Story ids are
**OSSIE-nnn**; a letter suffix (006a, 006b) marks a split of one of Chris's 15 stories.

This plan extends what exists. It adds no second Ossie layer, no second TMDL writer, no second
registry and no second lineage service. Ossie stays in `model_ossie.py` and the vendored
converter, TMDL stays in `tmdl.py`, metrics extend the platform catalog in `central/core.py`,
lineage extends `meta/lineage.py` and `meta/change_impact.py`, semantic findings become
`opportunities/` analyzers, and AI tool access stays in `integrations/capabilities.py`.

Inputs: the code audit of `origin/main` at `1aa53796` (Phase 0 repo audit), the official-docs
research verified on 2026-11-18 (Phase 0 platform research), and the existing plans named in each
story. Standards: `KratosDataGod/ai-commons` `AGENTS.md`, its `standards/`, the KratosForge
project context and the mission prompt's hard gates. Paths below are relative to the repository
root; `sfd/` means `sidecar/fabric_desktop/`.

## Executive assessment

- KratosForge already converts Power BI semantic models to and from Ossie on this PC, with the
  Apache converter vendored and its own TMDL reader and writer. 401 semantic tests pass.
- None of it has run against a real tenant (FEAT-META-19 and FEAT-META-13 wait on WR-502).
- There is no Ossie authoring, no schema validation, no version compare and no project concept.
- There is no Fabric Semantic View support and no Snowflake semantic view support at all.
- There is no DAX parser or evaluator. DAX behavior cannot be tested on this PC, so behavioral
  parity for DAX is blocked until a live run. Structural parity can start now.
- The three platforms pin three different Ossie versions (0.3.0.dev0, 0.2.0.dev0, 0.1.1). One file
  cannot serve all three. Each target needs its own output profile.
- Fabric Semantic Views are Preview with no documented publish API, so they stay file-only with a
  manual publish.
- The base is sound enough to build on. The first increment is OSSIE-004a, the five-class fidelity
  report, because it is engine-only, needs no mockup and gives parity its vocabulary.

## A. Current state

Legend (from the audit): **Verified** implemented and tested, **Incomplete**, **Defective**,
**Doc-only** documented but not implemented, **Missing**, **Blocked** by an external dependency.
"Branch" means built and reviewed on a local branch, not merged.

### A.1 Ossie

| Feature | Status | Evidence |
| --- | --- | --- |
| Vendored converter | Verified | `sfd/_vendor/ossie_microsoft/` from `apache/ossie-converters` commit `82b9eff` (2026-10-22), package 0.2.0.dev0, Apache-2.0. Tests `test_ossie_vendor_converters.py` (28), `test_ossie_vendor_translators.py` (25), #3025. Copied by hand; not on PyPI |
| Spec version accepted | Verified, strict | `_common.py:52` `OSSIE_VERSION = "0.2.0.dev0"`; `model_ossie.py:146` refuses any other version; `:142` refuses the `semantic_model` wrapper |
| Export, Fabric model to Ossie | Verified, no live run | `meta_model_ossie_export` (`model_ossie_service.py:114`), `model_ossie.export` (`:152`); UI `ossieCard` (`app/ui/meta.js:1733`); `ossie-card.test.cjs` |
| Export from a Git folder (TMDL or `model.bim`) | Verified | `meta_model_ossie_export_folder` (`model_ossie_service.py:140`); `test_a_model_kept_in_git_exports_offline` (#777) |
| Import, Ossie to Fabric (update, replace, new) | Verified, no live run | `meta_model_ossie_preview`, `_import` (`:229,240`); `to_tmsl`, `merge`, `plan`, `removable`; confirm required; `test_import_previews_then_writes_only_when_confirmed` |
| Save or read an Ossie file | Verified | `meta_model_ossie_save`, `_read` (`:182,190`). Saves only what Export made |
| Ossie file to TMDL, no Fabric call | Engine Verified (#2910, slice 2); UI in review | `meta_model_ossie_to_tmdl` (`:290`) with preview, token and confirm. UI and Round-trip check in PR #3039 (slice 3) |
| Kept and lost report | Incomplete | `_convert` (`model_ossie.py:113-125`) labels rows by substring ("preserved", "kept as-is"). Informational notes show as lost (audit R4) |
| Merge keeps unset properties | Branch `pb110-keep` | Table, column and measure properties the file doesn't set survive an update (`7051a3da` and follow-ups) |
| AI tool access (read-only) | Verified | `integrations/capabilities.py:352-390`: list models, export, preview import. No import tool, on purpose |
| Authoring or editing | Missing | No editor RPC or UI; export YAML shown read-only (`meta.js:1816`) |
| Schema validation | Missing | No JSON Schema vendored; `parse()` checks size, aliases, version, name, datasets only. `jsonschema==4.26.0` is in `sidecar/constraints.txt:29` but nothing in `sfd/` imports it |
| Version compare or migration | Missing | Only the equality check |
| Ossie project (create, open) | Missing | Files are plain `semantic/*.ossie.yaml` |
| Docs | Verified, drifting | `docs/semantic-model-ossie.md`; `FEATURE_INVENTORY.md:2496` still says "Engine only" (audit R6) |

### A.2 TMDL

| Feature | Status | Evidence |
| --- | --- | --- |
| Reader (TMDL to TMSL) | Verified, gap on main | `tmdl.py:139` `_Reader`, `:302` `to_bim`; `test_tmdl.py` (7), `_edges` (7), `_mutation` (12). Cultures reported lost on main; round-trip on branch `pb110-cultures` |
| Writer (TMSL to TMDL) | Verified | `tmdl.py:424` `_Writer`, `:576` `from_bim`, reads every write back; `test_tmdl_write.py` (18), #2592. Randomized hostile tests on branch `pb110-fuzz` (`test_tmdl_fuzz.py`) |
| Round-trip check | Incomplete on main | `tmdl.differences` (`:675`), `model_ossie.round_trip` (`:368`) called only by tests; wired into export and UI by #3039 |
| Save TMDL to Git | Defective on main | `meta_model_tmdl_save` (`model_ossie_service.py:262-288`): no link checks and removes files Fabric didn't return (audit D2). Link checks added in #3039 |
| Second TMDL emitter | Risk | `sfd/semantic.py:171,207` writes TMDL by hand, apart from `tmdl._Writer` (audit R3) |

### A.3 Semantic model features

| Feature | Status | Evidence |
| --- | --- | --- |
| Semantic model tab | Verified | `renderModelTab` (`meta.js:1446`); Test or Prod only |
| Direct Lake model build | Verified, live run pending | `meta_build_model` (`cloud_service.py:814`); `semantic.build`; `test_semantic_mutation.py`, `test_model_date_table.py`; FEAT-META-13 waits on PB-002 |
| Direct Lake check | Verified | `model_directlake.py`, `directLakeCard` (`meta.js:1631`); `test_model_directlake*.py` |
| BPA in Fabric | Incomplete and Blocked | `model_bpa_service.py`; semantic-link-labs 0.17.1; all three RPCs in `NO_UI_CALLER` (`test_rpc_surface.py:45-47`); not run live (PB-115) |
| Best practices on this PC (SM001-SM007) | Verified | `model_practices.py:37-60`; `best_practice_checkers.py:74`; PB-112 (#753). Checks only the generated model |
| Data dictionary | Verified | `model_dictionary_service.py`, `dictionaryCard` |
| DAX handling | Incomplete | Carried as strings only; no DAX parser, linter, formatter or evaluator outside `_vendor` |
| Measure editor | Missing | `artifacts.py:60-63`: reads TMDL "but doesn't edit it" |

### A.4 Fabric Semantic Views, Fabric IQ and ontology

| Feature | Status | Evidence |
| --- | --- | --- |
| Semantic View | Missing | `rg -in 'semantic[ _-]?view'` outside `_vendor` finds nothing |
| Fabric IQ | Doc-only | `COMPETITIVE_CAPABILITY_MATRIX.md:68,103` (competitive note) |
| Ontology item | Incomplete | Type listed only: `artifacts.py:103` |

### A.5 Snowflake

| Feature | Status | Evidence |
| --- | --- | --- |
| Snowflake SQL on this PC (DuckDB) | Verified | `snowflake_sql.py`, `snowflake_coverage.py`, about 12 `test_snowflake_*` files; never compared with an account |
| Remote engines | Doc-only | `engines/registry.py:31-53`: SF-07 SQL API, SF-08 Snowpark local, SF-10 Snowpark Connect, all `planned=` |
| Connection profiles | Incomplete | `connprofiles.py`; profiles validate and store, nothing connects |
| Snowflake semantic views | Missing | No code. Ossie export is the only bridge |
| sqlglot dialects | Verified | `sqlglot>=30.22,<30.28`, pinned 30.23.0; CI tests both ends of the range |

### A.6 Lineage, registries, Optimize, Academy, deployment, testing

| Feature | Status | Evidence |
| --- | --- | --- |
| Column lineage | Verified | `meta/lineage.py`; `meta_lineage`; `test_lineage.py`, `lineage-impact.test.cjs` |
| Change impact on models | Incomplete | `change_impact.py:218-245` covers only `SM_<LastLayer>` (audit R2) |
| Measure lineage | Missing | Nothing parses DAX for column references |
| Platform catalog (shared registry) | Incomplete, other name | `central/core.py:15-60`: glossary, enrichment, product_item, key_table |
| Glossary | Verified | `catalog.glossary`; free-text terms, no link to a measure |
| Metric registry | Missing | No store. Only mention is OE-013 in the FEA plan |
| Semantic findings in Optimize | Doc-only | `docs/optimize/engine-plan.md` 3.4 Missing, 4.5 Partial, OE-013, OE-023 |
| ESI savings | Verified (pure) | `sfd/savings/`; a semantic finding has no CU figure, so it is Unknown |
| Academy | Verified (thin) | DP-700 E45 (draft, simulated), LM-09, LAB-07; nothing on Ossie, TMDL, DAX or measures |
| Deploy a model across environments | Missing | Each environment builds its own model |
| Fabric workspace Git | Incomplete | `fabric_git.py` reads connection and status only |
| Model fixtures | Incomplete | No `.bim`, `.tmdl` or `.ossie.*` files; models are inline in tests |
| CI lanes | Incomplete | `.github/workflows/ci.yml` has no semantic lane; `tsql-contract-live.yml` is the live-lane pattern |
| Parity against Fabric | Blocked | No measured DAX comparison; WR-502 |

### A.7 Work in flight

| Item | State | What it carries |
| --- | --- | --- |
| PB-110 slice 1, #2592 | Merged | TMDL writer, TMDL to and from Ossie with nothing dropped silently |
| PB-110 slice 2, #2910 | Merged | Ossie file to TMDL in the project, preview, token and confirm |
| PB-110 slice 3, #3039 | In review | Card UI for Ossie file to TMDL, Round-trip check, link checks on Save TMDL (fixes D2 and D3) |
| `pb110-fuzz` | Built and reviewed | `test_tmdl_fuzz.py` and the writer fixes it found |
| `pb110-cultures` | Built and reviewed | Cultures and translations round-trip in reader and writer (fixes D4) |
| `pb110-keep` | Built and reviewed | Merge keeps properties the file doesn't set; D1 fix `85707981` |

## B. Official platform capabilities

All claims verified on **2026-11-18** from official sources: `raw.githubusercontent.com/apache/ossie`
and `apache/ossie-converters` at `main`, ossie.apache.org, learn.microsoft.com, docs.snowflake.com,
the NuGet API. GitHub tags, releases and commit history could not be reached, so commit dates are
missing and some pins are unverified. Nothing below is from community sources unless marked.

### B.1 Key finding: three platforms, three Ossie versions

| Platform | Ossie version | Extension key | Source |
| --- | --- | --- | --- |
| apache/ossie `main` (spec and JSON Schema) | `0.3.0.dev0`, unreleased; schema `"const": "0.3.0.dev0"` | `nonportable_extensions`, `data` an object | `core-spec/spec.md`, `core-spec/ossie-schema.json` |
| Fabric Semantic Views (Preview) | `0.2.0.dev0`: "Set `version` to `0.2.0.dev0` in every YAML file" | `custom_extensions` (JSON string), omitted with warnings | learn.microsoft.com `semantic-views-yaml-reference`, updated 2026-11-17 |
| Snowflake Ossie functions (Preview) | `0.1.1` only, document wrapper format | `custom_extensions` | docs.snowflake.com `system_create_semantic_view_from_ossie_yaml`, `system_read_ossie_yaml_from_semantic_view` |

- Ossie moved `main` from 0.2.0.dev0 to 0.3.0.dev0 on 2026-11-09 (PR #691) and renamed
  `custom_extensions` to `nonportable_extensions` on 2026-11-10 (PR #522). Both are breaking
  (`docs/spec_changes.md`).
- A document valid against today's `main` schema is rejected by both Fabric and Snowflake.
- KratosForge's vendored converter and `parse()` sit on 0.2.0.dev0, the Fabric version. The
  upstream Microsoft converter README now says it targets 0.3.0.dev0. **Updating the vendor would
  move KratosForge off the version Fabric accepts.** Version profiles (OSSIE-003b) must exist first.

### B.2 Apache Ossie

| Capability | Status | Source (2026-11-18) |
| --- | --- | --- |
| ASF incubating, formerly OSI; accepted 2026-07-10 | Documented | ossie.apache.org; README |
| 0.1.1, the only release (2025-12-11) | GA | spec.md version history |
| 0.2.0.dev0 | Superseded draft, "no 0.2.0 release" | spec.md; spec_changes.md |
| 0.3.0.dev0 | Draft: "do not depend on this version in production" | spec.md |
| Release tags or artifacts | Not documented; probes for 0.1.1 tags returned 404 | ossie.apache.org |
| JSON Schema, draft 2020-12 | Documented; pin `apache/ossie@aa500d37894c3d62c96cdce4192cbecea4d7f06a/core-spec/ossie-schema.json` (const checked) | schema file |
| 0.2.0.dev0 schema pin | Not verified: no SHA; that schema used the wrapped shape, Fabric's YAML is flat | research section A |
| Local validator `validation/validate.py` (`jsonschema>=4.26.0`) | Documented | repository |
| Python SDK `apache-ossie` | Repository only; PyPI 404 | `python/pyproject.toml` |
| Ontology layer | Preview | README |

Core 0.3.0.dev0 constructs: root (`version`, `name`, `datasets`; `additionalProperties: false`),
datasets, fields with `datatype`, metrics (model-scoped ones use `dataset.field`), relationships
(`from` is the many side; columns are fields from 0.3), expressions (`expression.sql` in
`OSSIE_SQL_2026` or `dialects[]`), extensions ("MUST preserve `data` they do not understand"), and
`ai_context` at every level.

### B.3 Apache Ossie converters

| Converter | Version | Direction | Status |
| --- | --- | --- | --- |
| Microsoft (Power BI and Fabric models) | 0.2.0.dev0 pyproject; README targets 0.3.0.dev0 | Both; TMSL by default, TMDL with the optional TOM extra | Pre-release; PyPI 404 |
| Snowflake (to Cortex Analyst YAML) | 0.2.0.dev0 | Export only | "use with caution in production" |
| dbt / MetricFlow | 0.2.0.dev0 | Both | Pre-release; README says `pip install`, PyPI 404 |

The Microsoft converter keeps calculation groups, RLS roles, perspectives, translations,
hierarchies and KPIs in its `POWER_BI` stash ("preserved, but not modelled"). It reports inactive,
many-to-many and composite relationships as "no equivalent". It translates DAX to SQL only for a
documented subset and refuses time intelligence. "Offline TOM does not parse or validate DAX."
It lives in the Apache repository, with two `@microsoft.com` maintainers.

### B.4 Fabric Semantic Views (Preview)

| Capability | Status | Source (page updated 2026-11-17 unless noted) |
| --- | --- | --- |
| Feature | Preview | `semantic-views-overview` |
| Not a standalone item or query engine; source YAML in lakehouse `Context/Semantic Views/<view>/`, read-only generated TMDL in `SystemContext/Semantic Views/<view>/` | Preview | overview |
| Publish creates a managed, read-only Direct Lake on OneLake model; YAML stays the source of truth; model name = folder name; measures in a `Metrics` table | Preview | overview; `semantic-views-publish`; whats-new |
| Supported YAML: root `version, name, description, datasets, relationships, metrics` | Preview | yaml-reference |
| Omitted with warnings: `fields`, `primary_key`, `unique_keys`, `ai_context`, all `custom_extensions`; metric `datatype` ignored; single-column relationships only | Preview limit | yaml-reference |
| Only `DAX` metrics publish; other dialects "recognized but not published" | Preview | yaml-reference |
| Limits: 20 `.yaml` files (`.yml` ignored), 16 MB YAML, 30 MB generated TMDL | Preview | yaml-reference; `semantic-views-create` |
| DAX query view, reports, data agents, composite models on top | Preview | `semantic-views-consume` |
| XMLA, Edit in Desktop, REST edits, version history on the generated model | Not supported | consume; overview |
| REST item type, publish endpoint, Fabric CLI type, SDK | Not documented. "publishing a semantic view through the Fabric public API with a third-party token isn't supported" | list-items; overview; fabric-cli resource types |
| Writing `Context/` through OneLake APIs | Not documented for views | `onelake-access-api` (2026-06-23) |
| Git integration and deployment pipelines | Not supported; cannot publish in a Git-connected workspace | overview; publish |
| Permissions | Lakehouse Write plus Contributor to create and publish | overview; consume |
| Licensing, SKUs, regions, tenant switch | Not found | overview |

### B.5 Snowflake semantic views

| Capability | Status | Source (no page dates on Snowflake pages) |
| --- | --- | --- |
| Querying semantic views | GA (release 9.25, 2025-08-26) | release notes |
| `CREATE SEMANTIC VIEW`; some sub-features Preview | Core unlabelled | `create-semantic-view` |
| `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(..., verify_only)` | No status stated; `verify_only=TRUE` validates without creating | stored procedure page |
| `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW` | No status stated | function page |
| `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML`: 0.1.1 only; fields and metrics without a `SNOWFLAKE` or `ANSI_SQL` expression are **silently skipped** | Preview | stored procedure page |
| `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW`: 0.1.1 wrapper, `SNOWFLAKE` dialect only, drops labels and data types | Preview | function page |
| `..._OSI_...` functions | Deprecated | function page |

### B.6 TMDL

| Capability | Status | Source |
| --- | --- | --- |
| TMDL language overview | Documented, no label | `tmdl-overview` (metadata 2026-06-11) |
| `TmdlSerializer` in `Microsoft.AnalysisServices` | GA; NuGet 19.120.1 (2026-11-17), proprietary licence | dotnet API page; NuGet |
| Fabric model definition: TMDL `definition/` or `model.bim`, plus `definition.pbism` | GA (REST v1) | `semantic-model-definition` (2026-03-18) |
| Create Semantic Model REST: `SemanticModel.ReadWrite.All` or `Item.ReadWrite.All` | GA | `create-semantic-model` (2026-10-22) |
| TMDL view in Desktop / on the web | GA / Preview | `desktop-tmdl-view` |

## C. Gap analysis

| Brief capability | Have | Gap | Stories |
| --- | --- | --- | --- |
| A. Native Ossie development | Export, import, save, read, offline folder export, Ossie to TMDL | No project, editor, schema validation, version compare or version profiles | 002, 003, 007 |
| B. Fabric Semantic Views | Nothing | Profile emitter, local validation against documented limits, manual publish steps, read-only view of the managed model | 005 |
| C. TMDL and Ossie interop | Vendored converter, own reader and writer, round trip | Five fidelity classes, full report fields, converter version in the report, upgrade regression | 004, 012 |
| D. Semantic parity validator | Round-trip `differences` only | L1 to L4, result vocabulary, tolerances, controlled datasets, CI. No DAX execution | 006, 012 |
| E. Enterprise metric registry | Platform catalog, glossary, enrichment | Metric records, conflicts, governed versus local | 008 |
| F. Semantic optimization advisor | SM001-SM007, BPA engine, Direct Lake check | Analyzers in `opportunities/`; savings tiers kept apart | 009 |
| G. Lineage and impact | Column lineage, table impact | Measure lineage; any model name; confirmed versus inferred; cross-platform copies | 011 |
| H. Cross-platform deployment | Import to Fabric with confirm | Target compatibility check, Snowflake path, deployment record | 010, 015 |
| I. Fabric IQ and ontology | Ontology type listed | Concept mapping, term consistency | 013 |
| Academy | E45, LM-09, LAB-07 | Ossie, Semantic Views, TMDL, conversion, DAX portability | 014 |
| Defects and risks | D2, D3 fixed in #3039; D1, D4 fixed on branches | R1 strict version, R2 `SM_` assumption, R3 two emitters, R4 substring report, R6 doc drift | 001b, 003b, 004a, 007a, 011 |

## D. Target architecture

### D.1 Four object kinds, kept distinct

| Kind | What it is | Where it lives | KratosForge may |
| --- | --- | --- | --- |
| Ossie definition | A portable document in one Ossie version | `semantic/<name>.ossie.yaml` in the metadata project | Author, validate, convert, diff, commit |
| Semantic View YAML | Fabric's Ossie profile (0.2.0.dev0, flat, DAX metrics) | Lakehouse `Context/Semantic Views/<view>/`; a local copy in the project (folder name set in OSSIE-005a) | Write the local copy, validate it, show manual publish steps. Never write `Context/` or publish |
| Power BI semantic model | A Fabric `SemanticModel` item, TMDL or TMSL | Fabric workspace; `semantic/<model>.SemanticModel/` in Git | Read, convert, preview, import with confirm (exists) |
| Managed Direct Lake model | The read-only model a published view generates | Fabric, named after the view folder | Read only, if a read path is confirmed live (OSSIE-005c). Never write |

Every record, report row and screen names which kind it is about. A conversion between kinds is
always a fidelity report (D.4), never a silent copy.

### D.2 Layers mapped onto existing modules

```
Semantic Model tab (existing)        cards: Ossie, Round-trip, fidelity, validation, deploy steps
   |                                 Optimize (existing): semantic opportunities
   |                                 Data mesh lineage (existing): measure consumers
Engine RPCs                          model_ossie_service.py  (preview, token, confirm; file safety)
   |
Semantic model core                  model_ossie.py  (profiles, fidelity, merge, plan, round trip)
   |                                 tmdl.py         (reader, writer, differences, canonical)
   |                                 _vendor/ossie_microsoft, _vendor/ossie_schema (new, pinned)
Validation                           model_ossie.py validate (L1 schema and profile rules)
   |                                 model_parity.py (new, pure: L1 to L3 results, tolerances)
   |                                 sql_dialects.py, snowflake_sql.py (SQL side on DuckDB)
Governance and analysis              central/core.py catalog (metric records extend glossary)
   |                                 meta/lineage.py, meta/change_impact.py (measure consumer kind)
   |                                 opportunities/ analyzers (OE-013, OE-023), optimize/shared.py
Deployment adapters                  Fabric: fabric_api.save_semantic_model (exists)
                                     Fabric Semantic Views: file plus manual publish
                                     Snowflake: SF-07 SQL API, then SYSTEM$ functions
AI clients                           integrations/capabilities.py (read-only tools only)
```

`model_parity.py` is the only new module. It is pure, like `savings/`, and holds what exists
nowhere today: the parity levels, result vocabulary and tolerances. It is not a registry.

### D.3 Version profiles

One internal model (TMSL dict, as today) and one profile per target:

| Profile | Version | Root | Extensions | Validated by |
| --- | --- | --- | --- | --- |
| `ossie-0.3` | 0.3.0.dev0 | Flat | `nonportable_extensions`, object | Pinned upstream schema `aa500d3` |
| `ossie-0.2` (KratosForge today, vendor) | 0.2.0.dev0 | Flat | `custom_extensions`, string | KratosForge rules derived from the pinned schema, labelled as derived |
| `fabric-semantic-view` | 0.2.0.dev0 | Flat | None (omitted by Fabric) | Documented Fabric rules: DAX metrics, single-column relationships, 20 files, 16 MB |
| `snowflake-ossie` | 0.1.1 | Wrapper | `custom_extensions` | Documented Snowflake rules; marked Unverified until a 0.1.1 schema is pinned |

The strict equality check (R1) becomes "read any known profile, refuse unknown versions by name".
Upgrading the vendor is a separate, tested step behind profiles (OSSIE-012c).

### D.4 Fidelity classes

Every converted object gets one class: **Fully preserved**, **Preserved through extensions**,
**Partially converted**, **Unsupported**, **Unverified**. A report carries source and destination
kind and version, converter commit, objects converted and excluded, metadata changes, warnings,
validation results and remediation. RLS roles and calculation groups can never fall into a silent
class: they are either Preserved through extensions or Unsupported with a named row. The source
file is never changed by a conversion, so it stays recoverable.

### D.5 Parity levels and results

L1 structural (objects, keys, relationships, types), L2 expression (normalized expression text per
dialect), L3 behavioral (both sides run on the same controlled dataset), L4 performance.
Results: **Verified equivalent**, **Equivalent within tolerance**, **Different results**,
**Structurally compatible but behavior unverified**, **Unsupported**, **Test failed**. DuckDB and
Polars run the SQL side only. A DAX side with no execution path is always "behavior unverified",
never equivalent. Tolerances are configuration, with defaults stated in the result.

## E. Backlog

**Shared rules for every story.** Each story is one PR unless it says otherwise.
- **Definition of done (standard):** code and tests on main; the RPC listed in `server.METHODS`,
  `test_rpc_surface.py` and `docs/audits/AUTHORIZATION_MATRIX.md` where it adds one; docs updated
  in `docs/semantic-model-ossie.md`; Quality PASS on the final head; Release management merges;
  Preview tag on any new UI.
- **Security (standard):** imported files are untrusted data: parsed, never executed; size and
  depth limits; no YAML aliases; writes only through `model_ossie_service.py`'s `_real`,
  `_no_links` and preview-token-confirm helpers; no secrets in logs or reports; Security QC read on
  any new parser, file write or Fabric write.
- **UX (standard):** any new screen or panel starts as a mockup through the UX thread, then Chris
  reacts, before build. "No mockup" means engine only or copy only.
- **Tests (standard):** deterministic, no paid capacity, cloud kept apart from local, and an
  unexecuted check is never reported as passing.
- **Where a story leaves a field out, the standard above applies.**
- **Program definition of done:** the brief's 13-step developer workflow, from open through
  define, validate, convert, test, optimize, preview, deploy and verify, to Git, works end to end.
  The DXE audit thread checks it after each phase.

### Phase 0: discovery

**OSSIE-001 Audit.** Done by this plan.
- Problem: no evidence-backed view of semantic features. Value: every later story starts from
  facts. Evidence: Phase 0 repo audit and platform research (A, B). Scope: audit, research, plan.
  Out of scope: code. Dependencies: none. Design: n/a. Acceptance: every feature classified with
  evidence; sources dated. Tests: the 401-test baseline run. Security: no tenant touched. UX: none.
  DoD: this plan merged. Owner: TMDL/Ossie thread. Priority P0: everything depends on it.

**OSSIE-001b Inventory and docs corrections.** Extends PB-110 and FEAT-META-19.
- Problem: `FEATURE_INVENTORY.md:2496` says "Engine only"; `docs/semantic-model-ossie.md` says the
  spec is at 0.2.0.dev0. Scope: correct both; record the version skew; add PB-110's merged and
  in-flight slices. Out of scope: code. Acceptance: inventory and docs match A.7 and B.1.
  Tests: existing doc checks. UX: copy only, no mockup. Owner: TMDL/Ossie thread.
  Priority P0: wrong docs mislead users about what ships.

### Phase 1: core

**OSSIE-002 Local Ossie project management.** Extends PB-110.
- Problem: an Ossie file is a loose YAML file; there is no way to start, open or compare one.
- Value: a developer works on semantic definitions offline in the metadata project, under Git.
- Evidence: `meta_model_ossie_folders`, `_read`, `_save` (`model_ossie_service.py:131,182,190`);
  no project concept (A.1).
- Dependencies: #3039 merged; OSSIE-003a for diagnostics.
- Security: standard; new files only under `semantic/`.
- Priority P0: the 13-step workflow starts with "open".

- **OSSIE-002a New and open.** Scope: list every semantic object kind in `semantic/` (Ossie, TMDL
  folder, Semantic View copy) with its kind and version; create a minimal valid Ossie file from a
  name and a lakehouse. Out of scope: editing content, Fabric calls. Design: one read RPC over the
  existing folder helpers; one create RPC with preview and confirm. Acceptance: works with no
  sign-in; a new file passes OSSIE-003a; links and junctions refused. Tests: scenarios 1, 2, 19.
  UX: needs a mockup (project list on the Semantic model tab). Owner: TMDL/Ossie thread.
- **OSSIE-002b Version compare.** Scope: compare two Ossie files, or a file and its Git `HEAD`,
  through `to_tmsl` plus `tmdl.differences`, reporting objects added, changed and removed and
  extension changes. Out of scope: merge conflict resolution. Acceptance: identical files report
  nothing; a changed measure expression is one row; extension data changes are listed.
  Tests: scenario 14. UX: no mockup for the engine; the screen joins 007b's mockup.
  Owner: TMDL/Ossie thread.

**OSSIE-003 Ossie structural validation.** Extends PB-110.
- Problem: `parse()` checks five things; nothing checks a document against the spec.
- Value: actionable diagnostics before any conversion or deploy.
- Evidence: no schema vendored (A.1); a pinnable 0.3.0.dev0 schema exists (B.2).
- Dependencies: decision Q1 (version policy); OSSIE-004a (shared report shape).
- Security: the schema is data, vendored with its licence and commit, never fetched at run time;
  `jsonschema` with no remote `$ref` resolution.
- Priority P0: validation is the first level of every check that follows.

- **OSSIE-003a Pinned schema and validation.** Scope: vendor `ossie-schema.json` at `aa500d3` into
  `sfd/_vendor/ossie_schema/` with a README like the converter's; validate with `jsonschema`
  (already pinned at 4.26.0 in `constraints.txt`; declare it as a direct dependency); diagnostics
  with a JSON path, the rule, the value and a fix. Out of scope: Fabric and Snowflake rules (003b).
  Acceptance: the upstream examples at the pinned commit validate; each required-field and
  `additionalProperties` breach gives one diagnostic with a path; no network access in the
  validator. Tests: scenario 17 (malformed and hostile documents), 19 (offline). UX: no mockup.
  Owner: TMDL/Ossie thread.
- **OSSIE-003b Version profiles.** Scope: the four profiles in D.3 as rule sets; replace the strict
  equality check (R1) with "known profile or a named refusal"; label derived and unverified rules.
  Out of scope: changing the vendored converter. Acceptance: a 0.3 file is read as 0.3, not
  refused; a 0.1.1 wrapper is recognized; an unknown version is refused by name; every rule cites
  its source page and date. Tests: one valid and one invalid fixture per profile. UX: no mockup.
  Owner: TMDL/Ossie thread.

**OSSIE-004 Conversion fidelity reporting.** Extends PB-110; upgrades the kept and lost report.
- Problem: rows are labelled "kept" or "lost" by substring (R4), so informational notes look like
  losses and a vendor wording change would silently reclassify rows.
- Value: a developer sees exactly what survives each conversion and why, before writing anything.
- Evidence: `_convert` (`model_ossie.py:113-125`); `round_trip` (`:368`); merge keeps on
  `pb110-keep`.
- Dependencies: `pb110-keep` merged (same file).
- Security: report text is data; object names are rendered as text only.
- Priority P0: every parity and deploy result uses this vocabulary.

- **OSSIE-004a Five-class report (engine).** Scope: classify each object into D.4's classes from
  structure (what reached the output, what sits in the `POWER_BI` extension, what is missing),
  using vendor messages only as supporting text; add the report fields in D.4; flag RLS roles and
  calculation groups explicitly. Out of scope: UI, Fabric and Snowflake profiles. Design: a
  `fidelity` function in `model_ossie.py` comparing input and output object sets; `_convert`'s rows
  become evidence attached to objects. The old `kind` field stays for the current UI until 004b.
  Acceptance: every input object appears exactly once in the report; no RLS role or calculation
  group is ever unlisted; an informational vendor note no longer shows as a loss; the report names
  the converter commit. Tests: scenarios 4, 5, 6, 13; a property test that report objects equal
  input objects. UX: no mockup. Owner: TMDL/Ossie thread.
- **OSSIE-004b Report on the card.** Scope: show the five classes in the Ossie card and the
  Round-trip check. Acceptance: browser test per class; keyboard and axe checks. UX: needs a
  mockup. Owner: TMDL/Ossie thread with UX.

**OSSIE-007a One TMDL emitter.** Extends PB-110 (audit R3).
- Problem: `semantic.py` writes TMDL by hand beside `tmdl.from_bim`. Value: one writer that is read
  back and checked. Scope: build the Direct Lake model's TMDL through `tmdl.from_bim`. Out of scope:
  changing what the model contains. Dependencies: `pb110-fuzz` merged. Acceptance: the generated
  model's TMSL is unchanged for every existing test; `semantic.py` has no TMDL text. Tests:
  `test_semantic_mutation.py`, `test_model_date_table.py`, `model-build-fx.test.cjs`. UX: none.
  Owner: TMDL/Ossie thread. Priority P1: removes drift before editing adds more writes.

**OSSIE-007 Native visual editing.** Extends PB-110; reuses `model_ossie.plan` change rows.
- Problem: a "Needs DAX" measure must be written in Fabric; nothing edits an Ossie file.
- Value: define, edit and save locally with diagnostics. Evidence: A.3 measure editor Missing.
- 007b code editing of Ossie YAML with OSSIE-003 diagnostics, reusing the app's existing code
  editor. 007c entity, relationship and measure forms that produce `plan` rows. Out of scope: DAX
  evaluation. Dependencies: 003a, 004a, 002a. Acceptance: metadata and extensions survive edit and
  save byte for byte where untouched; works offline. Tests: scenario 3. UX: needs a mockup.
  Owner: TMDL/Ossie thread with UX. Priority P1: large UI; needs the engine first.

### Phase 2: Fabric integration

**OSSIE-005 Documented Fabric Semantic Views workflows.** New; reuses PB-110's converter path.
- Problem: KratosForge cannot produce or check a Semantic View.
- Value: author a view locally, know it will publish, then publish by hand with confidence.
- Evidence: nothing in code (A.4); Fabric docs in B.4.
- Out of scope: writing `Context/`, triggering publish, any undocumented endpoint, Git-connected
  workspaces (Fabric refuses them).
- Dependencies: OSSIE-003b (`fabric-semantic-view` profile), OSSIE-004a.
- Security: no Fabric write in 005a and 005b; 005c reads only.
- Priority P0: Chris named Semantic Views; the file-only path is safe and documented.

- **OSSIE-005a Semantic View file emitter and checks.** Scope: Ossie definition to a Semantic View
  YAML copy in the project; check the documented rules (0.2.0.dev0, flat root, DAX on every
  metric, single-column relationships, `.yaml` only, 20 files, 16 MB) and report every construct
  Fabric omits with warnings (fields, keys, `ai_context`, extensions) as Unsupported for this
  target. Acceptance: a definition with a SQL-only metric is flagged before export; a 21st file is
  refused; the report lists every omitted construct. Tests: one fixture per documented rule.
  UX: no mockup (engine). Owner: TMDL/Ossie thread.
- **OSSIE-005b Manual publish steps.** Scope: a panel with the file list, the lakehouse folder
  path, the documented UI steps (Upload, Save, Publish), permissions needed, Preview limits and
  "last write wins". Acceptance: every step links its Learn page; no button writes to Fabric.
  Tests: browser test. UX: needs a mockup. Owner: TMDL/Ossie thread with UX.
- **OSSIE-005c Inspect the managed model.** Scope: read the generated model's definition
  read-only, if `getDefinition` works on it, and compare it with the local view. Docs don't say
  (research open question 7), so this starts with a live check. Acceptance: the model is never
  offered for edit or import over; if the read fails, the panel says "not readable" with the
  reason. Tests: fake-API tests; scenario 15. Gate: Chris's typed "yes, run it" in
  `kf_tenant_test`. UX: joins 005b's mockup. Owner: TMDL/Ossie thread.

### Phase 3: parity framework

**OSSIE-006 Semantic parity validation foundation.** Highest priority in the brief. New
`model_parity.py`; extends PB-110's round trip.
- Problem: there is no way to say whether two representations mean the same thing.
- Value: platform-aware claims, never equivalence without evidence.
- Evidence: `tmdl.differences` and `round_trip` only; no DAX execution (A.3, A.6).
- Out of scope: a DAX engine on this PC (decision Q3); L4 performance until a live baseline exists.
- Dependencies: OSSIE-004a; OSSIE-003b.
- Security: controlled datasets are generated, never customer data; no query text leaves the PC.
- Priority P0: Chris marked parity highest; L1 and L2 can start without a tenant.

- **OSSIE-006a L1 structural parity.** Scope: compare any two kinds (D.1) object by object using
  fidelity classes; emit D.5 results with "Structurally compatible but behavior unverified" as the
  ceiling. Acceptance: no L1 result is ever Verified equivalent; Unsupported objects are never
  counted as matches. Tests: scenarios 10, 13. UX: no mockup. Owner: TMDL/Ossie thread.
- **OSSIE-006b L2 expression parity.** Scope: for the vendor's translatable subset (six aggregates,
  `COUNT(*)`, division), check that the DAX and SQL forms agree after normalization; anything
  outside the subset is Unsupported. Acceptance: TOTALYTD and filtered aggregates are Unsupported,
  not different. Tests: scenario 7; extends `test_ossie_vendor_translators.py`. UX: none.
  Owner: TMDL/Ossie thread.
- **OSSIE-006c L3 behavioral harness.** Scope: controlled datasets (nulls, blanks, precision,
  dates, many-to-many, inactive relationships); run the SQL side on DuckDB through
  `sql_dialects`; record the DAX side as unverified until a live run. Configurable tolerances with
  stated defaults. Acceptance: same input gives same result; a DAX-side result never appears
  without an executed query. Tests: scenarios 7 to 12 (SQL side). UX: none. Owner: TMDL/Ossie
  thread.
- **OSSIE-006d Live DAX side.** Scope: run the same datasets in `kf_tenant_test` and fill the DAX
  side. The query path (DAX query view is documented for views; no executeQueries client exists in
  `fabric_api.py`) is confirmed from official docs first. Gate: Chris's typed "yes, run it".
  Tests: scenarios 8, 9, 11, 12. Owner: TMDL/Ossie thread with Security QC.
- **OSSIE-006e Parity results panel.** Scope: results by level with evidence. UX: needs a mockup.

**OSSIE-012 CI/CD semantic regression.** Extends `.github/workflows/ci.yml`; live lane follows
`tsql-contract-live.yml` (HD-054).
- Problem: no semantic CI lane, no fixture corpus, hand-copied vendor. Value: regressions caught
  before merge. Evidence: A.6. Dependencies: OSSIE-004a, 006a. Security: fixtures are synthetic;
  no tenant secrets in CI. Priority P1: protects everything above.
- **OSSIE-012a Fixture corpus.** Synthetic `.bim`, TMDL and Ossie fixtures per profile in
  `sidecar/tests/fixtures/`. Tests: scenarios 4, 5, 17. Owner: TMDL/Ossie thread.
- **OSSIE-012b Semantic CI lane and large models.** Runs fidelity, L1 and L2 on the corpus; times a
  large generated model. Tests: scenario 18 (baseline to be measured in OSSIE-012b). Owner:
  TMDL/Ossie thread with Release management.
- **OSSIE-012c Converter-upgrade regression.** Diff fidelity reports of the corpus between the
  vendored commit and a candidate commit; upgrade only behind D.3 profiles. Tests: scenario 20.
  Owner: TMDL/Ossie thread.

### Phase 4: enterprise

**OSSIE-008 Metric registry.** Extends the platform catalog (`central/core.py`), glossary and
enrichment. No second registry.
- Problem: no record of a metric's meaning, owner or platforms. Value: one governed definition,
  conflicts shown. Evidence: A.6. Scope (008a): metric records in a new `catalog.` table linked to
  glossary terms and product items, with the brief's fields; governed versus local; conflicts by
  name and normalized expression. 008b: screen. Out of scope: approval workflows beyond existing
  catalog requests. Dependencies: OSSIE-004a, 011a. Design: writes through `central/udf.py` like
  other catalog writes; listed in the authorization matrix. Acceptance: a metric defined in two
  models with different expressions shows one conflict; local metrics never show as governed.
  Tests: catalog tests on a local lakehouse folder. Security: Security QC read (shared write).
  UX: 008b needs a mockup. Owner: TMDL/Ossie thread specifies; catalog changes through its owner.
  Priority P1: needs lineage and fidelity first.

**OSSIE-009 Semantic optimization.** Extends OE-013 and OE-023 (`docs/optimize/engine-plan.md`)
and the BPA work (PB-112, PB-115).
- Problem: no semantic findings in Optimize. Value: action-first findings with evidence.
- Evidence: `rg 'semantic' sfd/optimize sfd/opportunities` finds nothing (A.6).
- 009a (extends OE-013): analyzers for duplicate measures (normalized DAX text) and duplicate
  tables across models, written through `Shared.record_analysis`. 009b (extends OE-023): BPA and
  Direct Lake results as opportunities. Out of scope: claimed savings; a semantic finding stays
  Unknown in `measurement.py` until measured (ESI rules). Dependencies: Optimization engine
  thread; BPA thread for 009b. Acceptance: each finding has what, why, evidence, fix, risk,
  confidence and a validation step; no CU or money figure without a measurement. Tests: extends
  `test_opportunities*`. UX: existing Optimize screens; copy only, no mockup unless a new panel.
  Owner: Optimization engine / ESI thread, specified here. Priority P1.

**OSSIE-011 Lineage and impact.** Extends `meta/lineage.py`, `meta/change_impact.py` and the
Lineage thread's impact work (#2359).
- Problem: impact covers only `SM_<LastLayer>` and ignores measures (R2). Value: see who breaks
  before a change. Evidence: `change_impact.py:218-245`. Scope: 011a a measure consumer kind from
  column references in measures (confirmed for structured references, inferred otherwise); any
  model name and layer; models in `semantic/` and from Ossie. 011b cross-platform copies and
  whether they are in sync, using OSSIE-002b. Out of scope: a separate lineage service.
  Dependencies: Lineage thread; OSSIE-004a. Acceptance: confirmed and inferred are labelled apart;
  a renamed column lists the measures that use it. Tests: extends `test_change_impact.py`,
  `lineage-impact.test.cjs`. UX: the impact view is in the Lineage thread's mockup. Owner: Lineage
  thread, specified here. Priority P1.

**OSSIE-010 Snowflake adapter.** Extends SF-07 (`engines/registry.py:36`) and PB-117. SF-08 and
SF-10 are not needed for semantic views.
- Problem: no path to Snowflake semantic views. Value: one definition reaches Snowflake.
- Evidence: A.5, B.5. Out of scope: Snowpark.
- 010a `snowflake-ossie` profile and local checks (no account): down-convert to the 0.1.1 wrapper,
  string `custom_extensions`; report every metric without a `SNOWFLAKE` or `ANSI_SQL` expression,
  because Snowflake skips them silently. Acceptance: a DAX-only metric is never exported without a
  named row; output is the 0.1.1 wrapper. Tests: fixtures per rule. Owner: Dbx/Snowflake parity
  thread with TMDL/Ossie thread.
- 010b live verify and read-back through SF-07: `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` with
  `verify_only=TRUE`, then `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW`. Gate: Chris's account
  (decision Q4); SF-07 built. Security: key pair, PAT or OAuth through `connprofiles.py`; no
  secrets stored by this program. Priority P1: Phase 4 because it needs an account.

### Phase 5: DX excellence and reach (IQ, Academy, deployment comparisons)

**OSSIE-013 Fabric IQ mapping.** Extends the Ontology type row (`artifacts.py:103`) and the key
registry (`meta/keys.py`).
- Problem: no link from metrics to business concepts. Scope: map datasets to canonical keys and
  glossary terms; flag inconsistent terms. Out of scope: treating Ossie as an ontology; any AI
  provider. Evidence: A.4 (doc-only). Dependencies: OSSIE-008. Acceptance: every mapping is
  confirmed by a person or labelled inferred. UX: needs a mockup. Owner: TMDL/Ossie thread.
  Priority P2: the Ossie ontology layer is Preview and Fabric IQ has no documented API in the
  research.

**OSSIE-014 Academy.** Extends DP-700 E45, LM-09 and LAB-07.
- Scope: a Learn module on Ossie, Semantic Views versus Power BI models, TMDL, conversion and DAX
  portability; optional exercises graded on this PC with fixtures from OSSIE-012a. Out of scope:
  claims about features not on main. Acceptance: every lesson cites the dated official source.
  UX: mockups as already agreed with the Academy thread. Owner: Academy thread.
  Priority P2: teaches what exists once Phase 1 to 3 ship.

**OSSIE-015 Cross-platform deployment comparisons.** Extends the existing import flow
(`meta_model_ossie_import`) and ESI-style records.
- Problem: no compatibility check across targets and no deployment record. Scope: 015a a matrix
  per definition: for each target profile, its fidelity and parity result; 015b a deployment
  record (target, version, report, who, when) for Power BI model imports, with a dry run through
  the existing preview. Out of scope: automated Semantic View publish. Dependencies: 004a, 005a,
  006a, 010a. Acceptance: no silent destructive or security change; a failed import leaves the
  model as it was. Tests: scenarios 15, 16. Security: Security QC read. UX: needs a mockup.
  Owner: TMDL/Ossie thread. Priority P2: needs the profiles and parity first.

### Story map to existing items

| Story | Extends or maps to |
| --- | --- |
| 001, 001b | PB-110, FEAT-META-19, FEAT-META-13 |
| 002, 003, 004, 007 | PB-110 (TMDL and OSI conversion) |
| 005 | PB-110 converter path; new Semantic View profile |
| 006 | PB-110 round trip; new `model_parity.py` |
| 008 | Platform catalog (`central/core.py`), glossary |
| 009 | OE-013, OE-023, PB-112, PB-115 (BPA), ESI measurement rules |
| 010 | SF-07, PB-117 |
| 011 | Lineage (#2359), `meta/change_impact.py` |
| 012 | `ci.yml`, HD-054 live-lane pattern |
| 013 | Ontology type row, key registry |
| 014 | Academy E45, LM-09, LAB-07 |
| 015 | Ossie import flow, ESI record style |

## F. Agent and thread allocation

| Thread | Owns in this program | Reviews or supports |
| --- | --- | --- |
| TMDL/Ossie thread (owner) | `model_ossie*.py`, `tmdl.py`, `_vendor/ossie_*`, `model_parity.py`, this plan; OSSIE-001 to 007, 012, 013, 015 | Specifies 008, 009, 010, 011 |
| BPA | `model_bpa*.py`, `model_practices.py` | OSSIE-009b |
| Lineage | `meta/lineage.py`, `meta/change_impact.py` | Builds OSSIE-011 |
| Optimization engine / ESI | `opportunities/`, `savings/` rules | Builds OSSIE-009; checks savings tiers |
| Dbx/Snowflake parity | `snowflake_sql.py`, `engines/registry.py`, `connprofiles.py`, SF-07 | Builds OSSIE-010 |
| Academy | `academy/`, `learn/content/` | Builds OSSIE-014 |
| UX (mockups) | Mockups and copy | 002a, 004b, 005b, 006e, 007, 008b, 013, 015 |
| Security QC | Security reads | 003a, 005c, 006d, 008, 010b, 015, every new parser or write |
| Quality reviewers | Verdict on every PR head | All |
| Release management | Merge batches, CI lanes | 012b |
| Workload mgmt | Sign-off if workloads change | Scheduled regression runs on local schedules, if added |
| DXE audit thread | Walks the 13-step developer workflow | Measures its baseline (OSSIE-001b) and re-measures after each phase |

No two threads edit the same file in parallel. `model_ossie.py` changes queue behind the PB-110
stack (#3039, then fuzz, cultures, keep). Files owned elsewhere change only through their owner.

## G. Progress

| Item | State | Evidence |
| --- | --- | --- |
| PB-110 slice 1 | Merged | #2592: TMDL writer, TMDL to and from Ossie, nothing dropped silently |
| PB-110 slice 2 | Merged | #2910: Ossie file to TMDL, preview, token and confirm |
| Vendor tests | Merged | #3025: 53 tests over the vendored converter |
| PB-110 slice 3 | In review | #3039: card UI, Round-trip check, Save TMDL link checks |
| Fuzz slice | Built and reviewed | `pb110-fuzz`: `test_tmdl_fuzz.py` and fixes |
| Cultures slice | Built and reviewed | `pb110-cultures`: translations and linguistic metadata round-trip |
| Keep slice | Built and reviewed | `pb110-keep`: merge keeps properties the file doesn't set |
| D1 defect | Fixed on `pb110-keep` | Commit `85707981`: a dataset metric whose SQL fails to tokenize (sqlglot `TokenError`) is reported by the converter, not raised out of import |
| Phase 0 | Done | Repo audit, platform research, this plan |

## H. Risks and blockers

| Risk or blocker | Effect | Mitigation |
| --- | --- | --- |
| Preview APIs (Fabric views, Snowflake Ossie functions) | Behavior and limits can change | Each rule cites its page and date; profiles are data; recheck dates in every phase |
| Version skew (0.3.0.dev0, 0.2.0.dev0, 0.1.1) | One file can't serve all targets; a vendor update breaks Fabric | D.3 profiles before any vendor update; OSSIE-012c |
| No pinnable 0.2.0.dev0 or 0.1.1 schema | Those profiles can't be validated against upstream | Derived and unverified rules, labelled as such |
| No Semantic View publish API | No automated deploy | File-only plus manual publish (decision Q2) |
| DAX not executable locally | L3 parity for DAX cannot run on this PC | "Behavior unverified" until OSSIE-006d live runs |
| Snowflake needs Chris's account | 010b and Snowflake parity wait | 010a works with no account (decision Q4) |
| Live runs | Any Fabric run needs Chris's typed "yes, run it", in `kf_tenant_test` only | Batch live checks (005c, 006d, WR-502) into one ask |
| Mockups for new UI | Screens wait | Engine stories first; mockups bundled into one review |
| Hand-copied vendor, unreleased upstream | Drift, no release to pin | Commit recorded in README and report; upgrade regression |
| Shared files across threads | Clashing edits | Queue behind the PB-110 stack; owners change their own files |
| Large models | Slow conversion or parity | Existing size limits; timing baseline in 012b |

## I. Next actions

1. **First increment: OSSIE-004a, the five-class fidelity report.** It is engine-only and needs no
   mockup and no new dependency. It does not wait on an upstream pin or on decision Q1, which
   OSSIE-003 does (the only pinnable schema is 0.3.0.dev0, a version the app does not read yet). It
   fixes a known risk (R4) in code the team already owns. Parity L1 (006a), the Semantic View
   profile (005a), the Snowflake profile (010a) and the deploy matrix (015a) all report in its
   classes, so it unblocks section D. It starts on top of `pb110-keep` once that merges, to avoid
   clashing in `model_ossie.py`.
2. Merge the PB-110 stack in order: #3039, fuzz, cultures, keep.
3. OSSIE-001b doc corrections, in parallel (docs only).
4. OSSIE-003a and 003b once Chris answers Q1.
5. OSSIE-007a (one TMDL emitter) after the fuzz slice merges.
6. Bundle the mockups for 002a, 004b and 005b into one UX review.

## Test scenarios

Status: **Built** (a test exists on main or a reviewed branch), **Planned**, **Needs live tenant**.

| # | Scenario | Story | Existing tests | Status |
| --- | --- | --- | --- | --- |
| 1 | New project | 002a | None | Planned |
| 2 | Open project | 002a | `test_export_folder_reads_only_model_folders_under_semantic` | Built (folders); Planned (project) |
| 3 | Edit and save | 007b | `test_save_stays_inside_the_project_and_names_ossie_files` (save only) | Planned |
| 4 | TMDL to Ossie | 004a | `test_tmdl_to_ossie_to_tmdl_keeps_every_object`, `test_a_model_kept_in_git_exports_offline` | Built |
| 5 | Ossie to TMDL | 004a | `test_an_ossie_file_becomes_tmdl_in_the_project_with_no_fabric_call`, `test_an_ossie_file_from_another_tool_becomes_tmdl_on_this_pc` | Built |
| 6 | Unsupported metadata | 004a | `test_export_reports_what_only_fabric_understands_and_writes_simple_measures_as_sql`, `test_round_trip_names_what_doesnt_come_back` | Built (kept/lost); Planned (five classes) |
| 7 | Simple aggregation equivalence | 006b, 006c | `test_semantic_parity_l3.py` (SQL side run) | Built (SQL side); Needs live tenant (DAX) |
| 8 | Filter context | 006c, 006d | None | Needs live tenant |
| 9 | Time intelligence | 006b, 006d | None | Planned (Unsupported at L2); Needs live tenant |
| 10 | Relationships | 006a, 006c | `test_merge_keeps_hierarchies_drops_empty_lists_and_relationships_to_removed_tables` | Planned (L1); Needs live tenant (L3) |
| 11 | Null and blank | 006c, 006d | `test_scenario_11_*` in `test_semantic_parity_l3.py` | Built (SQL side); Needs live tenant |
| 12 | Precision | 006c, 006d | `test_scenario_12_*` in `test_semantic_parity_l3.py` | Built (SQL side); Needs live tenant |
| 13 | Security metadata | 004a | `test_update_keeps_roles_hierarchies_lineage_tags_and_fabric_only_measures` | Built (kept); Planned (never silent) |
| 14 | Git compare | 002b | `test_ossie_compare.py` (`ossie_compare.compare_versions`) | Built (engine); Planned (Git `HEAD` and screen) |
| 15 | Fabric deploy (authorized test resources) | 015b, 005c | `test_import_previews_then_writes_only_when_confirmed` (fake API) | Needs live tenant |
| 16 | Deploy failure and recovery | 015b | `test_a_model_that_cant_be_read_is_not_imported_over` (fake API) | Built (fake); Needs live tenant |
| 17 | Malicious imports | 003a, 012a | `test_files_that_arent_ossie_are_refused_with_a_reason`, `test_links_and_junctions_on_the_way_are_refused_and_never_followed`, `test_a_hostile_model_reads_back_with_the_same_objects_and_nothing_extra`, `test_tmdl_fuzz.py` (branch), D1 test (branch), `test_ossie_corpus.py` (012a corpus) | Built |
| 18 | Large-model performance | 012b | `test_too_many_tables_or_too_much_text_is_refused` (limits only) | Planned |
| 19 | Offline | 002a, 003a | `test_a_model_kept_in_git_exports_offline` | Built |
| 20 | Converter-upgrade regression | 012c | `test_ossie_vendor_converters.py` (pins current behavior) | Planned |

## Success metrics

| Metric | Baseline (2026-11-18) | Target |
| --- | --- | --- |
| Semantic sidecar tests passing | 401 passed in 40 s, 17 files (audit) | Grows each story; never lower |
| Ossie versions read | 1 of 3 (0.2.0.dev0) | 3 of 3 profiles after 003b |
| Documents checked against a schema | 0 (no schema vendored) | Every import and export after 003a |
| Report rows misclassified as lost | Baseline to be measured in OSSIE-004a | 0 |
| Objects with a fidelity class | 0 (two labels only) | 100% after 004a |
| Test scenarios Built | 8 of 20 fully or partly Built (table above) | 16 of 20 Built; the rest Needs live tenant |
| Parity scenarios executed at L3 | 0 | All SQL-side by 006c; DAX side after 006d |
| Semantic features run on a live tenant | 0 (WR-502) | FEAT-META-19 round trip plus 005c and 006d |
| Measures with traced consumers | 0 | Every measure in traced models after 011a |
| Semantic opportunities in Optimize | 0 analyzers | OE-013 and OE-023 analyzers after 009 |
| Large-model conversion time | Baseline to be measured in OSSIE-012b | Within the budget 012b sets |
| Academy items on Ossie or TMDL | 0 | One module and exercises after 014 |
| 13-step developer workflow steps doable in the app | Baseline to be measured in OSSIE-001b | All 13 by the end of Phase 5 |

## Decisions for Chris

| # | Decision | Recommendation | Needed by |
| --- | --- | --- | --- |
| Q1 | Which Ossie version is the native authoring version | Keep 0.2.0.dev0 (the vendor's and Fabric's) as native; read and write 0.3.0.dev0 and 0.1.1 as profiles; move native only when Fabric moves | OSSIE-003b |
| Q2 | Semantic Views: file-only plus manual publish | Yes. No writes to `Context/` and no publish automation until Microsoft documents an API | OSSIE-005 |
| Q3 | Build a DAX evaluator on this PC | No. DAX behavior is checked only by live runs in `kf_tenant_test`; until then results say "behavior unverified" | OSSIE-006 |
| Q4 | When to use your Snowflake account | After SF-07 is built and 010a passes, for `verify_only` checks only | OSSIE-010b |

Everything else follows existing rules and needs no new decision. Live runs still need a typed
"yes, run it" each time, in `kf_tenant_test` only.
