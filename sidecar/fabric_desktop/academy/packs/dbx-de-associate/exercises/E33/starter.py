# E33: this job loads bronze.payments into silver.payments. It used to fail on values like "1,250.50",
# so someone changed cast() to try_cast. It hasn't failed since, but finance says revenue dropped by
# more than half. Find out why and fix it. `spark` is ready; run with Run, check with Check my work.

from pyspark.sql import functions as F

payments = spark.read.table("bronze.payments")
silver = payments.withColumn("amount", F.expr("try_cast(amount AS DECIMAL(12,2))"))
silver.write.mode("overwrite").saveAsTable("silver.payments")
