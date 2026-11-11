regions = sorted(r["region"] for r in spark.read.table("bronze.orders").select("region").distinct().collect())

dag = {"activities": [{"name": f"load_{r}", "path": "load_region", "args": {"region": r}} for r in regions]
       + [{"name": "summary", "path": "build_summary", "args": {"regions": ",".join(regions)},
           "dependencies": [f"load_{r}" for r in regions]}]}
results = notebookutils.notebook.runMultiple(dag)

# An exit value always comes back as a string.
log = [(r, int(results[f"load_{r}"]["exitVal"])) for r in regions]
spark.createDataFrame(log, ["region", "rows_loaded"]).write.mode("overwrite").saveAsTable("ops.load_log")
