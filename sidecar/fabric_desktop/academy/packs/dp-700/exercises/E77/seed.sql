INSERT INTO dbo.DimProduct VALUES (1, 'Trail bike', 'Bikes'), (2, 'Road helmet', 'Helmets'), (3, 'Rear light', 'Lights');
INSERT INTO dbo.FactSales (SaleKey, ProductKey, SaleDate, Amount, Channel) VALUES
    (100, 1, '2026-02-01', 899.00, NULL), (101, 2, '2026-02-03', 60.00, NULL),
    (102, 1, '2026-03-01', 849.00, 'Online'), (103, 2, '2026-03-02', 55.00, 'Store'),
    (104, 3, '2026-03-02', 20.00, 'Online'), (105, 3, '2026-03-04', 18.00, 'Store');
