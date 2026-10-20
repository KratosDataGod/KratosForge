# Drag tables and files into a notebook

The notebook's **Explorer > Data items** lists the attached lakehouse's tables and files. As in
Fabric, you can put them into your code three ways. Each only writes code into the notebook:
nothing is read from OneLake until you run the cell, and then the read uses capacity as any
OneLake read does.

## Click

Clicking a table or file adds a cell below the current one that loads it with the `lakehouse`
module (Polars), as before.

## Drag into a cell

Drag a table or file onto a cell's code:

| Where you drop it | What goes in |
| --- | --- |
| An empty line of a Python cell | The code that loads it: `df = lakehouse.read_table("dbo.sales")` and `df` |
| Inside Python code | The quoted name, `"dbo.sales"` or `"Files/raw/orders.csv"` |
| Inside a Python string | The name without quotes |
| A `%%sql` cell | The table name, `dbo.sales` |
| A Markdown cell | The name as code, `` `dbo.sales` `` |
| Between cells or below the last one | A new cell that loads it |

**Ctrl+Z** in the cell takes a drop back. A frozen cell doesn't take drops.

## Right-click for more

Right-click a table or file for the ways to load it. Each adds a new cell:

| Table | File (`.csv`, `.parquet`, `.json`) |
| --- | --- |
| Load with Polars (`lakehouse.read_table`) | Load with Polars (`lakehouse.read_csv` / `read_parquet` / `read_json`) |
| Load with Spark (`spark.read.table`) | Load with Spark (`spark.read.csv` with a header / `parquet` / `json`) |
| Load with pandas (`.to_pandas()`) | Load with pandas |
| Query with SQL (`%%sql SELECT * ... LIMIT 1000`) | Read the file's text (not Parquet) |
| Copy name | Copy path |

Spark here is the notebook's local `spark` (see [Fabric notebook code on this PC](../notebooks.md#fabric-notebook-code-on-this-pc)).
