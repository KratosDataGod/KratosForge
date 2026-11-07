# KQL: summarize, extend and project event data

Operators are learning KQL to explore sensor events in an eventhouse. Their first queries mix up the operators that filter, reshape and aggregate.

## What you'll do

Pick the KQL operator for filtering, adding columns, choosing columns, counting per time bucket and sampling rows, and say why.

## Notes

- where filters rows.
- extend adds calculated columns and keeps the rest; project keeps only the columns it names.
- summarize aggregates, and bin(Timestamp, 1h) groups times into hours.
- take returns some rows, with no guarantee which unless the data is sorted.

## What this run proves

- You can pick the right feature, setting or operator for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
