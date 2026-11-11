# T-SQL errors: read the messages, fix conversion, divide-by-zero and MERGE faults

A warehouse load fails in three places: a text quantity, a zero divisor and a MERGE. The on-call engineer has the messages and needs to pick the right fix for each.

## What you'll do

Read each T-SQL error message, say what caused it, and choose the change that fixes it.

## Notes

- "Conversion failed when converting ..." (error 245): CAST or CONVERT met a value it can't convert. TRY_CAST returns NULL instead, unless the conversion isn't permitted at all.
- "Divide by zero error encountered." (error 8134): wrap the divisor in NULLIF(divisor, 0).
- MERGE can't update the same target row more than once: make the source unique on the key.
- MERGE is generally available in a Fabric warehouse and must end with a semicolon.

## What this run proves

- You can pick the right feature, setting or operator for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
