from pyspark.sql import functions as F

payments = spark.read.table("bronze.payments")
silver = payments.withColumn("amount", F.expr("try_cast(nullif(regexp_replace(amount, '[$]', ''), '') AS DECIMAL(12,2))"))
silver.write.mode("overwrite").saveAsTable("silver.payments")
