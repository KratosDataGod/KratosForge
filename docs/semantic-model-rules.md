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
