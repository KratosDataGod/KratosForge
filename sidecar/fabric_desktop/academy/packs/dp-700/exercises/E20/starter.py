# E20: orchestrate the two notebooks in notebooks/ from here. `spark` and `notebookutils` are ready.
#   load_region   (parameter region)   loads one region into silver.orders_<region>; exits with its row count
#   build_summary (parameter regions, comma-separated) builds gold.region_summary from those tables
# Load every region in bronze.orders, then build the summary once all of them have loaded,
# and record each region's row count in ops.load_log (region, rows_loaded as an integer).

rows = notebookutils.notebook.run("load_region", 90, {"region": "east"})
print("east loaded", rows)
