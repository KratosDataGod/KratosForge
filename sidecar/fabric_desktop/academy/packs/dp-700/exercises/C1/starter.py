# C1 Retail lakehouse. Build these tables; the lesson says what each must hold.
#   ops.load_log           load_date, rows_landed                       one row per day landed, ever
#   silver.orders          order_id, customer_id, product_id, qty, unit_price, order_ts, load_date
#   silver.orders_rejected order_id, load_date, reason
#   silver.dim_customer    customer_key, customer_id, name, city, segment, valid_from, valid_to, is_current
#   gold.fact_sales        order_id, customer_key, product_id, order_date, qty, amount
#   gold.sales_by_segment  order_date, segment, category, orders, amount
# The notebook land_day lands one day from landing into bronze and exits with the order rows it landed.

landed = notebookutils.notebook.run("land_day", 90, {"load_date": "2026-10-01"})
print("landed", landed)
