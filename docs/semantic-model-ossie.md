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

## Structural parity (L1)

`semantic_parity.parity_l1(left, right)` compares two semantic definitions object by object
(OSSIE-006a, engine only: no engine method and no screen yet; the results panel is OSSIE-006e).
Each side can be a `model.bim` (TMSL), a TMDL folder's files, or an Ossie document in any profile
KratosForge reads. An Ossie 0.2.0.dev0 document goes through the converter, and each of its objects
carries its fidelity class; an Ossie 0.3.0.dev0 document or Snowflake's 0.1.1 wrapper is compared as
written, without conversion. A Fabric Semantic View is a fourth kind, named
`semantic_view` because it has the same version as Ossie 0.2.0.dev0. It's compared as written, and
anything the view has no place for is Unsupported (see Semantic View files).

It compares the model, tables, columns, measures, relationships, roles, calculation groups,
hierarchies and perspectives: whether each is there, its name, its data type (through the
converter's own type maps only, so Ossie `Time` is never the same as `dateTime`), each
relationship's endpoints, cardinality, active flag and cross-filter direction, each role's
permission, filters and members, and each calculation item. Measures are compared by name and
home table only. Each object gets one of the plan's results:

| Result | When |
| --- | --- |
| Structurally compatible but behavior unverified | The structure matches. This is the best L1 result. |
| Different results | Missing on one side (the side is named), or a change, each listed. |
| Unsupported | The conversion couldn't carry it, or the other side has no place for it (a role, calculation group or inactive relationship against Ossie 0.3). Never counted as a match. |
| Test failed | A side couldn't be read. The reason is in `error`. |

**L1 never claims equivalence.** It never reports Verified equivalent or Equivalent within
tolerance: matching structure says nothing about what the expressions compute (L2, OSSIE-006b),
what a query returns (L3) or how fast (L4), and the report lists these under `not_checked`. When
behavior is checked later, DuckDB runs the SQL side only: DuckDB is not a DAX engine, so a DAX
side with no execution path stays "behavior unverified". The report's `summary` is the worst
object result, `counts` counts each result, and `tolerances` echoes the configured tolerances
with their defaults (unused at L1). A Different results summary can hide Unsupported rows, so read
`counts`, which has both. Model names are only noted, never counted as a difference.

The report can name role members and quote row-level security filters. Anything that shows it
outside the local pane (a log, a support bundle, a CI artifact, GitHub text) must use
`semantic_parity.redacted(report)`, which gives counts of members and a length and hash of each
filter instead.

### Expression parity (L2)

`semantic_parity.parity_l2(left, right)` (OSSIE-006b, engine only) runs L1 and keeps all its rows.
For each measure or metric on both sides, it also compares the expressions. Each expression is
read into a small canonical form, but only inside the subset the converter itself translates
between DAX and SQL. An Ossie metric is compared through its SQL when it has SQL the converter
reads, otherwise through its DAX. DAX is read by a small tokenizer that knows only this subset.
SQL is read by the converter's SQL reader, after a size check. Nothing is run or evaluated.

| DAX | SQL | Result |
| --- | --- | --- |
| `SUM`, `AVERAGE`, `MIN`, `MAX` over `'Table'[Column]` | `SUM`, `AVG`, `MIN`, `MAX` over the column | Structurally compatible but behavior unverified |
| `COUNTA` (or `COUNT` on a column that isn't a boolean) | `COUNT(column)` | Compatible, with a note that a blank and an empty string can differ |
| `COUNT` | `COUNTA` | Compatible only on a column known not to be a boolean; otherwise Unsupported |
| `DISTINCTCOUNTNOBLANK` | `COUNT(DISTINCT column)` | Compatible: the converter maps these two; NULL handling is left to L3 |
| `DISTINCTCOUNT` | `COUNT(DISTINCT column)` | Different results: DISTINCTCOUNT counts BLANK as a value |
| `COUNTROWS('Table')` | `COUNT(*)` | Compatible |
| `DIVIDE(a, b)` | `a / NULLIF(b, 0)`, or a safe division | Compatible: the converter's own form |
| `DIVIDE(a, b)`, `DIVIDE(a, b, x)` or `a / b` | `a / b`, or any division for the last two | Different results: a zero divisor gives a different answer |
| `[Measure]` | the metric's name | Compatible, by name only (a bare `[X]` that isn't a measure, or a SQL name that is both a metric and a column, is Unsupported) |

L2 is Unsupported, never Different, when either side is outside the subset. That covers CALCULATE
and other filters, TOTALYTD and the rest of time intelligence, iterators such as SUMX, variables,
IF and SWITCH, window functions, filtered aggregates (`FILTER (WHERE ...)`), CASE, COALESCE, other
arithmetic, a column that doesn't resolve to exactly one field, a DIVIDE alternate that isn't a
plain decimal number, and anything past the caps
(4,096 characters, 64 tokens, 8 levels of parentheses) or holding invisible characters. Identifiers
are compared without case, as DAX does. Each measure row carries `expressions` (`left`, `right`,
`left_form`, `right_form`, cut short like every other name), and the report lists the subset
under `subset`.

**No L2 result means the numbers match.** L2 compares the shape of two expressions, never what they
return. The best result is still Structurally compatible but behavior unverified. Behavior (L3) and
performance (L4) are listed under `not_checked`. Expressions can hold literals, so `redacted()`
replaces their text and forms with a length and a hash.

### Behavioral parity, SQL side (L3)

`semantic_parity_l3.parity_l3(left, right)` (OSSIE-006c, engine only) runs L2 and keeps all its
rows. For each measure or metric that L2 could read, it runs the SQL side on DuckDB, in memory
with external access off, against seven small controlled datasets: plain values, NULLs, NULLs
only, no rows, repeated values, zeros (a zero divisor, empty text, DAX's zero date) and precision
(decimal fractions, integers past 2^53). The datasets are generated from the tables and columns the
measures name, typed as each side types them, so a type change between sides is part of the run.
They are never customer data, and nothing touches disk. The query is built from L2's canonical
form with quoted names, never from the expression's own text. A referenced measure is computed
once per dataset and read by each reference. Each measure is capped at 64 aggregates and each side at 2,000.
A measure past either cap is not run, and the row gives the reason.

| Sides | Result |
| --- | --- |
| Both SQL, same value on every dataset | Verified equivalent, on these datasets run on DuckDB only (the row says so) |
| Both SQL, some values differ within the tolerances | Equivalent within tolerance |
| Both SQL, a value differs past the tolerances | Different results, naming the dataset and both values |
| One side DAX | The L2 result stands. The SQL values are evidence, and the DAX side is "not run" |
| DuckDB refuses both sides' query on a dataset | The L2 result stands, with a note. The same error on both sides is never a match |
| DuckDB refuses one side's query only | Different results, naming the dataset |

**A DAX side never runs on this PC**, because there is no DAX engine here and DuckDB isn't one. A
report never holds a DAX-side value without an executed DAX query. OSSIE-006d runs that side in
`kf_tenant_test`. L3 never improves a row that L1 or L2 already found worse, but values that differ
still mark the row Different results. Both SQL sides run with DuckDB's semantics, not their own
engine's. For example, DuckDB gives infinity for 1/0 where T-SQL raises an error, and integer
division and NULL order can differ too. Relationships, filter context and time intelligence aren't exercised, because the subset aggregates whole tables, so
these are listed under `not_checked`.

Tolerances are configuration, and the report states them: `absolute` 0 and `relative` 1e-9 by
default, so only floating-point noise passes. A column type with no controlled values (binary,
time of day, time zones) is not run, with the reason. Neither is SUM or AVERAGE over a text,
Boolean, date or time column, because DuckDB sums a Boolean, where DAX and T-SQL refuse it. `semantic_parity_l3.redacted()` also replaces
each built query with a length and a hash.

## Schema validation

Import's preview also checks the Ossie file against the official Apache Ossie JSON Schema for the
version the file declares, and returns the result as `schema_validation` (OSSIE-003a, engine
only; no screen shows it yet). The check runs on this PC and never goes online. The preview
reads the file once and the check uses what it read.

The result only informs (`"effect": "informs"`). A file that fails it (`valid` false) can still be
imported: Import never waits on this check, because the converter already refuses what it can't
read, and says why. The check shows where the file departs from the specification, which matters
when the file goes to other tools.

- KratosForge carries two copies of the schema, each fixed to one upstream commit, in
  `sidecar/fabric_desktop/_vendor/ossie_schema` with their licence and SHA-256 hashes:
  0.2.0.dev0 (commit `21d14d7`, the last before Ossie moved to 0.3.0.dev0) and 0.3.0.dev0
  (commit `aa500d3`). The converter reads and writes 0.2.0.dev0, which Fabric Semantic Views take.
- A file of any other version gets one problem, "no pinned schema for" that version, and never
  passes. Snowflake's 0.1.1 wrapper is checked for its documented shape (see Version profiles).
- Each problem gives where it is as a path such as `$.datasets[0].fields[2].name`, the schema
  rule it breaks (`required`, `additionalProperties`, `type`, `enum` and so on), a short form of
  the value, what is wrong and how to fix it. A missing property and an unexpected property are
  one problem each. Problems are sorted by path; after 200 the rest are counted in one line.
- A file that is wrong almost everywhere costs bounded time: the check stops after 5,000 schema
  errors and the last line says "at least N more" (`stopped` is true). The problems shown are
  then the first ones in the file. A file of 2,000 datasets with 50 bad fields each is checked in
  about a second.
- The file is read with the same limits as Import: at most 20 million characters, no YAML
  aliases (`*name`), no key given twice in one object, and no deeper nesting than a model needs.
  A repeated key is refused with its name and line, because YAML would keep only the last one.
  The schema can't refer to anything outside the copies it ships with.
- `valid` is true only when a schema checked the file and found nothing. Rules beyond the schema,
  such as what Fabric or Snowflake accept, are the version profiles below.
- Regular expressions in a schema (`pattern`, `patternProperties`) would be matched with Python's
  `re`, not the ECMA-262 dialect JSON Schema names. Neither pinned schema has any, and a test fails
  if a newly vendored schema adds one, so the dialect is reviewed before such a schema ships.

## Version profiles

Fabric, Snowflake and Apache Ossie itself each take a different Ossie version, so KratosForge
reads a file by its profile (OSSIE-003b, engine only; `sidecar/fabric_desktop/ossie_profiles.py`).

| Profile | Version and layout | Checked by |
| --- | --- | --- |
| `ossie-0.3` | 0.3.0.dev0, flat | the pinned schema at `aa500d3` |
| `ossie-0.2` | 0.2.0.dev0, flat (what the converter reads and writes) | the pinned schema at `21d14d7` |
| `fabric-semantic-view` | 0.2.0.dev0, flat | the 0.2 schema plus Fabric's documented rules |
| `snowflake-ossie` | 0.1.1 in the `semantic_model` wrapper | documented shape only: always **Unverified** |

- A file's profile comes from its version and layout. Any other version is refused with a message
  that names it. Fabric's profile is never guessed, because it uses the same version as `ossie-0.2`;
  ask for it by name.
- A 0.3.0.dev0 file or a Snowflake 0.1.1 file is read and checked, but it isn't converted. The
  converter reads 0.2.0.dev0 only, so Import and Ossie file to TMDL refuse these files and name the
  profile. Import's preview still returns the schema check, with `convertible` false. Converting
  between versions comes later (OSSIE-012c).
- Fabric rules: every metric needs a `DAX` expression, and relationships have one column on each
  side. A view has at most 20 `.yaml` files, 16 MB of YAML combined and 30 MB of generated TMDL.
  The TMDL size is checked only when the TMDL is given. Files without the `.yaml` extension, `.yml`
  among them, are ignored, with a warning. Two readings are labelled derived, because the page
  doesn't say: "MB" is read as 1,000,000 bytes, the stricter reading, and `.yaml` is matched in any
  case. A warning says what Fabric leaves out: `custom_extensions`, dataset `fields`,
  `primary_key`, `unique_keys` and `ai_context`. Warnings never fail the check.
- A Fabric view can be split across files, and publishing merges them into one view
  ([YAML reference](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-yaml-reference),
  updated 2026-11-17). `validate_files` checks each file as a fragment: it needs `version` and
  `name`, and `datasets`, when the file declares it, is a nonempty list. It then checks the merged
  view by the Fabric rules and shows each problem in the file it came from. Repeated names,
  different top-level descriptions and any other top-level key given differently in two files
  (`fabric/conflict`: "Resolve duplicate names and conflicting definitions across files") are
  errors. Files may give different `name` values, because Fabric doesn't use the name: "Required,
  but the value isn't used. The view folder name is used as the view and semantic model name." A
  file that can't be read is left out of the merged check, and `merged.notes` names it. A view
  whose files declare no datasets is valid, with a warning that Fabric infers the datasets.
  `validate` checks one whole document.
- Snowflake rules: version `0.1.1` and the `semantic_model` wrapper. A field or metric with no
  `SNOWFLAKE` or `ANSI_SQL` expression gets a warning, because Snowflake skips it. That rule is
  labelled derived: Snowflake documents the behaviour but not where fields sit in 0.1.1. A correct
  0.1.1 file has no errors, a `notes` line that no schema is pinned, and the status Unverified. A
  file that breaks a Snowflake rule is Invalid.
- Each rule records its source page, the page date from the phase 0 research, its basis (pinned
  schema, documented or derived) and, in `derived`, any part of it that KratosForge reads in. The
  result gives the `profile`, a `status` (Valid, Invalid, Unverified or Not checked), `warnings`,
  `notes` and the `rules` that ran. It still only informs.

## Semantic View files (Preview)

Fabric Semantic Views (Preview) take Fabric's own profile of Ossie: version 0.2.0.dev0, a flat
root and a DAX expression on every metric. KratosForge can write the files of a view on this PC
and check them (OSSIE-005a; `sidecar/fabric_desktop/semantic_view.py`). The card below saves them and shows
the steps to publish them.

**KratosForge doesn't publish Semantic Views.** It never writes to a lakehouse's `Context/` folder,
where Fabric reads a view's YAML, and it never calls Fabric for a view. Publishing is manual, in
Fabric: a person uploads the files to the view's folder in the lakehouse, then saves and
publishes it. The steps, with links to each Learn page, are on the card in
[Publish the view in Fabric](#publish-the-view-in-fabric-preview).

What's written:

- The view is made from a model (a `model.bim`, or a model folder under `semantic/`) or from an
  Ossie 0.2.0.dev0 file. A model goes to Ossie first, as Export does.
- The files go to `semantic-views/<view>/` in the project's folder, and only `.yaml` files are
  written. Each file has `version: 0.2.0.dev0` and `name`. Fabric names the view and its model
  after the folder, so the view's name is a folder name. A name with `/`, `\` or `..` is refused,
  and so is a Windows device name such as `CON`, or a name that starts or ends with a space or a dot
  (Windows drops those, so "Sales." would share the folder of "Sales"). Other characters Windows
  doesn't allow become `_`.
  Nothing is ever written to a path that has a folder named `Context` or `SystemContext`.
- **Single file** writes the whole view as `<view>.yaml`. **One file per dataset** writes the
  description, relationships and any metric with no home dataset to `00_model.yaml`. Each other
  file holds one dataset and the metrics whose home it is. That follows the YAML reference's
  rules for a view split across files. A view never has more than 20 files. Past 19 datasets,
  several datasets share a file, and the report says so. **Schema and metrics** writes
  `schema.yaml` (description, datasets, relationships) and `metrics.yaml` (the metrics), the two
  parts of
  [Separate schema from metrics](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#separate-schema-from-metrics),
  flat in the view folder. Learn documents uploading files to the view folder but not making a
  subfolder under it, so the subfolder form that page offers for shortcuts isn't written. `files_summary` in the report gives each file's path,
  size in bytes and how many datasets, relationships and metrics it holds, and how many of the
  metrics have DAX.
- Characters YAML reads as a line break (U+0085, U+2028, U+2029) are written as escapes, never
  raw, and each file is read back before it's returned; one that wouldn't read back exactly is
  refused.
- Each metric's DAX is written first. A metric with SQL only is translated to DAX by the same
  converter Import uses, and the DAX goes beside the SQL. That metric is **Unverified**, because
  the two expressions aren't compared. Its other dialects stay in the file: Fabric recognizes them
  but publishes only DAX.
- The files are checked with `validate_files` and the `fabric-semantic-view` profile (see
  Version profiles). The result is in the report, and it informs only.
- Writing previews first. The preview lists the files, any `.yaml` file in the folder that would
  be removed (Fabric merges every `.yaml` file in a view's folder, so an old file would join the
  view) and a token. A write needs `confirmed` and that token, so it writes exactly what the
  preview showed. Each file is written whole and swapped in, and links and junctions are never
  followed.

What's left out, and why. Each item is a report row in the class **Unsupported**, with the rule
and the Learn page it comes from
([YAML reference](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-yaml-reference),
updated 2026-11-17):

| Left out | Why |
| --- | --- |
| `custom_extensions`, everywhere | "omitted with warnings", including Microsoft's own |
| Dataset `fields`, `primary_key`, `unique_keys` | "omitted with warnings". Fabric reads the columns from the lakehouse table |
| Every `ai_context` | "omitted with warnings" |
| A metric's `datatype` | Fabric ignores it |
| Any other key | not in the YAML reference's supported list |
| Security roles, calculation groups, perspectives, cultures, shared expressions, hierarchies, and the tables, columns, measures and relationships the converter set aside | They live only in the Power BI section (`custom_extensions`), which Fabric omits. Each is named. A role is a prominent **security warning**: the published view's model has no row-level security for it |
| A metric whose SQL can't be translated to DAX, or that has no expression | "Published metrics require DAX". It's flagged before any file is written, left out and listed in `excluded` with the converter's reason. Add a DAX expression to include it |
| A relationship with more than one column on a side | "Relationships are single-column only". It's left out and listed in `excluded` |
| A dataset whose source is a query, with its metrics and relationships | "Query sources are not supported". They're left out and listed in `excluded` |

Structural parity can compare a model with its view: pass the view as `semantic_view`
(`parity_l1(model, view, right_kind="semantic_view")`), as its files or as what the emitter
returned. Everything the view leaves out is **Unsupported** in that report, never a match. What
the view leaves out is worked out again from the other side, so a report passed in is never
trusted: a metric taken out of the files by hand stays **Different results**.

## Publish the view in Fabric (Preview)

On **Data mesh > Deploy > Semantic model**, under the Apache Ossie card, the card **Publish the
`<view>` semantic view in Fabric** shows the steps to publish a view's saved files by hand
(OSSIE-005b). Nothing on it writes to Fabric or calls Fabric. Its only buttons copy text, open the
view's folder on this PC, or save the view's files in the project's folder.

- **No files yet.** The card shows only **Save the view's files first**. **Save view files** makes
  the files on this PC from a model under `semantic/` (Schema and metrics, so `schema.yaml` and
  `metrics.yaml`). It lists the files it writes, and any it deletes, in a confirm first, and
  writes nothing until you confirm.
- **1. The files.** The files in `semantic-views\<view>\` are checked as Fabric reads them
  (`validate_files`, `fabric-semantic-view`). **Ready** means every documented check passed:
  version 0.2.0.dev0, DAX on every metric, single-column relationships, at most 20 files and
  16 MB. A table shows each file, what it holds and its size. **Open folder** opens that folder in
  File Explorer. When the view has a model of the same name under `semantic/`, the card converts
  it again on this PC. If the files differ from what it gives now, it says so and offers **Save
  view files** again. If they match, a **left out** line counts what the view leaves out, with the
  report.
- **A check fails.** The card lists each failure with its fix, and steps 2 to 5 wait with **Fix
  the files first**.
- **2. Where they go.** `<lakehouse> > Context/Semantic Views/<view>/`, with **Copy path**. The
  lakehouse and workspace are named only when the view comes from this layer's model, which
  reads that lakehouse. Otherwise the card says "the lakehouse the view's datasets read".
- **3. The steps in Fabric.** Create the view's folder, upload the files, save if you edit one in
  Fabric, publish, then check the result. Each step links its Learn section:
  [Create a view](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#create-a-view-with-the-starter-file),
  [Upload files](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#upload-existing-yaml-files),
  [Edit and save](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#edit-and-save),
  [Publish a view](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-publish#publish-a-view)
  and [Check the result](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-publish#check-the-result).
  The card also lists the permissions each step needs and what to know before you publish, from
  the same pages and the
  [overview](https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-overview).
- **Published model: not read.** KratosForge doesn't read the model Fabric makes yet, so it
  can't compare it with the files.

The card reads with `meta_model_semantic_view_status`. It reads only `semantic-views/<view>/` in
the project's folder, refuses a link or junction there, and reads nothing past 200 entries or
16 MB of YAML. **Open folder** uses `meta_model_semantic_view_reveal`, which opens only that view's
existing folder in File Explorer and is written to the audit log, as the other Open folder buttons are.

## Keeping Git in step

After an import, **Save TMDL to Git** reads the model back from Fabric as TMDL and writes it
to `semantic/<model>.SemanticModel/` in the Power BI project layout, next to the Ossie file.
Files Fabric no longer returns are removed from that folder.

### Compare two versions

`ossie_compare.compare_versions(old, new)` says what changed between two versions of the same
model (OSSIE-002b, engine only: no engine method and no screen yet). Each version can be an Ossie
0.2.0.dev0 file, a `model.bim` (TMSL) or a TMDL folder's files, read as structural parity reads
them, so a model and its own Ossie export are the same version. Ossie 0.3.0.dev0 and Snowflake's
0.1.1 files are refused with the reason, because the converter reads 0.2.0.dev0 only.

For tables, columns, measures, relationships, roles, calculation groups, hierarchies and
perspectives the report lists each object added, removed or changed, and counts the unchanged
ones by kind. A change says what changed: the data type, the expression, the format string, the
description, a relationship's ends or flags, a role's permissions or a calculation item. A
measure's expression is compared through the L2 canonical form when both versions are inside its
subset, so a rewrite that computes the same thing ("expression text") is told apart from one that
doesn't ("expression"). A SQL translation added beside unchanged DAX isn't a change. Roles and
calculation groups that changed are also listed under `look_at`, so they are checked first.

The model's own name and the order of columns aren't compared, and the report says so under
`not_checked`, with the other properties left out: partitions and data sources, shared
expressions, sourceColumn and summarizeBy. Bad input never raises: `status` is "failed" or
"refused", `error` says why, and the summary says the versions weren't compared. A line the reader
can't understand is left out of the comparison, so the summary and `notes` count it and each
side's `warnings` name it.
The report can quote a role's filter and name its members. Anything shown outside the local pane
must use `ossie_compare.redacted(report)`, which gives a length and hash of each filter, counts of
members and permissions, and no expression or format text.

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
| `meta_model_semantic_view_save` | A Fabric Semantic View's YAML files in `semantic-views/<view>/`, made on this PC from an Ossie file or a model (Preview; **Save view files** on the publish-steps card). It shows the files, removals and report first, and writes only when confirmed with the preview's token. It never publishes and never writes a lakehouse's `Context/` folder | Files |
| `meta_model_semantic_view_status` | The saved view's files: what each holds and Fabric's documented checks, and, given its model, whether the files still match and what the view leaves out (Preview) | Nothing |
| `meta_model_semantic_view_reveal` | Opens one saved view's folder, `semantic-views/<view>/`, in File Explorer, only if it exists (Preview; **Open folder** on the publish-steps card). Audited | Nothing |
