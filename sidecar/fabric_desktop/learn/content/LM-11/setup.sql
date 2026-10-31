CREATE TABLE customers AS
SELECT * FROM (VALUES
    (1, 'Avery',  'Leeds'),
    (2, 'Blake',  'York'),
    (3, 'Casey',  'Hull'),
    (4, 'Devon',  'Bath')
) AS t(customer_id, name, city);

CREATE TABLE changes AS
SELECT * FROM (VALUES
    (2, 'Blake',  'Derby', 'upsert'),
    (3, 'Casey',  'Hull',  'delete'),
    (5, 'Emery',  'Ely',   'upsert'),
    (6, 'Finley', 'Wells', 'delete')
) AS t(customer_id, name, city, op);
