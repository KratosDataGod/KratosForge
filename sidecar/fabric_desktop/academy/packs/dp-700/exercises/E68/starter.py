# E68: this notebook merges today's order updates into silver.orders. Today it fails at the MERGE.
# Find out why, fix it here, and run it again. `spark` is ready; check with Check my work.
from delta.tables import DeltaTable

updates = spark.read.table("bronze.order_updates")

(DeltaTable.forName(spark, "silver.orders").alias("t")
    .merge(updates.alias("s"), "t.order_id = s.order_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute())
