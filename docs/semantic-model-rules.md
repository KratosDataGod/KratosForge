# Semantic model rules (Preview)

KratosForge checks the semantic model a layer gets, the one **Update model** writes, against
best-practice rules on this PC. The model is worked out from the metadata project: its tables and
relationships from the entities, column types from each entity's contract, and descriptions from the
catalog, as the [data dictionary](data-dictionary.md) shows them. **Nothing is read from Fabric**, no
Fabric job runs, and it uses no capacity.

The rules are the Best Practice Analyzer (BPA) rules that need only the model's definition. Rules that
need the model's VertiPaq statistics (sizes and cardinality) need the model in Fabric, so they aren't
checked here.

## Rules

| Id | Severity | Category | Rule | What to do |
| --- | --- | --- | --- | --- |
| SM001 | Error | Error Prevention | Relationship columns have the same type | Give the fact's key column the dimension key's type in its contract |
| SM002 | Warning | Performance | Relationship keys are integers | Take keys from LH_Keys (`[[keys]]`) or a `[[lookup]]`, which issue integer surrogate keys |
| SM003 | Warning | Performance | No floating point columns | Use `decimal(p,s)` in the contract for amounts |
| SM004 | Info | Formatting | Foreign keys are hidden | Name the key like the dimension's surrogate key (ending `_sk`), which Update model hides |
| SM005 | Info | Maintenance | Tables and visible columns have a description | Add them on the entity's About card or with the data dictionary |
| SM006 | Warning | Maintenance | Fact tables relate to a dimension | Add a `[[lookup]]`, or name a column like a dimension's surrogate key |
| SM007 | Warning | Naming Conventions | Names have no tabs, line breaks or edge spaces | Rename the column or table |

A type the project doesn't record (a derived column without a contract type, say) is never guessed, so
it can't cause an SM001 or SM002 finding. Surrogate keys are whole numbers. SM003 leaves the
framework's own columns (`_run_id`, `_loaded_at`) alone.

## Each rule explained

Why each rule exists and what to change in the metadata project. A finding's link points at its rule's
section here. Each title line ends with the rule's importance, as on the
[notebook](notebook-best-practices.md) and [SQL](sql-best-practices.md) pages: **Critical** (it can put
wrong data in place), **Recommended**, **Educational** or **Advanced**. These sections ship inside the app
as the rules' guides, the same as the notebook and SQL rules' ([Learn](learn.md)), so they read offline.

### SM001

**Relationship columns have the same type** · Error · Error Prevention · Critical

Why: a relationship between a `bigint` key and a `string` key either fails to build or matches
nothing, so totals by that dimension come out blank. Give both ends the same type in their contracts.

When it's fine: never. A relationship between two types can't be right.

Principle: The two ends of a relationship are one value. Give them one type.

Impact: Data. Totals by the dimension come out blank, or the model won't build.

How to check: After the contract change, Check now lists no SM001 for the model, and Update model builds the relationship.

### SM002

**Relationship keys are integers** · Warning · Performance · Recommended

Why: integer keys compress better and join faster than text keys, which matters most in Direct Lake
and Import models with large facts. Take keys from LH_Keys (`[[keys]]`) or a `[[lookup]]`.

When it's fine: a small dimension whose natural key is short text, in a model nobody filters by it at scale.

Principle: Join on small whole numbers. Keep business keys as attributes.

Impact: Performance. Text keys make the model larger and every filter through the relationship slower.

How to check: Check now lists no SM002, and the key column's type in the data dictionary is a whole number.

### SM003

**No floating point columns** · Warning · Performance · Recommended

Why: `double` and `float` store an approximation, so money can be off by a fraction of a cent and
totals can differ between visuals. `decimal(p,s)` is exact.

When it's fine: measurements that are approximate by nature, like sensor readings or scientific values.

Principle: Store money and counts as exact numbers.

Impact: Data. Totals can differ in the last digits between visuals and from the source system.

How to check: Check now lists no SM003, and the column's type in the contract is `decimal(p,s)`.

### SM004

**Foreign keys are hidden** · Info · Formatting · Educational

Why: people slice by the dimension's columns, not by the fact's key. A visible key adds a field that
gives wrong answers when used on its own. Name it like the dimension's surrogate key (`_sk`) and
Update model hides it.

When it's fine: rarely. The rule looks only at a fact's columns that join to a dimension. A column people look up on its own, like an order number, doesn't join, so it isn't flagged.

Principle: Show people the columns they filter by, and hide the ones that only join tables.

Impact: Usability. People who drag a key into a visual get numbers that look right and aren't.

How to check: Check now lists no SM004, and the key isn't in the model's field list after Update model.

### SM005

**Tables and visible columns have a description** · Info · Maintenance · Educational

Why: descriptions appear in the field list, Copilot and the data catalog. Without them people guess
what a column means.

When it's fine: a column whose name says everything, like a date key. A short description still helps.

Principle: Write down what each table and column means once, where everyone reads it.

Impact: Usability. People guess what a column means, and two reports can answer the same question differently.

How to check: Check now lists no SM005, and the data dictionary shows a description for each table and visible column.

### SM006

**Fact tables relate to a dimension** · Warning · Maintenance · Recommended

Why: a fact with no relationship can only be summed as a whole, so filters on other tables don't
reach it and visuals show the same total on every row.

When it's fine: a small table kept only for a measure that doesn't need filtering, like a single-row settings table.

Principle: Every fact is filtered through at least one dimension.

Impact: Data. Visuals repeat the same total on every row, which reads as a real number.

How to check: Check now lists no SM006, and the model's relationships include one from the fact after Update model.

### SM007

**Names have no tabs, line breaks or edge spaces** · Warning · Naming Conventions · Recommended

Why: hidden characters make two names look the same, break DAX that names the column, and fail
deployment checks.

When it's fine: never. A name that looks clean should be clean.

Principle: Names are typed in DAX and read in tools. Keep them plain.

Impact: Maintenance. DAX that names the column fails, and deployment checks refuse the model.

How to check: Check now lists no SM007 after the column or table is renamed.

## Where the rules run

A governance policy names them in **Checked by**, like `SM003, SM005`
([governance rules](governance.md#the-rules)). **Check now** then checks the model for the project's
last layer (usually Gold), `SM_<Layer>`. Each rule can be turned off or given another severity with the
policy's `enabled` and `severity` parameters. A description finding offers **Open the data dictionary**,
and the others offer **Open the entity**.

## Where the rules come from

[Microsoft's Best Practice Analyzer rules](https://github.com/microsoft/Analysis-Services/tree/master/BestPracticeRules)
(MIT), which Michael Kovalsky of [Elegant BI](https://www.elegantbi.com/) co-wrote. KratosForge
rewrites the ones that need only the model's definition for the metadata project; nothing is vendored.
