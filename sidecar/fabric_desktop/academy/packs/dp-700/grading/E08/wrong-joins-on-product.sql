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
JOIN [dbo].[DimStore] AS st ON st.[StoreKey] = s.[ProductKey]
GROUP BY st.[Region];
