# Build a star schema for a report

A report about sales needs a **fact table**, one row per sale with its numbers, and the
**dimensions** people slice it by: customer, product, date. Joined on keys, they make a star.
This is the shape a Direct Lake model reads best (LM-09), and the one Fabric's own guidance on
dimensional modeling describes.

The lab has a small retailer's data, generated on this PC, with some of the problems real data has.

## Before you build

A dimension needs one row per business key. If the source lists a customer twice, numbering its
rows gives that customer two surrogate keys, and each of their sales can match both. So look first:

```sql
SELECT customer_id, count(*) AS copies
FROM customers
GROUP BY customer_id
HAVING count(*) > 1;
```

## The build

1. **Dimensions.** One row per business key, with a surrogate key numbered in a fixed order
   (`row_number() OVER (ORDER BY customer_id)`), and the columns people filter by.
2. **The fact.** Only the rows the report counts (complete orders), with each dimension's
   surrogate key in place of its business key, and a whole-number `date_key`.
3. **Check.** Every key in each dimension is unique, and every fact row finds its dimensions. A
   sale that finds no customer disappears from any visual that uses customer.

## Try it

1. Find every `customer_id` that appears more than once in `customers`: `customer_id` and
   `copies`.
2. Make `dim_customer`, `dim_product` and `fact_sales` from complete orders, where every sale finds
   its customer and product.
