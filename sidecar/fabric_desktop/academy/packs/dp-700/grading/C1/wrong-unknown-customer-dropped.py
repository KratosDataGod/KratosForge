from delta.tables import DeltaTable
from pyspark.sql import functions as F

# 1. Land only the days that haven't landed yet, oldest first, and log what each landing reported.
logged = {r["load_date"] for r in spark.read.table("ops.load_log").collect()}
waiting = {r["load_date"] for r in spark.read.table("landing.orders").select("load_date").distinct().collect()}
waiting |= {r["load_date"] for r in spark.read.table("landing.customers").select("load_date").distinct().collect()}
new_days = sorted(waiting - logged)
for day in new_days:
    rows = notebookutils.notebook.run("land_day", 90, {"load_date": day})
    # An exit value always comes back as a string.
    spark.createDataFrame([(day, int(rows))], ["load_date", "rows_landed"]).write.mode("append").saveAsTable("ops.load_log")

# 2. MERGE the newly landed orders into Silver: the newest load of each order wins, and an order whose
#    newest row fails a check leaves Silver.
if new_days:
    spark.read.table("bronze.orders").where(F.col("load_date").isin(new_days)).createOrReplaceTempView("arrived")
    newest = spark.sql("""
        SELECT order_id, customer_id, product_id, qty, unit_price, order_ts, load_date FROM (
            SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY load_date DESC) AS rn FROM arrived) AS a
        WHERE rn = 1""")
    valid = "s.customer_id IS NOT NULL AND s.qty > 0"
    (DeltaTable.forName(spark, "silver.orders").alias("t")
        .merge(newest.alias("s"), "t.order_id = s.order_id")
        .whenMatchedDelete(condition=f"NOT ({valid})")
        .whenMatchedUpdateAll(condition="s.load_date >= t.load_date")
        .whenNotMatchedInsertAll(condition=valid)
        .execute())

# 3. Rejected orders: each order whose newest row fails a check, with the first check it fails.
spark.read.table("bronze.orders").createOrReplaceTempView("bronze_orders")
spark.sql("""
    SELECT order_id, load_date,
           CASE WHEN customer_id IS NULL THEN 'missing customer_id' ELSE 'quantity not positive' END AS reason
    FROM (SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY load_date DESC) AS rn FROM bronze_orders) AS b
    WHERE rn = 1 AND (customer_id IS NULL OR qty IS NULL OR qty <= 0)
""").write.mode("overwrite").saveAsTable("silver.orders_rejected")

# 4. The customer dimension, type 2 on city and segment, rebuilt from the whole change history so a change
#    that arrives late still lands in its place. The name is type 1: every version shows the latest name.
spark.read.table("bronze.customers").createOrReplaceTempView("changes")
spark.sql("""
    WITH ordered AS (
        SELECT customer_id, name, city, segment, changed_at,
               lag(city) OVER (PARTITION BY customer_id ORDER BY changed_at) AS prev_city,
               lag(segment) OVER (PARTITION BY customer_id ORDER BY changed_at) AS prev_segment,
               row_number() OVER (PARTITION BY customer_id ORDER BY changed_at DESC, load_date DESC) AS newest
        FROM changes
    ),
    versions AS (
        SELECT customer_id, city, segment, changed_at AS valid_from FROM ordered
        WHERE prev_city IS NULL OR city <> prev_city OR segment <> prev_segment
    ),
    numbered AS (
        SELECT *, row_number() OVER (PARTITION BY customer_id ORDER BY valid_from) AS version,
               lead(valid_from) OVER (PARTITION BY customer_id ORDER BY valid_from) AS valid_to
        FROM versions
    )
    SELECT CAST(n.customer_id * 100 + n.version AS BIGINT) AS customer_key, n.customer_id, l.name, n.city, n.segment,
           n.valid_from, n.valid_to, n.valid_to IS NULL AS is_current
    FROM numbered AS n
    JOIN (SELECT customer_id, name FROM ordered WHERE newest = 1) AS l ON l.customer_id = n.customer_id
""").write.mode("overwrite").saveAsTable("silver.dim_customer")

# 5. Gold: each order joined to the customer version that was valid when it was placed (-1 when none is),
#    then a denormalised daily aggregate by segment and category.
spark.read.table("silver.orders").createOrReplaceTempView("orders")
spark.read.table("silver.dim_customer").createOrReplaceTempView("dim_customer")
spark.read.table("bronze.products").createOrReplaceTempView("products")
spark.sql("""
    SELECT o.order_id, coalesce(d.customer_key, -1) AS customer_key, o.product_id,
           CAST(o.order_ts AS DATE) AS order_date, o.qty, o.qty * o.unit_price AS amount
    FROM orders AS o
    JOIN dim_customer AS d
      ON d.customer_id = o.customer_id AND o.order_ts >= d.valid_from AND (d.valid_to IS NULL OR o.order_ts < d.valid_to)
""").write.mode("overwrite").saveAsTable("gold.fact_sales")
spark.read.table("gold.fact_sales").createOrReplaceTempView("fact_sales")
spark.sql("""
    SELECT f.order_date, coalesce(d.segment, 'Unknown') AS segment, p.category,
           count(*) AS orders, sum(f.amount) AS amount
    FROM fact_sales AS f
    LEFT JOIN dim_customer AS d ON d.customer_key = f.customer_key
    JOIN products AS p ON p.product_id = f.product_id
    GROUP BY f.order_date, coalesce(d.segment, 'Unknown'), p.category
""").write.mode("overwrite").saveAsTable("gold.sales_by_segment")
