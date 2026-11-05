# Ship a domain data product

The Sales domain publishes `customer_summary` for other domains: one row per customer with their
order count, revenue and last order date. Its contract (LM-30) promises the columns and types,
a unique `customer_id`, and that every customer appears, with zeros rather than a missing row
when they haven't ordered.

## What makes it a product

- **Built to the contract**: `CREATE TABLE` with the promised types, then load it (SQ004, SQ010).
- **Complete**: start from the customers, not from the orders, so nobody drops out.
- **Clean keys**: orders for customers Sales doesn't know aren't the product's business; they
  belong in a data quality report (LM-20), not in a published row.
- **Owned and described**: in Fabric, endorse it, describe it in the OneLake catalog and put it in
  the Sales domain.

## The lab

Build `customer_summary` from `customers` and `orders`.
