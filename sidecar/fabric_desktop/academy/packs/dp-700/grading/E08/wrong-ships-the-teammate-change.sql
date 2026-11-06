-- file: dbo/Tables/DimStore.sql
CREATE TABLE [dbo].[DimStore]
(
    [StoreKey] INT NOT NULL,
    [StoreName] VARCHAR(100) NULL,
    [Region] VARCHAR(20) NULL
);
-- file: dbo/Views/vw_SalesByRegion.sql
CREATE VIEW [dbo].[vw_SalesByRegion]
AS
SELECT st.[Region], SUM(s.[Amount]) AS [Sales]
FROM [dbo].[FactSales] AS s
JOIN [dbo].[DimStore] AS st ON st.[StoreKey] = s.[StoreKey]
GROUP BY st.[Region];
-- file: dbo/Tables/DimProduct.sql
-- A teammate's change, waiting to go in with yours: Category moves to its own table later.
CREATE TABLE [dbo].[DimProduct]
(
    [ProductKey] INT NOT NULL,
    [ProductName] VARCHAR(100) NULL
);
