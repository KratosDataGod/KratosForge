CREATE TABLE products (product VARCHAR, domain VARCHAR, owner VARCHAR);
INSERT INTO products VALUES
    ('revenue_daily', 'finance', 'fin-team'), ('margin', 'finance', 'fin-team'), ('fx_rates', 'finance', 'fin-team'),
    ('orders', 'sales', 'sales-team'), ('customers', 'sales', 'sales-team'), ('stock', 'supply', 'supply-team');
CREATE TABLE dependencies (consumer VARCHAR, source VARCHAR);
INSERT INTO dependencies VALUES
    ('revenue_daily', 'orders'), ('revenue_daily', 'fx_rates'), ('margin', 'revenue_daily'),
    ('margin', 'stock'), ('orders', 'customers');
