-- E23 grading data the learner never sees: a model name with spaces, upper-case tags with stray
-- spaces, one product with a single tag, and two discontinued ones. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.products AS SELECT * FROM (VALUES
  (10, 'Dyna|Mo 3000 XL', 'POWER , Garden', 12000, 'Y-10', 'active'),
  (11, 'Dyna|Mini',       'garden',          300, 'Y-11', 'discontinued'),
  (12, 'Echo|Bell',       ' Music',          999, 'Y-12', 'active'),
  (13, 'Echo|Horn',       NULL,              450, 'Y-13', 'active'),
  (14, 'Fizz|Pop',        'kids,sale',       150, 'Y-14', 'discontinued')
) AS t(product_id, full_name, tags, price_cents, legacy_code, status);
