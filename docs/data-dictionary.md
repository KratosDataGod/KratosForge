# Data dictionary

**Preview.** The data dictionary lists every table, column and measure of a layer's semantic
model with its description and classification. Export it, edit the descriptions in Excel, and
import it back. **Update model** then writes the descriptions into the model, where they show
in Power BI's field list as tooltips.

It lives on **Data mesh > Deploy > Semantic model**, in a card under the Direct Lake one, and
shows only while Settings shows preview features.

## Where the text comes from

The dictionary reads the catalog: what someone saved on an entity's **About** card, else the
starter text in the entity file's `[catalog]` section, else the entity's own `description`.
Measures are the ones Update model writes (a row count for each fact, and a total for each
numeric column), and they're described for you, for example "Sum of net_amount" followed by
the column's own description.

If the catalog can't be reached, the card says so and shows the text in Git only. Import
needs the catalog, so it waits until it can be reached.

## Export, edit, import

- **Export CSV** writes a new file in your Downloads folder and never replaces one. It opens
  in Excel; the `Entity` column is how Import finds each row, so leave it as it is. Text that
  starts with `=`, `+`, `-` or `@` goes out with a `'` in front, so Excel shows it as text
  instead of running it as a formula; Import takes the `'` off again.
- **Save to the project** writes `semantic/SM_<Layer>.dictionary.csv` into the metadata
  project's folder, where it shows in Changes, ready to commit.
- **Import descriptions** reads an edited CSV, from Excel in any language (semicolons work
  too), and shows every change before anything is saved:

| Shown as | What it means |
| --- | --- |
| To change | A description or classification that's different in the file |
| Unchanged | The row matches the catalog |
| Skipped | A column that isn't in the table any more, a row listed twice, or a classification that isn't Public, Internal, Confidential or Restricted |

A blank cell leaves the text as it is, so an import never erases a description. Measure rows
are skipped, since their descriptions are written for you. **Save** saves the changes to the
catalog through the same call as the About card; a catalog in Fabric uses capacity to save.
The model in Fabric doesn't change until you **Update model**.

## Where the idea comes from

Elegant BI's [data dictionary and description scripts](https://github.com/m-kovalsky/Tabular)
for Tabular Editor (MIT), by Michael Kovalsky. KratosForge keeps descriptions in the catalog
instead of only in the model, so the same text shows in the catalog and in Power BI.
