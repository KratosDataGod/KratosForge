-- E23 practice data: a product feed with the brand and model in one column, tags as one
-- comma-separated string, prices in cents, a column nobody uses any more, and discontinued products.
-- DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.products AS SELECT * FROM (VALUES
  (1, 'Acme|Rocket 2',  'outdoor, Sale,new', 1999, 'X-01', 'active'),
  (2, 'Acme|Anvil',     'heavy',             4500, 'X-02', 'active'),
  (3, 'Bolt|Drill Pro', 'tools,Sale',        8999, 'X-03', 'discontinued'),
  (4, 'Bolt|Saw',       NULL,                2500, 'X-04', 'active'),
  (5, 'Cog|Gear Set',   'tools, kids ',       750, 'X-05', 'active')
) AS t(product_id, full_name, tags, price_cents, legacy_code, status);
