# Semantic models and Apache Ossie

**Preview.** KratosForge converts Fabric semantic models to and from
[Apache Ossie](https://ossie.apache.org/), the open format for semantic models that
Snowflake, Salesforce, dbt Labs, Databricks and others read and write. Ossie was called
Open Semantic Interchange (OSI) until July 2026, and it is now an Apache Incubator project.
Its spec, version 0.2.0.dev0, is still a draft and may change.

It lives on **Data mesh > Deploy > Semantic model**, in the Open Semantic Interchange
card under the Direct Lake one. It shows only while Settings shows preview features.

## Export to Ossie

Pick a semantic model in the environment's workspace (Test or Prod). KratosForge reads its
definition from Fabric as TMSL (`model.bim`, a read-only getDefinition call) and converts it
on this PC. Nothing in Fabric changes. **Save to Git** writes
`semantic/<model>.ossie.yaml` into the metadata project's folder, where it shows in
Changes, ready to commit. **Copy** puts the YAML on the clipboard instead.

The report sorts every object into three groups:

| Group | What it means |
| --- | --- |
| Portable | Any Ossie tool reads it: tables, columns, keys, relationships, descriptions, and measures that are one aggregate over one column (SUM, AVERAGE, MIN, MAX, COUNTA, DISTINCTCOUNTNOBLANK), written as SQL with the DAX kept beside it |
| Kept for Fabric only | Stored in the file's Power BI section, so a model sent to Ossie and back is the same model, but other tools don't see it: other DAX measures, security roles, hierarchies, calculation groups, perspectives, translations, format strings, display folders, lineage tags |
| Not converted | Anything that can't be carried at all, named one by one. Nothing is dropped without being listed |

## Import from Ossie

Pick an Ossie file (`.yaml` or `.json`) from the project or from disk, and what to do with it:

- **Update** an existing model (the default). The file's tables, columns, measures and
  relationships are laid over the model. What the file doesn't mention stays, such as
  security roles, hierarchies and measures written in Fabric, unless you untick it in the
  preview. Lineage tags are kept by name, so reports on the model keep working.
- **Replace** an existing model with exactly what the file holds.
- **New model** in the environment's workspace.

The preview lists every table, column, measure and relationship that is added, changed or
removed, and what can't be carried over. Nothing is written until you confirm. Writing
updates the model's definition only; no refresh runs.

New tables read the layer's lakehouse through the model's existing Direct Lake
connection, or through Direct Lake on OneLake for a new model.

Measures written in SQL become DAX when the SQL is one aggregate over one column, or a
division of those. Anything else (CASE, window functions, filtered aggregates) is added as
`BLANK()` and marked **Needs DAX**: the measure shows blank until someone writes it in DAX.
Ossie has no place for a composite key in Power BI, a many-to-many relationship, or a time
zone on a date and time column, so those are listed as not converted.

## Keeping Git in step

After an import, **Save TMDL to Git** reads the model back from Fabric as TMDL and writes it
to `semantic/<model>.SemanticModel/` in the Power BI project layout, next to the Ossie file.
Files Fabric no longer returns are removed from that folder.

## How this works

- Reads: the model's definition from Fabric (getDefinition, as TMSL or TMDL). Item.ReadWrite.All,
  which sign-in already asks for.
- Writes: the model's definition (updateDefinition) or a new model, only after you confirm;
  files under `semantic/` in the metadata project's folder.
- Converts on this PC with the Apache Ossie Microsoft converter (Apache License 2.0),
  vendored in `sidecar/fabric_desktop/_vendor/ossie_microsoft`. No AI is involved.
- KratosForge doesn't ship Microsoft's Tabular Object Model libraries, so TMDL goes through
  Fabric's own TMSL format rather than being converted offline.

## From an AI tool

A connected AI tool ([Connect your AI tool](ai/README.md)) can list an environment's semantic
models (`fabric_desktop_list_semantic_models`), export one as Ossie YAML
(`fabric_desktop_export_semantic_model_ossie`) and preview what importing a document would change
(`fabric_desktop_preview_ossie_import`). All three only read, and a tool limited to some workspaces
reaches only environments in them. Importing writes the model, so it stays on this tab, where you
see the preview and confirm.

## Engine methods

| Method | What it does | Writes |
| --- | --- | --- |
| `meta_model_ossie_models` | Models in the environment's workspace | Nothing |
| `meta_model_ossie_export` | A model as Ossie YAML, with counts and the report | Nothing |
| `meta_model_ossie_save` | Saves an Ossie file under the project's folder | The file |
| `meta_model_ossie_read` | Reads an Ossie file from the project's folder | Nothing |
| `meta_model_ossie_preview` | What an import changes, and what it can't carry | Nothing |
| `meta_model_ossie_import` | Writes the import; without `confirmed` it only asks | The semantic model in Fabric |
| `meta_model_tmdl_save` | The model's TMDL into `semantic/<model>.SemanticModel/` | Files |
