CREATE TABLE contract (column_name VARCHAR, data_type VARCHAR);
INSERT INTO contract VALUES
    ('order_id', 'INTEGER'), ('order_date', 'DATE'), ('amount', 'DECIMAL(12,2)'), ('currency', 'VARCHAR');
CREATE TABLE delivered_orders (order_id INTEGER, order_date VARCHAR, amount DECIMAL(12, 2));
