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
on this PC. Nothing in Fabric changes.

Models kept in Git are listed too, under **In Git, on this PC**: each
`semantic/<model>.SemanticModel/` folder in the metadata project (the layout **Save TMDL to
Git** writes, and the one Power BI projects use). KratosForge reads its TMDL, or its
`model.bim`, with no Fabric call, so this works in Dev and with no one signed in. Anything the
TMDL reader leaves out, such as translations, is listed in the report under Not converted. **Save to Git** writes
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
  preview. Lineage tags are kept by name, so reports on the model keep working. On a table,
  column or measure the file has, what Ossie itself describes (name, description, the
  column's source or expression and data type, keys, the measure's expression) comes from the
  file; every other property the model has and the file doesn't set, such as Hidden, format
  string, display folder, an annotation or a property KratosForge doesn't know, stays. Where
  the file has a Power BI section for that object (Export to Ossie writes one), the section is
  the whole story, so a property taken out of it is taken out of the model. A column the file
  gives another type or data type keeps none of its old properties, and one it maps to
  another source column keeps nothing about the old source (its source lineage tag and
  provider type). A property naming another object (a sort-by or group-by column, a
  variation's relationship or default hierarchy, the column or table an aggregation is an
  alternate of) isn't kept when the update no longer has that object, for example because you
  unticked it; nor is a KPI, a format string expression or a detail rows expression whose DAX
  names a table, column or measure the update takes out. Each property not kept is named in
  the preview's report.
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

## Ossie file to TMDL (Preview)

**Ossie file to TMDL...** writes a model's TMDL into the project straight from an Ossie file,
on this PC with no Fabric call. Pick the file and the model folder name. The dialog shows
whether `semantic/<name>.SemanticModel/` is new or already there.

- A new folder: the counts from the file and the **Round-trip check**, then **Write TMDL**.
- A folder already there: the file is laid over the TMDL like an Import update. The table
  lists what is added and changed, and each table, column or measure the file doesn't have
  with a **Keep it** tick. **Replace TMDL in <name>...** asks once more, in red, before it
  rewrites the files. Translations under `cultures/` are read and written back like the rest, so
  they stay; a culture file named differently from its culture is written back under the
  culture's name, and the old file is listed as removed. A culture file holding translations
  KratosForge can't read (of a partition, a data source or a KPI, say) is kept byte for byte:
  it is never rewritten or removed, the file list shows it as Same, and the Round-trip check
  says "kept as it is, because it holds translations KratosForge can't read" with their names.
  Every other culture file that defines one of its cultures is kept as well, and `model.tmdl`
  still lists those cultures in their places. File names are compared as Windows and macOS
  compare them (ignoring case and Unicode normalisation), so a write never lands on a kept file
  under another spelling, and a file already there under another case is rewritten in place
  rather than removed.
- Any other TMDL file in the folder (a table, `model.tmdl`, `relationships.tmdl`, a role, a
  perspective...) that holds lines KratosForge can't read, such as an object kind newer than
  it knows, refuses the write: the preview says, in red, which file and which lines, for
  example "tables/Sales.tmdl holds lines KratosForge can't read (calendar 'Fiscal year'), so it
  would lose them if rewritten; nothing was written.", and **Write TMDL** stays off. Such a
  file isn't kept as it is either, because a table file left stale would disagree with the
  model written. Take those lines out by hand, then preview again. A confirm is checked the
  same way, so lines added after the preview are refused too. A property it doesn't know,
  written as `name: value`, is read like the rest, so it doesn't refuse the write, and it stays
  as an Import update keeps it (see Update above).
- The **Round-trip check** lists what is kept, what rides in the Power BI section, and what
  can't be carried, by name. A measure whose SQL has no DAX equivalent is written with no
  expression and shows blank until you add DAX.
- A file that can't be converted says which object and what is missing, and nothing is written.

Exporting a model kept as TMDL in the project shows the same Round-trip check, which says whether
reading the Ossie file back gives the same model or names what differs.

## Fidelity report

Export (from Fabric or from a folder), Import's preview and Ossie file to TMDL each return a
`fidelity` report beside the `report` rows (OSSIE-004a, engine only; the card shows it in
OSSIE-004b). It lists every object the conversion reads once: the model, tables or datasets,
columns or fields, measures or metrics, hierarchies, calculation groups, relationships, roles,
perspectives, cultures and shared expressions, including those that travel in a file's Power BI
section. Each object is in one class:

| Class | Meaning |
| --- | --- |
| Fully preserved | It reached the destination as it was. |
| Preserved through extensions | It is only in the Ossie file's `POWER_BI` extension: it comes back in Power BI, and other tools don't see it. |
| Partially converted | It arrived, but something changed: a measure written as BLANK() until it has DAX (`needs_dax`), a data type with no equivalent, a name changed, a label or another vendor's extension dropped. |
| Unsupported | It has no equivalent and isn't in the destination. It is named, never dropped silently. |
| Unverified | It arrived, but no one has checked it behaves the same: SQL translated to DAX, or DAX to SQL. |

The class comes from comparing what went in with what came out (what reached the destination,
what sits in the extension, what is missing), not from the converter's wording. The converter's
messages, and the TMDL reader's and the merge's rows, are attached to the object they name as
`evidence`, so a note such as "Power BI infers a measure's data type from its DAX" is no longer
counted as a loss. A row that names no object is a warning. The report also gives the source and
destination formats and versions, the converter (`apache/ossie-converters`, commit `82b9eff`), the
excluded objects, the metadata changes, the warnings, the round-trip check as `validation`, and one
remediation line for each object that isn't fully preserved. Security roles and calculation
groups are always listed and flagged, and compared by what they do, not only by name: a role's
model permission, each table's filter and column permissions and its members, and each
calculation item's name, expression, ordinal and format string expression. One that arrives with
any of these changed is Partially converted, with each change listed. A role that is lost,
changed, or kept only in the extension where other tools won't apply it, is a security warning;
security warnings come first. Object names longer than 1,024 characters are cut short in the
report, with their length.

Every export now runs the round-trip check (Ossie and back) for the validation result, so an
export takes about three to four times as long as the conversion alone (3.5 times on a model of
2,400 columns). Imports run it on the model they write. A model with more than 20,000 columns and
measures isn't checked: the validation says "model too large for round-trip check", and a check
that didn't run is never shown as passed. If the report can't be built from a document, the
conversion still goes ahead, and the report says so with `status` "unverified" and the reason.

The `kind` (kept or lost) of the existing rows is unchanged, so the current card still works.

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
- KratosForge doesn't ship Microsoft's Tabular Object Model libraries. A model in Fabric is read
  as TMSL; a model in Git is read by KratosForge's own TMDL reader (`sidecar/fabric_desktop/tmdl.py`),
  which covers tables, columns, measures, hierarchies, partitions, relationships, expressions,
  roles, perspectives, calculation groups, cultures, annotations and descriptions.
- Cultures are one file each under `definition/cultures/`, as Power BI Desktop writes them:
  `cultureInfo <name>`, its `linguisticMetadata` (JSON content is read as JSON, XML as text,
  with its `contentType`), and `translations`, which repeat the model's tree (`model Model`,
  then tables, columns, measures, hierarchies and their levels, perspectives, roles and
  expressions) with `caption`, `description` and `displayFolder`. These become TMSL's
  `model.cultures[]`, with `translatedCaption`, `translatedDescription` and
  `translatedDisplayFolder` under `translations.model`. The writer writes them the same way, with
  the linguistic metadata on the lines after `linguisticMetadata =` as Power BI Desktop does (so
  re-saving a Power BI culture file shows no diff), plus a `ref cultureInfo <name>` line in
  `model.tmdl` for each, which keeps their order. Anything
  else in a culture (a translation of a KPI, a property a translation doesn't have, a
  translation with no name) is named in the report, never dropped silently.
- The engine also writes TMDL on this PC (Preview, PB-110): a model, or an Ossie file, becomes
  TMDL files with no Fabric call. Every write is read back with the TMDL reader before it is
  used. Anything that wouldn't come back exactly as it was, such as a blank first or last line in
  a value, is listed in the report by name. A name with a line break, or an
  expression with a line of three backticks, would break the TMDL layout, so it is refused and
  the object is named. Line breaks also include the characters other editors break lines on
  (U+2028, U+2029, U+0085, vertical tab, form feed and the file, group and record separators),
  and values holding them are written fenced (in JSON linguistic metadata, as `\u` escapes).
- Writing an Ossie file as TMDL into the project never follows a link or junction. Each file is
  written whole and swapped in. Only `.tmdl` files under `definition/` that the model no longer
  has are removed, and the preview lists them first. Table, role, perspective and culture names never become file names as they are:
  characters Windows doesn't allow, device names such as `CON`, and names that clash are changed
  for the file name only. TMDL to Ossie and back keeps every object the reader reads. Roles,
  perspectives, translations, hierarchies and calculation groups travel in the Ossie file's Power BI section,
  where other tools don't see them.

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
| `meta_model_ossie_to_tmdl` | An Ossie file as TMDL in `semantic/<model>.SemanticModel/`, on this PC with no Fabric call (Preview). It shows the files, removals and report first, and writes only when confirmed with the preview's token, so a confirm never removes files the preview didn't list | Files |
| `meta_model_ossie_save` | Saves an Ossie file under the project's folder | The file |
| `meta_model_ossie_read` | Reads an Ossie file from the project's folder | Nothing |
| `meta_model_ossie_preview` | What an import changes, and what it can't carry | Nothing |
| `meta_model_ossie_import` | Writes the import; without `confirmed` it only asks | The semantic model in Fabric |
| `meta_model_tmdl_save` | The model's TMDL into `semantic/<model>.SemanticModel/` | Files |
| `meta_model_ossie_folders` | Models kept under `semantic/` in the project's folder | Nothing |
| `meta_model_ossie_export_folder` | A model kept in Git as Ossie YAML, read on this PC | Nothing |
