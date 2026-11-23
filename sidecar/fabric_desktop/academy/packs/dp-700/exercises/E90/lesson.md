# Refresh history: failures in a row, a schedule turned off, and slow refreshes

Five semantic models refresh on a schedule every night. Some reports show old numbers, two owners say their schedule has gone, and the Stock owner says one framing took much longer than usual. Stock is a Direct Lake model and the other four are Import models. You have the refresh history for the last eight nights. Find which models are stale, which schedules the service has turned off, and which refreshes were slow for their own model.

## What you'll do

From eight nights of refresh history, report each semantic model's latest status, the time its data is from, its failures in a row, whether the service has turned its schedule off, and its slow refreshes.

## How it runs here

The refresh history shows every refresh attempt with its status, start time, duration and error. This lab gives you the same facts as two made-up tables, models and refresh_history. The starter query's comments say what each column of the answer means.

Two rules from the service matter here.

- Power BI turns a schedule off after four failed refreshes in a row. You can't change that number.
- It turns a schedule off at once when a refresh fails with an error that needs a configuration change, such as invalid or expired credentials.

A Direct Lake refresh is framing. It reads the Delta tables' metadata and points the model at their latest version, so it usually takes a few seconds. Queries then read the data as of the last successful framing. An Import refresh copies the data and can take much longer.

## What this run proves

- Your query picks each model's latest refresh by its start time, counts only the failures in a row, applies the four-failure and credentials rules, and judges slow refreshes against each model's own median, on simulated data with DuckDB on this PC.

## What it doesn't prove

- The refresh history page or a real model. The history here is made up and shaped like what the page shows.
- Refreshes started on demand or through the API. Every refresh here is scheduled, so this lab doesn't say how they count toward the four failures.
- Turning the schedule back on, or fixing the credentials or the data source.
- Direct Lake automatic updates (Keep your Direct Lake data up to date), which this history leaves out.
