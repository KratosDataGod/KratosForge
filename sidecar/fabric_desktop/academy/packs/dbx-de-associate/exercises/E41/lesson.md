# Time-based or data-driven: choose triggers for five jobs

An insurer's data team has five Lakeflow jobs: one waits for files in a volume, one feeds a 06:00 report, one depends on another team's silver tables, one is a stream that must always run, and one is sometimes started by hand.

## What you'll do

Choose time-based or data-driven triggers from data availability and dependencies.

## Notes

- Scheduled triggers run on a clock. File arrival and table update triggers run when the data is there.
- A continuous trigger starts a new run whenever one completes or fails.
- By default only one run of a job is active at a time.

## What this run proves

- You can pick the right component, method or trigger for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can configure it in Databricks: nothing is set up or run here.
