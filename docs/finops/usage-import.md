# Measured usage from Capacity Metrics and Cost Management exports

Fabric Desktop by KratosBI estimates what each Fabric run used from its run time ([Capacity and cost evidence](cost-evidence.md)). For the CU your capacity actually recorded, import a file exported from Microsoft's **Fabric Capacity Metrics app**. The app reads the file on this PC, keeps its rows as **Measured** figures, and sets them beside its own estimates.

**Monitor > Cost > Measured usage** (Preview) > **Import usage file**.

## Why a file

No supported Fabric API gives a developer the CU their runs used. The Capacity Metrics app has it, but only capacity admins can open it, and its semantic model is only supported for the app's own reports, so Fabric Desktop by KratosBI never queries it. A capacity admin can export a table from the app and hand you the CSV. You don't need admin rights to import it, and nothing is sent anywhere.

[The usage source decision](plan.md#finops-003-usage-source-decision) records which sources are supported and why. For what a capacity was billed, import an [Azure Cost Management export](#billed-per-capacity-from-azure-cost-management) too.

## Getting the file

In the Capacity Metrics app, as a capacity admin:

1. Open the **Timepoint** page for the time you want, and on the interactive or background operations table choose **More options > Export data**, then **CSV**. Keep the **Total CU (s)** column.
2. Or, for totals per item, export the **Compute** page's matrix by item (its **CU (s)** column). These rows have no times, so the import asks for the period the page showed.

Microsoft notes that exports can be sampled, so a file may not hold every operation. Each imported figure says so.

## What the import checks

Before anything is saved, the dialog shows:

- **Recognized as**: the export and its table, from the column names Microsoft documents for it.
- **Columns used**, **Not used** and **Not stored**. The **User** column is never stored: people's names stay out of this PC's files.
- **Rows**: how many were read, will be imported, were already imported (from an earlier file), or repeat inside the file.
- **Skipped** rows and why: no CU value, not a number, negative, no start time, an end before the start.
- **Period**, **Workspaces** and the **Measured CU** total.

The file is refused, and the dialog says what to do, when:

- its columns don't match a supported export (an Excel workbook: export CSV instead);
- it only has **Timepoint CU (s)**, which is the share of an operation counted in one 30-second timepoint, not the operation's own use;
- it is the same file as an earlier import;
- its totals per item cover a period that overlaps an earlier totals import for the same workspaces, which would count the same use twice.

Nothing is guessed. When the numbers or dates can be read two ways (is `1,234` one thousand? is `03/04` March or April?), the dialog asks which. The export doesn't state its time zone, so **Times are in** says how to read them (UTC unless you change it).

## How rows are matched to runs

A row is matched to one of this app's Fabric runs only when it can't belong to anything else:

- same workspace name and same item name (the notebook; the runner notebook for metadata runs; the lakehouse for table maintenance),
- its time overlaps the run's, give or take five minutes,
- and no other run of this app fits.

A job can show as more than one operation; all its rows are added to the run. Everything else stays unmatched and is shown as **Other use in the imports**: use on the capacity by other people, schedules or items this app didn't start. Totals per item are never tied to single runs.

The card shows:

| Figure | What it is |
| --- | --- |
| Measured CU in your imports | Every imported row in the period, measured |
| This app's runs, measured | The matched rows, and how many of this app's Fabric runs matched |
| The same runs, estimated | This app's estimate for those same runs, and how far measured is from it |
| Other use in the imports | Imported rows that aren't this app's runs |

Runs that didn't match are counted by reason: **No row** (an import covers that workspace and time but has no matching row; the export may be sampled or filtered), **Not covered** (no import covers it) or **Can't match** (the run doesn't record its item).

The cost figures above the card are unchanged: they stay estimates, so you can compare the two.

## Billed per capacity, from Azure Cost Management

Azure bills a Fabric capacity per meter and day. Someone with **Cost Management Reader** on the capacity's subscription (usually finance or the platform team) can export it and hand you the CSV. You need no Azure access yourself, and the app never calls Azure.

Two documented layouts are read:

- **Cost details**, with **Actual cost** (Azure portal > Cost Management > Exports, or a downloaded cost details report). The columns used are Date, MeterName, Quantity, UnitOfMeasure, CostInBillingCurrency (Cost in pay-as-you-go files), BillingCurrency, ResourceId, ResourceName, ChargeType and PricingModel. Column names are compared without case, because Microsoft notes they differ between account types.
- **FOCUS** cost and usage: ChargePeriodStart, ChargePeriodEnd, SkuMeter, ConsumedQuantity, ConsumedUnit, BilledCost and BillingCurrency. A file that has only EffectiveCost is refused: that is the amortized cost, and a reservation purchase would be counted twice.

Only rows of a Fabric capacity are kept (a ResourceId of type `Microsoft.Fabric/capacities`); every other row is skipped with its reason, and so are refunds and credits (negative amounts). Each kept row is measured and shows:

- its **cost**, in its own billing currency, before credits and taxes. Amounts in different currencies are never added together;
- its **CU hours**, only for a compute meter (Microsoft names them "... Capacity Usage CU") billed in hours. Storage meters and a reservation's own meter carry cost only.

The card's **Billed per capacity** table shows each capacity, the days covered, the compute CU hours billed and the cost per currency, and which of your environments use that capacity (from the last capacity reading on this PC; see [Capacity of each environment](capacity.md)). These figures are for the whole capacity, so they are never matched to single runs or added to the Capacity Metrics figures.

## On this PC

Imports are kept in `usage_imports` in the app's data folder (`%LOCALAPPDATA%\FabricDesktop` on Windows), one file per import, with the file's name and SHA-256. **Remove** deletes an import's local copy; your exported file isn't touched. Importing and removing are recorded in the [audit trail](../user-guide.md#audit-trail); the file's contents aren't.

## Limits

- Matching uses names. Renaming a notebook or workspace between the run and the export stops it matching.
- Operation ids in the export aren't documented to equal Fabric job ids, so they're used only to spot the same operation imported twice.
- CSV only; up to 64 MB and 250,000 rows per file.
- Not yet validated against a live tenant's export: the column names come from Microsoft Learn's description of the app's tables and of Cost Management's cost details and FOCUS files.
- A Cost Management export's ResourceName is taken as the capacity's name, which is how its environments are found. A capacity renamed in Azure won't match its environment.
