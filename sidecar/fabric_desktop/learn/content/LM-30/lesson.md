# Data contracts

A data contract is a table's promise to the people who use it: its columns and their types, its
key, how fresh it is, who owns it, and how changes are announced. Written down, it can be checked
by a machine on every delivery, so a broken file stops at the door instead of in a report.

## What goes in one

- **Schema**: each column, its type, and whether it may be empty.
- **Keys**: what makes a row unique, and what it points to.
- **Freshness and volume**: when it arrives, and roughly how many rows.
- **Owner and change process**: who to ask, and how much notice a breaking change gets.

## Check deliveries against it

Compare what arrived with the contract before loading. A missing column or a changed type is a
breaking change: stop the load and tell the producer. A new optional column usually isn't
(LM-22). In Fabric, OneLake catalog descriptions, endorsement and domain owners carry the human
part; the checks run in the pipeline that loads the data.

## Try it

Check `delivered_orders` against its contract.
