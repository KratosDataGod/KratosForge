# The Forge Trial

This is the capstone. One raw delivery, three steps, and an answer finance can trust. It uses
what the paths taught: profiling (LAB-01), one row per key and safe reruns (LAB-04), joins that
keep their row counts (LM-03) and grouping (LM-04).

## The delivery

`raw_orders` arrived from a source system that reloads orders when they change, so some orders
are there more than once, with a later `loaded_at`. A few rows are broken. `customers` is clean.

## The three steps

1. **Profile.** Before fixing anything, count what's wrong. A number per problem is what you
   put in the incident note and what you check again after the fix.
2. **Silver.** Keep the latest copy of each order, then drop rows that break the rules. The
   order matters: if the latest copy of an order is broken, the order is out. An older, good
   copy doesn't get to stand in for it.
3. **Gold.** Answer the business question from Silver, never from raw. Check that the join to
   customers keeps every order: the totals should add up to Silver's total.

## Passing the trial

Pass all three exercises. On the same PC, with the same data, your answer has to match exactly.
