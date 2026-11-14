# Enterprise notebook templates

KratosForge ships enterprise templates for the three notebooks every medallion platform needs: a
**Silver table**, a **Gold dimension** (Type 1 or Type 2) and a **Gold fact**, for Fabric, Databricks and
Snowflake. Each one already follows the standards, so the quickest notebook to write is also the right one.

## Start from a template

```
fabric-desktop templates list
fabric-desktop templates new fabric silver nb_crm_to_silver_customers.ipynb
```

Open the copy and fill in **only the Parameters cell**: the tables, the business key, the columns and
their types. Every other code cell starts with `# kratosforge:standard`. Leave those alone.

## What the templates do for you

- **Silver** keeps only the columns in its contract, trims and deduplicates, sends rows without a business
  key to a quarantine table, and takes each row's surrogate key from the central key table. Fabric issues
  the next key in a `MERGE`, Databricks uses an identity column and Snowflake a sequence. The same business
  key always gets the same key, from every source.
- **Dimensions** always have an Unknown member, `-1`. Type 2 dimensions keep a version key that facts use
  and a durable key that never changes, with `valid_from` (inclusive) and `valid_to` (exclusive).
- **Facts** declare their grain, hold only dimension keys and numeric measures, look each key up as of the
  fact's date, and send a miss to `-1`. An order number or a status is a degenerate or descriptive field: it
  goes in a dimension, and the fact keeps that dimension's key. The rules cell refuses them.

## Who supports what

| You change | The notebook is | Supported by |
| --- | --- | --- |
| Only the Parameters cell | Conforming | The enterprise |
| A standard cell, or you add a code cell | Domain-owned | Your domain |

Check before you hand over:

```
fabric-desktop templates check nb_crm_to_silver_customers.ipynb
fabric-desktop templates handover nb_crm_to_silver_customers.ipynb --domain Sales --owner you@contoso.com
```

The check lists every change that makes a notebook domain-owned. Need logic the template doesn't have?
Derive it in Silver, or ask the enterprise to add it to the template so every domain gets it.

## Try it

The starter query carries degenerate fields and joins the customer's current version. Fix it the way the
fact template does.
